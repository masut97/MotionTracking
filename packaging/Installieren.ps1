param(
    [string]$Destination,
    [switch]$Headless,
    [switch]$SkipShortcuts,
    [switch]$UiSelfTest
)
$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent $PSScriptRoot
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
    throw 'Dieses Paket benoetigt Windows 10/11 fuer x64 (Intel/AMD).'
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
if (-not $Destination) { $Destination = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'MotionTracking' }

function Write-JsonUtf8([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 12), (New-Object Text.UTF8Encoding($false)))
}

function Get-Sha256([string]$Path) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = $null
    try {
        $stream = [IO.File]::OpenRead($Path)
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        $algorithm.Dispose()
    }
}

function Install-Tracker([string]$TargetRoot) {
    $TargetRoot = [IO.Path]::GetFullPath($TargetRoot.Trim())
    $sourceApp = Join-Path $packageRoot 'App'
    $targetApp = Join-Path $TargetRoot 'App'
    if ($TargetRoot -eq $packageRoot -or $TargetRoot.StartsWith($packageRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or $packageRoot.StartsWith($TargetRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Bitte einen Installationsordner ausserhalb des entpackten Pakets waehlen.'
    }
    if (Test-Path -LiteralPath $targetApp) {
        throw 'Im Zielordner ist bereits eine Anwendung vorhanden. Bitte einen neuen Ordner waehlen, damit bestehende Einstellungen erhalten bleiben.'
    }
    if (Test-Path -LiteralPath $TargetRoot) {
        if (-not (Test-Path -LiteralPath $TargetRoot -PathType Container) -or @(Get-ChildItem -LiteralPath $TargetRoot -Force).Count -gt 0) {
            throw 'Der Zielordner muss neu oder vollstaendig leer sein. Vorhandene Dateien werden nicht ueberschrieben.'
        }
    }
    $manifest = Get-Content -LiteralPath (Join-Path $packageRoot 'PAKET.json') -Raw | ConvertFrom-Json
    foreach ($required in $manifest.required_files) {
        $file = Join-Path $packageRoot $required.path
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Sha256 $file) -ne $required.sha256) {
            throw "Das Paket ist unvollstaendig oder beschaedigt: $($required.path). Bitte das ZIP nochmals vollstaendig entpacken."
        }
    }
    New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null
    $copyLog = Join-Path $TargetRoot 'installation-copy.log'
    $copyArgs = @($packageRoot, $TargetRoot, '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NP', "/LOG:$copyLog")
    & "$env:SystemRoot\System32\robocopy.exe" @copyArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Dateien konnten nicht kopiert werden. Details: $copyLog" }
    $configPath = Join-Path $targetApp 'config.json'
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $config.measurement_directory = Join-Path $TargetRoot 'measurements'
    $config.log_directory = Join-Path $targetApp 'logs'
    $config.PSObject.Properties.Remove('update_directory')
    Write-JsonUtf8 (Join-Path $targetApp 'update_settings.json') @{ update_directory = (Join-Path $TargetRoot 'versions') }
    # Keep the package-selected analysis; only relocate a validated local entry.
    $analysisName = [IO.Path]::GetFileName([string]$config.analysis_script)
    $analysisPath = Join-Path $targetApp ('analysis_scripts\' + $analysisName)
    if (-not $analysisName) {
        $analysisPath = ''
    } elseif (-not (Test-Path -LiteralPath $analysisPath -PathType Leaf)) {
        $analysisPath = Join-Path $targetApp 'analysis_scripts\auto_auswertung.py'
    }
    $config.analysis_script = $analysisPath
    Write-JsonUtf8 $configPath $config
    foreach ($dir in @($config.measurement_directory, $config.log_directory, (Join-Path $TargetRoot 'diagnostics'))) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    if (-not $SkipShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcutFolders = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('Programs')) 'Motion Tracking'))
        foreach ($folder in $shortcutFolders) {
            New-Item -ItemType Directory -Force -Path $folder | Out-Null
            $shortcutPath = Join-Path $folder 'Motion Tracking.lnk'
            $temporaryShortcut = Join-Path $folder ('MotionTracking-install-' + [Guid]::NewGuid().ToString() + '.lnk')
            $shortcut = $shell.CreateShortcut($temporaryShortcut)
            $shortcut.TargetPath = Join-Path $targetApp 'AtracsysLiveTracker_Messskripte.exe'
            $shortcut.Arguments = '--mode hardware'
            $shortcut.WorkingDirectory = $targetApp
            $shortcut.IconLocation = (Join-Path $targetApp 'assets\motion_tracking.ico') + ',0'
            $shortcut.Save()
            if (Test-Path -LiteralPath $shortcutPath) {
                $backupShortcut = Join-Path $folder ('Motion Tracking - Vorherige Version ' + [Guid]::NewGuid().ToString() + '.lnk')
                [IO.File]::Replace($temporaryShortcut, $shortcutPath, $backupShortcut)
            } else {
                [IO.File]::Move($temporaryShortcut, $shortcutPath)
            }
        }
    }
    $report = @{ installed_utc = [DateTime]::UtcNow.ToString('o'); root = $TargetRoot; network_changes = $false; system_installations = $false; version = $manifest.version }
    Write-JsonUtf8 (Join-Path $TargetRoot 'installation-result.json') $report
    return $report
}

