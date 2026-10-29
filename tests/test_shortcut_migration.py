"""Exercise real Windows shortcuts in an isolated synthetic desktop only."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from atracsys_sync.shortcuts import switch_shortcut

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows shortcuts required')


def powershell(tmp_path, code, *args):
    script = tmp_path / 'test-link.ps1'
    script.write_text(code, encoding='utf-8-sig')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                             'Bypass', '-File', str(script), *map(str, args)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def make_link(tmp_path, path, target):
    powershell(tmp_path, '''param($Path,$Target)
$s=(New-Object -ComObject WScript.Shell).CreateShortcut($Path)
$s.TargetPath=$Target
$s.Save()
''', path, target)


def test_legacy_shortcut_is_backed_up_and_migrated_without_touching_other_files(tmp_path):
    app = tmp_path / 'new version/App'; app.mkdir(parents=True)
    desktop = tmp_path / 'fake desktop'; desktop.mkdir()
    legacy = desktop / 'Atracsys Tracking.lnk'
    make_link(tmp_path, legacy, tmp_path / 'old/App/AtracsysLiveTracker_Messskripte.exe')
    before = legacy.read_bytes()
    untouched = desktop / 'measurement.csv'; untouched.write_bytes(b'keep data')
    switch_shortcut(app, desktop=desktop)
    assert not legacy.exists()
    assert (app.parent / 'previous-desktop-shortcut.lnk').read_bytes() == before
    target = powershell(tmp_path, '''param($Path)
$s=(New-Object -ComObject WScript.Shell).CreateShortcut($Path)
@{target=$s.TargetPath;arguments=$s.Arguments} | ConvertTo-Json
''', desktop / 'Motion Tracking.lnk')
    info = json.loads(target)
    assert Path(info['target']) == app / 'AtracsysLiveTracker_Messskripte.exe'
    assert info['arguments'] == '--mode hardware'
    assert untouched.read_bytes() == b'keep data'
    first_backup = (app.parent / 'previous-desktop-shortcut.lnk').read_bytes()
    switch_shortcut(app, desktop=desktop)
    assert (app.parent / 'previous-desktop-shortcut.lnk').read_bytes() == first_backup


def test_foreign_motion_tracking_shortcut_is_never_replaced(tmp_path):
    app = tmp_path / 'App'; app.mkdir()
    desktop = tmp_path / 'desktop'; desktop.mkdir()
    link = desktop / 'Motion Tracking.lnk'
    make_link(tmp_path, link, tmp_path / 'OtherProgram.exe')
    before = link.read_bytes()
    with pytest.raises(RuntimeError):
        switch_shortcut(app, desktop=desktop)
    assert link.read_bytes() == before


def test_foreign_legacy_shortcut_is_preserved(tmp_path):
    app = tmp_path / 'App'; app.mkdir()
    desktop = tmp_path / 'desktop'; desktop.mkdir()
    legacy = desktop / 'Atracsys Tracking.lnk'
    make_link(tmp_path, legacy, tmp_path / 'OtherProgram.exe')
    before = legacy.read_bytes()
    switch_shortcut(app, desktop=desktop)
    assert legacy.read_bytes() == before
    assert (desktop / 'Motion Tracking.lnk').exists()
