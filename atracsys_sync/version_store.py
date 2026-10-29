"""Version identity, duplicate checks and reversible managed-version archival."""
import hashlib
import json
from pathlib import Path
import re
import subprocess


def local_state(app):
    app = Path(app)
    config = json.loads((app/'config.json').read_text(encoding='utf-8-sig'))
    geometry = Path(config.get('geometry_directory') or 'geometries')
    if not geometry.is_absolute(): geometry = app/geometry
    paths = [(name,app/name) for name in ('config.json','measurement_scripts.json','update_settings.json')]
    for folder in ('analysis_scripts','analysis_vendor','measurement_scripts'):
        root=app/folder
        paths.extend((p.relative_to(app).as_posix(),p) for p in root.rglob('*')
                     if p.is_file() and (p.suffix=='.json' or folder=='measurement_scripts' and p.suffix=='.py'))
    paths.extend(('geometry/'+p.relative_to(geometry).as_posix(),p) for p in geometry.rglob('*') if p.is_file())
    digest=hashlib.sha256()
    for name,path in sorted(paths):
        digest.update(name.encode()); digest.update(b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b'missing')
    return digest.hexdigest()


def check_prepared(app, current, revision, archive_hash, payload):
    """Never overwrite an existing version or silently reuse stale local settings."""
    app,current=Path(app),Path(current)
    report=json.loads((app/'sync_extension/update.json').read_text())
    if (report.get('revision')!=revision or report.get('archive_sha256')!=archive_hash or
        report.get('origin_state')!=local_state(current) or report.get('prepared_state')!=local_state(app)):
        raise ValueError(f'Version bereits vorhanden, aber lokale Einstellungen haben sich geÃ¤ndert: {app}. Keine weitere Kopie erstellt.')
    build=json.loads((app/'sync_extension/build.json').read_text())
    if build.get('source_revision')!=revision: raise ValueError('Existing version identity mismatch')
    if not {'AtracsysLiveTracker.exe','AtracsysLiveTracker_Messskripte.exe','native/bin/atracsys_bridge.exe'}<=build.get('files',{}).keys():
        raise ValueError('Existing runtime manifest incomplete')
    for name,expected in build.get('files',{}).items():
        relative=Path(name)
        path=app/relative
        if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(app.resolve()):
            raise ValueError('Invalid existing version manifest')
        if (relative.name in {'config.json','measurement_scripts.json','update_settings.json'} or
            relative.name.endswith('.local.json') or set(relative.parts)&{'geometries','back_up_geom','calibrations','calibration','logs','measurements','diagnostics'}): continue
        with path.open('rb') as stream: actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=expected:
            raise ValueError(f'Vorhandene Version wurde verÃ¤ndert: {path}. Keine weitere Kopie erstellt.')
    for name,data in payload.items():
        if (app/name).read_bytes()!=data: raise ValueError('Existing update source mismatch')


def process_paths():
    code="Get-CimInstance Win32_Process | Where-Object {$_.Name -in @('AtracsysLiveTracker_Messskripte.exe','atracsys_bridge.exe')} | Select-Object -ExpandProperty ExecutablePath | ConvertTo-Json -Compress"
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],
                          capture_output=True,text=True,timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: raise OSError('Cannot check running applications before archival')
    paths=json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(paths,str): paths=[paths]
    if any(not p for p in paths): raise OSError('A running application path could not be checked')
    return [Path(p).resolve() for p in paths]


def retire_versions(active, *, running=None):
    """Remove recognised inactive versions, preserving local research files first."""
    active=Path(active).resolve()
    store=active.parent.parent
    if store.name!='versions': return []
    running=process_paths() if running is None else [Path(p).resolve() for p in running]
    config=json.loads((active/'config.json').read_text(encoding='utf-8-sig'))
    protected=[]
    for key in ('measurement_directory','geometry_directory'):
        if config.get(key):
            path=Path(config[key]);protected.append((path if path.is_absolute() else active/path).resolve())
    entries=[]
    from .installation_cleanup import remove_old_installation
    for folder in sorted(store.iterdir()):
        if not folder.is_dir() or folder.is_symlink() or not re.fullmatch('[0-9a-f]{12}',folder.name): continue
        folder=folder.resolve()
        if folder.parent!=store or folder==active.parent or any(p.is_relative_to(folder) for p in [*running,*protected]): continue
        manifest=folder/'App/sync_extension/build.json'
        if not manifest.is_file(): continue
        info=json.loads(manifest.read_text())
        if info.get('project')!='MotionTracking': continue
        revision=info.get('source_revision','')
        if not re.fullmatch('[0-9a-f]{40}',revision) or revision[:12]!=folder.name: continue
        entries.append(remove_old_installation(folder,store,store.parent/'PreservedData',running=running))
    return entries


def retire_in_background(active):
    import threading
    def run():
        try: retire_versions(active)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Alte Version bleibt erhalten; Bereinigung nicht abgeschlossen')
    threading.Thread(target=run,name='version-cleanup',daemon=True).start()
