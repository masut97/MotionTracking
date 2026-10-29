[CmdletBinding()]
param(
    [ValidateSet('Start','Stop','Finalize','Plan','Prepare','Abort')][string]$Action,
    [string]$Root = 'C:\Users\Zwick Biaxial\Documents\Spine_Tester_Red',
    [string]$ControlPath = 'HKCU:\Software\AtracsysTracking\Control',
    [int]$FrequencyHz = 0,
    [int]$TimeoutSeconds = 120,
    [string]$ExportFile = ''
)
$ErrorActionPreference = 'Stop'
$exports = Join-Path $Root 'Exports'
$nameFile = Join-Path $Root 'ExperimentName.txt'
$startRequested = $false
function Write-Value($Name,$Value,$Type='String') {
    New-ItemProperty -LiteralPath $ControlPath -Name $Name -Value $Value -PropertyType $Type -Force | Out-Null
}
function Read-Control { Get-ItemProperty -LiteralPath $ControlPath -ErrorAction Stop }
function Wait-Control([string[]]$States,[int]$Seconds) {
    $deadline=[DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $s=Read-Control
        if ($s.RecordingState -eq 'Fehler') { throw "Atracsys: $($s.LastError)" }
        if ($s.RecordingState -in $States) { return $s }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timeout waiting for Atracsys: $($States -join ', '). Last state: $($s.RecordingState)"
}
function Get-Plan {
    $name=(Get-Content -LiteralPath $nameFile -TotalCount 1 -ErrorAction Stop).Trim()
    if (-not $name -or $name -match '[<>:"/\\|?*]' -or $name.EndsWith('.') -or $name -in '.','..') { throw 'Invalid ExperimentName.txt filename.' }
    $name=[IO.Path]::GetFileNameWithoutExtension($name+'.tra')
    # The inherited recorder sanitizes parentheses; use an already-safe basename.
    $name=[regex]::Replace($name,'[^A-Za-z0-9_-]','_').Trim('_','-')
    $name=[regex]::Replace($name,'(?:_rep_\d+)+$','',[Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $match=[regex]::Match($name,'(?:^|_)(FE|LB|AR|AS|LS|AC)(?:_|$)',[Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $match.Success) { throw 'ExperimentName.txt must contain _FE_, _LB_, _AR_, _AS_, _LS_ or _AC_.' }
    $base=$name
    $index=0
    while (@(Get-ChildItem -LiteralPath $exports -File | Where-Object { $_.Name.StartsWith($name+'.',[StringComparison]::OrdinalIgnoreCase) }).Count) {
        $index++
        $name=$base+'_rep_'+$index.ToString('000')
    }
    [pscustomobject]@{Name=$name;Mode=$match.Groups[1].Value.ToUpperInvariant();Directory=$exports;TRA=(Join-Path $exports ($name+'.tra'));CSV=(Join-Path $exports ($name+'.csv'))}
}
try {
    if ($Action -eq 'Abort') {
        # End-event hook: request a normal recorder stop without blocking testXpert
        # or requiring a session/TRA/ROM report. Repeated idle calls are harmless.
        if (Test-Path -LiteralPath $ControlPath) {
            $s = Read-Control
            if ($s.RecordingState -in @('Aufnahme','Vorbereitung')) {
                Write-Value 'StartRecord' 0 'DWord'
                Write-Value 'StopRecord' 1 'DWord'
            }
        }
        exit 0
    }
    if ($Action -eq 'Plan') { Get-Plan | ConvertTo-Json; exit 0 }
    if ($Action -eq 'Prepare') {
        $launcher = 'C:\Users\Public\Documents\AtracsysTracking\Start-Atracsys.ps1'
        $s = if (Test-Path -LiteralPath $ControlPath) { Read-Control } else { $null }
        $fresh = $s -and $s.AppReady -eq 1 -and ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()-[long]$s.HeartbeatUnixMs -lt 5000)
        if (-not $fresh) {
            if (-not (Test-Path -LiteralPath $launcher)) { throw 'Shared Atracsys launcher not found.' }
            $launch = Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$launcher+'"')) -WindowStyle Hidden -Wait -PassThru
            if ($launch.ExitCode -ne 0) { throw 'Atracsys app launcher failed.' }
        }
        $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        do {
            if (Test-Path -LiteralPath $ControlPath) {
                $s = Read-Control
                $fresh = $s.AppReady -eq 1 -and ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()-[long]$s.HeartbeatUnixMs -lt 5000)
                if ($fresh -and $s.RecordingState -in @('Aufnahme','Vorbereitung')) { throw 'An existing recording is active/preparing. Preparation did not stop or change it.' }
                if ($fresh -and $s.CameraReady -eq 1) { exit 0 }
            }
            Start-Sleep -Milliseconds 200
        } while ([DateTime]::UtcNow -lt $deadline)
        throw 'Camera not ready within timeout. Check camera connection and the current Atracsys app version.'
    }
    if (-not (Test-Path -LiteralPath $ControlPath)) { throw 'Motion Tracking is not ready. Open Motion Tracking under the same Windows account as testXpert.' }
    if ($Action -eq 'Start') {
        $s=Read-Control
        $now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        if ($s.AppReady -ne 1 -or $now-[long]$s.HeartbeatUnixMs -gt 5000) { throw 'Atracsys heartbeat is missing/stale. Open the app under the same Windows account as testXpert.' }
        if ($s.RecordingState -in @('Aufnahme','Vorbereitung')) { throw 'Atracsys is already recording or preparing.' }
        $selectedFrequency=if($FrequencyHz -gt 0){[string]$FrequencyHz}else{[string]$s.RecordingFrequencyHz}
        if($selectedFrequency -notmatch '^\d+$' -or [int]$selectedFrequency -lt 1 -or [int]$selectedFrequency -gt 10000){throw 'RecordingFrequencyHz must be an integer frequency in Hz in the configured control registry key.'}
        $plan=Get-Plan
        $started=[DateTime]::UtcNow
        $session=[pscustomobject]@{Name=$plan.Name;Mode=$plan.Mode;TRA=$plan.TRA;CSV=$plan.CSV;StartedUtc=$started.ToString('o');Active=($plan.Name -match '(?i)(^|_)a(_|$)');Pressure=$(if($plan.Name -match '(?i)(^|_)wp(_|$)'){'wp'}else{'p'})}
        $archive=Join-Path $Root ('ZwickCoupling\history\'+$started.ToString('yyyyMMdd_HHmmss_fffffff'))
        New-Item -ItemType Directory -Path $archive -Force | Out-Null
        Copy-Item -LiteralPath $nameFile -Destination (Join-Path $archive 'ExperimentName-before.txt')
        # Preserve previous feedback before removing it from testXpert's live paths.
        if ($session.Active) {
            foreach ($suffix in @('UpROM','DnROM','ZeroPoint')) {
                $path=Join-Path $exports ($session.Mode+'_'+$session.Pressure+'_'+$suffix+'.txt')
                if(Test-Path -LiteralPath $path){Move-Item -LiteralPath $path -Destination $archive}
            }
            if($session.Mode -eq 'AR'){
                $path=Join-Path $exports ($session.Pressure+'_Vert_TransSP.txt')
                if(Test-Path -LiteralPath $path){Move-Item -LiteralPath $path -Destination $archive}
            }
        }
        [IO.File]::WriteAllText($nameFile,$plan.Name+[Environment]::NewLine,(New-Object Text.UTF8Encoding($false)))
        $session | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root 'ZwickCoupling\session.json') -Encoding UTF8
        Write-Value 'StartRecord' 0 'DWord'
        Write-Value 'StopRecord' 0 'DWord'
        $resetToken = [guid]::NewGuid().ToString('N')
        if ($s.StartResetSupported -eq 1) { Write-Value 'StartResetToken' $resetToken }
        Write-Value 'RecordingName' $plan.Name
        Write-Value 'RecordingDirectory' $exports
        if($FrequencyHz -gt 0){Write-Value 'RecordingFrequencyHz' $selectedFrequency}
        Write-Value 'ZwickFile' $plan.TRA
        Write-Value 'Movement' $plan.Mode
        if ($s.StartResetSupported -eq 1) {
            $resetDeadline = [DateTime]::UtcNow.AddSeconds(5)
            do {
                if ((Read-Control).StartResetAck -eq $resetToken) { break }
                if ([DateTime]::UtcNow -ge $resetDeadline) { throw 'Atracsys did not acknowledge start reset. No recording requested.' }
                Start-Sleep -Milliseconds 20
            } while ($true)
        } else {
            # Compatibility with app versions without explicit reset acknowledgement.
            Start-Sleep -Milliseconds 600
        }
        Write-Value 'StartedAtUnixMs' ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) 'QWord'
        Write-Value 'CouplingState' 'Preparing'
        $startRequested = $true
        $session | Add-Member -NotePropertyName StartRequestUtc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
        Write-Value 'StartRecord' 1 'DWord'
        $null=Wait-Control @('Aufnahme') 45
        $session | Add-Member -NotePropertyName StartAcknowledgedUtc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
        $session | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root 'ZwickCoupling\session.json') -Encoding UTF8
        Write-Value 'CouplingState' 'Recording'
        exit 0
    }
    if ($Action -eq 'Stop') {
        $session=Get-Content -LiteralPath (Join-Path $Root 'ZwickCoupling\session.json') -Raw | ConvertFrom-Json
        $session | Add-Member -NotePropertyName StopRequestUtc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
        Write-Value 'StopRecord' 1 'DWord'
        $s=Wait-Control @('Gespeichert','Gestoppt') 45
        $session | Add-Member -NotePropertyName StopAcknowledgedUtc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
        $session | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root 'ZwickCoupling\session.json') -Encoding UTF8
        Write-Value 'CouplingState' 'AwaitingExport'
        exit 0
    }
    $session=Get-Content -LiteralPath (Join-Path $Root 'ZwickCoupling\session.json') -Raw | ConvertFrom-Json
    if ($ExportFile -and [IO.Path]::GetFullPath($ExportFile) -ne [IO.Path]::GetFullPath($session.TRA)) { throw 'Machine export filename differs from the started session. Do not use ROM feedback.' }
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $reportPath=Join-Path $exports ($session.Name+'_ROM_auswertung\'+$session.Name+'_'+$session.Mode+'_ROM_report.json')
    do {
        if(Test-Path -LiteralPath $reportPath){
            $report=Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
            if($report.input -ne $session.CSV){throw "Camera filename mismatch. Expected: $($session.CSV); report: $($report.input)"}
            if(-not $report.zwick -or $report.camera_only){throw ('Machine TRA could not be evaluated: '+($report.warnings -join '; '))}
            if($report.zwick.source -ne $session.TRA){throw "Machine filename mismatch. Expected: $($session.TRA); report: $($report.zwick.source)"}
            if($session.Active){
                if($session.Mode -eq 'AR'){
                    # MATLAB's Vert_TransSP is axial displacement, not rotation.
                    $rows=Get-Content -LiteralPath $session.TRA -Encoding Default | ConvertFrom-Csv -Delimiter ';'
                    $axial=@(foreach($row in $rows){
                        $value=0.0
                        $text=([string]$row.'Axial Workaround').Trim().Replace(',','.')
                        if([double]::TryParse($text,[Globalization.NumberStyles]::Float,[Globalization.CultureInfo]::InvariantCulture,[ref]$value) -and -not [double]::IsNaN($value) -and -not [double]::IsInfinity($value)){$value}
                    })
                    if($axial.Count -lt 2){throw 'AR axial feedback cannot be calculated: Axial Workaround column missing or invalid.'}
                    $mean=($axial | Measure-Object -Average).Average
                    [IO.File]::WriteAllText((Join-Path $exports ($session.Pressure+'_Vert_TransSP.txt')),$mean.ToString('G17',[Globalization.CultureInfo]::InvariantCulture))
                }
                $required=@('UpROM','DnROM','ZeroPoint') | ForEach-Object {Join-Path $exports ($session.Mode+'_'+$session.Pressure+'_'+$_+'.txt')}
                if($session.Mode -eq 'AR'){$required+=Join-Path $exports ($session.Pressure+'_Vert_TransSP.txt')}
                foreach($path in $required){
                    if(-not(Test-Path -LiteralPath $path)){throw "Missing ROM feedback: $path"}
                    if((Get-Item -LiteralPath $path).LastWriteTimeUtc -lt [DateTime]::Parse($session.StartedUtc).ToUniversalTime()){throw "Stale ROM feedback: $path"}
                    $number=0.0
                    if(-not [double]::TryParse((Get-Content -LiteralPath $path -Raw).Trim(),[Globalization.NumberStyles]::Float,[Globalization.CultureInfo]::InvariantCulture,[ref]$number) -or [double]::IsNaN($number) -or [double]::IsInfinity($number)){throw "Invalid ROM feedback: $path"}
                }
            }
            Write-Value 'AnalysisReport' $reportPath
            Write-Value 'AnalysisQuality' ([string]$report.quality.status)
            # Quality findings are displayed in the plot/report, not command errors.
            # Keep the original quality status; successful transfer is not approval.
            Write-Value 'CouplingError' ''
            Write-Value 'CouplingState' 'Complete'
            # Data completion is not a statement of biomechanical test approval.
            exit 0
        }
        Start-Sleep -Milliseconds 200
    }while([DateTime]::UtcNow -lt $deadline)
    throw "No completed matching ROM analysis. Check TRA export, all five markers and app analysis status: $reportPath"
}catch{
    if(Test-Path -LiteralPath $ControlPath){
        if($startRequested){Write-Value 'StopRecord' 1 'DWord'}
        Write-Value 'CouplingState' 'Error'
        Write-Value 'CouplingError' $_.Exception.Message
    }
    Write-Error $_.Exception.Message
    exit 1
}
