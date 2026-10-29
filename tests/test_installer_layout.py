import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.name!='nt',reason='Windows installer required')
def test_offline_installer_uses_shared_version_store_without_root_app_copy(tmp_path):
    package=tmp_path/'package';(package/'Installer').mkdir(parents=True)
    shutil.copy2(Path(__file__).resolve().parents[1]/'packaging/Installieren.ps1',package/'Installer/Installieren.ps1')
    (package/'App').mkdir()
    config=package/'App/config.json'
    config.write_text(json.dumps({'measurement_directory':'old','log_directory':'old','analysis_script':''}))
    (package/'PAKET.json').write_text(json.dumps({'version':'a'*40,'required_files':[
        {'path':'App/config.json','sha256':hashlib.sha256(config.read_bytes()).hexdigest()}]}))
    destination=tmp_path/'installed'
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(package/'Installer/Installieren.ps1'),'-Headless','-SkipShortcuts','-Destination',str(destination)],
        capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stderr
    app=destination/'versions'/('a'*12)/'App'
    assert app.is_dir() and not (destination/'App').exists()
    settings=json.loads((app/'update_settings.json').read_text())
    assert settings['update_directory']==str(destination/'versions')
    assert json.loads((app/'config.json').read_text())['measurement_directory']==str(destination/'measurements')
