"""Atomic project-shortcut migration, independently testable without Qt/devices."""
from pathlib import Path
import subprocess
import tempfile

SCRIPT = r'''param([string]$Target,[string]$Backup,[string]$Desktop)
$ErrorActionPreference='Stop'
function Get-Sha256([string]$Path){
    $algorithm=[Security.Cryptography.SHA256]::Create()
    $stream=$null
    try{
        $stream=[IO.File]::OpenRead($Path)
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant()
    }finally{
        if($null -ne $stream){$stream.Dispose()}
        $algorithm.Dispose()
    }
}
if(-not $Desktop){$Desktop=[Environment]::GetFolderPath('Desktop')}
$link=Join-Path $Desktop 'Motion Tracking.lnk'
$legacy=Join-Path $Desktop 'Atracsys Tracking.lnk'
$shell=New-Object -ComObject WScript.Shell
function Is-ProjectLink([string]$Path){
    return [IO.Path]::GetFileName($shell.CreateShortcut($Path).TargetPath) -eq 'AtracsysLiveTracker_Messskripte.exe'
}
$source=$null
if(Test-Path -LiteralPath $link){
    if(-not (Is-ProjectLink $link)){throw 'Motion Tracking shortcut belongs to another application'}
    $source=$link
}elseif((Test-Path -LiteralPath $legacy) -and (Is-ProjectLink $legacy)){$source=$legacy}
if($source){
    if(Test-Path -LiteralPath $Backup){$Backup=$Backup+'.'+[Guid]::NewGuid().ToString()+'.lnk'}
    Copy-Item -LiteralPath $source -Destination $Backup
    if((Get-Sha256 $source) -ne (Get-Sha256 $Backup)){throw 'Shortcut backup verification failed'}
}
$temporary=Join-Path $PSScriptRoot 'new.lnk'
$s=$shell.CreateShortcut($temporary)
$s.TargetPath=$Target
$s.WorkingDirectory=Split-Path $Target
$s.Arguments='--mode hardware'
$s.IconLocation=(Join-Path $s.WorkingDirectory 'assets\motion_tracking.ico')+',0'
$s.Save()
if($source -eq $link){[IO.File]::Replace($temporary,$link,(Join-Path $PSScriptRoot 'old.lnk'))}
else{[IO.File]::Move($temporary,$link)}
if($source -eq $legacy){Remove-Item -LiteralPath $legacy}
'''


def switch_shortcut(app, *, desktop=None):
    app = Path(app).resolve()
    with tempfile.TemporaryDirectory(prefix='motion-tracking-shortcut-') as temporary:
        script = Path(temporary) / 'shortcut.ps1'
        script.write_text(SCRIPT, encoding='utf-8-sig')
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                   '-File', str(script), '-Target', str(app / 'AtracsysLiveTracker_Messskripte.exe'),
                   '-Backup', str(app.parent / 'previous-desktop-shortcut.lnk')]
        if desktop is not None:
            command.extend(['-Desktop', str(desktop)])
        result = subprocess.run(command, capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(f'Neue Version ist geprüft, aber die Verknüpfung konnte nicht umgestellt werden: {app}')