if ($Headless) {
    Install-Tracker $Destination | ConvertTo-Json
    exit 0
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Motion Tracking - Offline-Installation'
$form.ClientSize = New-Object Drawing.Size(660, 350)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.Font = New-Object Drawing.Font('Segoe UI', 10)
$heading = New-Object System.Windows.Forms.Label
$heading.Text = 'Motion Tracking - eigenstaendiges Paket'
$heading.Font = New-Object Drawing.Font('Segoe UI', 15, [Drawing.FontStyle]::Bold)
$heading.SetBounds(22, 18, 615, 36)
$form.Controls.Add($heading)
$info = New-Object System.Windows.Forms.Label
$info.Text = "Alle Programmkomponenten sind enthalten. Es wird nichts heruntergeladen.`r`nVorhandene FTK-Installation, Treiber und Netzwerkeinstellungen bleiben erhalten."
$info.SetBounds(24, 66, 610, 60)
$form.Controls.Add($info)
$pathLabel = New-Object System.Windows.Forms.Label
$pathLabel.Text = 'Installationsordner:'
$pathLabel.SetBounds(24, 137, 170, 24)
$form.Controls.Add($pathLabel)
$pathBox = New-Object System.Windows.Forms.TextBox
$pathBox.Text = $Destination
$pathBox.SetBounds(24, 166, 515, 29)
$form.Controls.Add($pathBox)
$browse = New-Object System.Windows.Forms.Button
$browse.Text = 'Auswaehlen'
$browse.SetBounds(545, 165, 95, 31)
$browse.Add_Click({
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    $picker.Description = 'Installationsordner waehlen'
    if ($picker.ShowDialog() -eq 'OK') { $pathBox.Text = Join-Path $picker.SelectedPath 'MotionTracking' }
    $picker.Dispose()
})
$form.Controls.Add($browse)
$independenceNote = New-Object System.Windows.Forms.Label
$independenceNote.Text = "Eigene Bibliotheken, Einstellungen und Messordner.`r`nDie Installation benoetigt keine Administratorrechte."
$independenceNote.SetBounds(24, 213, 615, 54)
$form.Controls.Add($independenceNote)
$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = 'Bereit zur Installation.'
$statusLabel.SetBounds(24, 283, 425, 56)
$form.Controls.Add($statusLabel)
$installButton = New-Object System.Windows.Forms.Button
$installButton.Text = 'Installieren'
$installButton.SetBounds(477, 287, 162, 39)
$form.Controls.Add($installButton)
$installButton.Add_Click({
    try {
        $installButton.Enabled = $false
        $statusLabel.Text = 'Programmdateien werden geprueft und kopiert ...'
        $form.Refresh()
        $result = Install-Tracker $pathBox.Text
        [void][System.Windows.Forms.MessageBox]::Show("Installation abgeschlossen.`r`n`r`nStart ueber die neue Desktopverknuepfung:`r`nMotion Tracking", 'Motion Tracking')
        $form.Close()
    } catch {
        $statusLabel.Text = 'Installation konnte nicht abgeschlossen werden.'
        [void][System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Installation', 'OK', 'Error')
        $installButton.Enabled = $true
    }
})
if ($UiSelfTest) {
    $form.CreateControl()
    $form.DrawToBitmap(($preview = New-Object Drawing.Bitmap($form.Width, $form.Height)), $form.ClientRectangle)
    $preview.Save((Join-Path $packageRoot 'installer-preview.png'))
    $preview.Dispose()
    $form.Dispose()
    @{ default_path = $pathBox.Text; network_changes = $false } | ConvertTo-Json
    exit 0
}
[void]$form.ShowDialog()
$form.Dispose()
