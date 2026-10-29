"""Explicit inactive-installation removal with byte-verified data preservation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

DATA_FOLDERS={'measurements','recordings','results','data','geometries','back_up_geom','calibration','calibrations','step_sources'}
VENDOR_FOLDERS={'_internal','sdk','native','analysis_vendor','assets'}
DATA_SUFFIXES={'.csv','.tsv','.tra','.log','.json','.json5','.ini','.step','.stp','.zip',
               '.html','.png','.jpg','.pdf','.xlsx','.txt','.local'}


def linked(path):
    info=path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0)&0x400)


def research_file(relative):
    parts={p.casefold() for p in relative.parts[:-1]}
    return (bool(parts & DATA_FOLDERS) or relative.name.endswith('.local.json') or
            not parts & VENDOR_FOLDERS and relative.suffix.lower() in DATA_SUFFIXES)


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def remove_old_installation(folder, allowed_parent, preserved, *, running):
    folder,allowed_parent,preserved=map(lambda p:Path(p).absolute(),(folder,allowed_parent,preserved))
    if (folder.parent!=allowed_parent or folder.resolve()!=folder or linked(folder) or
        preserved.resolve().is_relative_to(folder) or folder==preserved.resolve()):
        raise ValueError('Cleanup target must be an explicit direct child outside preserved data')
    if any(Path(p).resolve().is_relative_to(folder) for p in running):
        raise ValueError('Cannot remove a running installation')
    files=[]
    for current,dirs,names in os.walk(folder,followlinks=False):
        for name in [*dirs,*names]:
            path=Path(current)/name
            if linked(path): raise ValueError('Cleanup refuses links or reparse points')
        files.extend(Path(current)/name for name in names)
    # An arbitrary directory must never become a recursive deletion target.
    if not any(p.name=='build.json' and p.parent.name=='sync_extension' for p in files):
        raise ValueError('No verified application layout in cleanup target')
    destination=preserved/folder.name
    if destination.exists(): raise FileExistsError('Preserved data already exists; no merge or deletion')
    destination.mkdir(parents=True)
    record=destination/'preservation-manifest.json'
    report={'original':str(folder),'preserved':str(destination),'status':'preserving','files':[]}
    def save():
        temp=record.with_suffix('.tmp');temp.write_text(json.dumps(report,indent=2),encoding='utf-8');temp.replace(record)
    save()
    selected=[p for p in files if research_file(p.relative_to(folder))]
    # Copy and verify all protected contents before removing any old files.
    for path in selected:
        relative=path.relative_to(folder);target=destination/'files'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)
        sha=digest(path)
        if digest(target)!=sha: raise OSError('Protected file verification failed')
        report['files'].append({'path':relative.as_posix(),'sha256':sha})
    report['status']='verified';save()
    # Recheck the resolved absolute boundary immediately before recursion.
    if folder.resolve()!=folder or folder.parent!=allowed_parent or linked(folder):
        raise ValueError('Cleanup path changed')
    expected={item['path']:item['sha256'] for item in report['files']}
    actual={p.relative_to(folder).as_posix():digest(p) for p in folder.rglob('*')
            if p.is_file() and research_file(p.relative_to(folder))}
    if actual!=expected: raise ValueError('Protected files changed during cleanup; installation retained')
    def retry_readonly(operation,path,error):
        checked=Path(path).resolve()
        if not checked.is_relative_to(folder) or linked(Path(path)): raise error[1]
        os.chmod(path,stat.S_IWRITE)
        operation(path)
    shutil.rmtree(folder,onerror=retry_readonly)
    report['status']='removed';save()
    return report
