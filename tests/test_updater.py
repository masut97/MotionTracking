import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from atracsys_sync import updater


REVISION = "a" * 40


def test_updater_settings_use_separate_file_and_resolve_relative_directory(tmp_path):
    app = tmp_path / "App"
    app.mkdir()
    assert updater.load_update_settings(app) == {"update_directory": str(tmp_path / "versions")}
    (app / "update_settings.json").write_text(json.dumps({"update_directory": "../updates"}))
    assert updater.load_update_settings(app) == {"update_directory": str(tmp_path / "updates")}
    (app / "update_settings.json").write_text("[]")
    with pytest.raises(ValueError, match="settings"):
        updater.load_update_settings(app)


def archive(tmp_path, files=None, extra=None, wrong_source_hash=False):
    files = files or {"atracsys_sync/bootstrap.py": b"VALUE = 'updated'\n", "atracsys_sync/integration.py": b"VALUE = 2\n"}
    manifest = {"format_version": 1, "revision": REVISION,
                "compatible_base_sha256": [hashlib.sha256(b"licensed base").hexdigest()],
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    if wrong_source_hash:
        manifest["files"][next(iter(files))] = "0" * 64
    path = tmp_path / "update.zip"
    with zipfile.ZipFile(path, "w") as target:
        target.writestr(updater.MANIFEST, json.dumps(manifest))
        for name, data in files.items():
            target.writestr(name, data)
        if extra:
            target.writestr(*extra)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def runtime(tmp_path):
    app = tmp_path / "current" / "App"
    for name, data in {
        "AtracsysLiveTracker.exe": b"licensed base", "AtracsysLiveTracker_Messskripte.exe": b"local exe",
        "sdk/local.dll": b"local sdk", "native/bin/atracsys_bridge.exe": b"local bridge",
        "geometries/299.ini": b"local calibration", "measurement_scripts/custom.py": b"custom=True",
        "sync_extension/atracsys_sync/bootstrap.py": b"VALUE='old'", "sync_extension/keep.txt": b"local",
        "logs/old.log": b"history", "measurements/unique.csv": b"original data",
    }.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (app / "config.json").write_text(json.dumps({"sdk_root": "sdk", "native_bridge_path": "native/bin/atracsys_bridge.exe",
        "geometry_directory": "geometries", "analysis_script": "", "measurement_directory": "measurements", "recording_frequency_hz": 37}))
    return app


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_repeat_update_reuses_verified_version_without_copying_again(tmp_path,monkeypatch):
    app=runtime(tmp_path);path,digest=archive(tmp_path);parent=tmp_path/'versions'
    installed=updater.install_update(app,path,parent,digest,REVISION)
    def fail(*args,**kwargs):pytest.fail('duplicate runtime copy')
    monkeypatch.setattr(updater,'_copy_runtime',fail)
    assert updater.install_update(app,path,parent,digest,REVISION)==installed
    assert list(parent.iterdir())==[installed.parent]


def test_existing_update_with_changed_local_settings_is_not_duplicated(tmp_path):
    app=runtime(tmp_path);path,digest=archive(tmp_path);parent=tmp_path/'versions'
    installed=updater.install_update(app,path,parent,digest,REVISION)
    config=json.loads((app/'config.json').read_text());config['recording_frequency_hz']=123
    (app/'config.json').write_text(json.dumps(config))
    with pytest.raises(ValueError,match='bereits vorhanden'):
        updater.install_update(app,path,parent,digest,REVISION)
    assert list(parent.iterdir())==[installed.parent]


def test_existing_update_with_corrupted_runtime_is_not_reused(tmp_path):
    app=runtime(tmp_path);path,digest=archive(tmp_path);parent=tmp_path/'versions'
    installed=updater.install_update(app,path,parent,digest,REVISION)
    (installed/'sdk/local.dll').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='Vorhandene Version'):
        updater.install_update(app,path,parent,digest,REVISION)
    assert list(parent.iterdir())==[installed.parent]


def test_update_preserves_local_calibration_settings_and_old_installation(tmp_path):
    app = runtime(tmp_path)
    before = tree_bytes(app)
    path, digest = archive(tmp_path)
    updated = updater.install_update(app, path, tmp_path / "versions", digest, REVISION)
    assert tree_bytes(app) == before
    assert (updated / "geometries/299.ini").read_bytes() == b"local calibration"
    assert (updated / "measurement_scripts/custom.py").read_bytes() == b"custom=True"
    assert (updated / "sdk/local.dll").read_bytes() == b"local sdk"
    assert not (updated / "measurements").exists()
    cfg = json.loads((updated / "config.json").read_text())
    assert cfg["recording_frequency_hz"] == 37
    assert cfg["analysis_script"] == ""
    assert cfg["measurement_directory"] == str(app / "measurements")
    assert cfg["sdk_root"] == "sdk"
    assert b"updated" in (updated / "sync_extension/atracsys_sync/bootstrap.py").read_bytes()


@pytest.mark.parametrize("separate_settings", [False, True])
def test_update_removes_legacy_setting_from_strict_base_config(tmp_path, separate_settings):
    app = runtime(tmp_path)
    config_path = app / "config.json"
    config = json.loads(config_path.read_text())
    legacy = str(tmp_path / "legacy-versions")
    config["update_directory"] = legacy
    config_path.write_text(json.dumps(config))
    expected = str(tmp_path / "selected-versions") if separate_settings else legacy
    if separate_settings:
        (app / "update_settings.json").write_text(json.dumps({"update_directory": expected}))
    before = tree_bytes(app)
    path, digest = archive(tmp_path)
    def check_before_publish(stage):
        assert "update_directory" not in json.loads((stage / "config.json").read_text())
        assert updater.load_update_settings(stage)["update_directory"] == expected
    updated = updater.install_update(app, path, tmp_path / "releases", digest, REVISION,
                                     verify_callback=check_before_publish)
    assert "update_directory" not in json.loads((updated / "config.json").read_text())
    assert updater.load_update_settings(updated)["update_directory"] == expected
    assert tree_bytes(app) == before


def test_verification_failure_never_publishes_or_changes_current_runtime(tmp_path):
    app = runtime(tmp_path)
    before = tree_bytes(app)
    path, digest = archive(tmp_path)
    parent = tmp_path / "versions"
    with pytest.raises(RuntimeError, match="verification"):
        updater.install_update(app, path, parent, digest, REVISION, verify_callback=lambda stage: {"passed": False})
    assert tree_bytes(app) == before
    assert not list(parent.iterdir())


@pytest.mark.parametrize("extra", [("../escape.py", b"x=1"), ("analysis_scripts/undeclared.py", b"x=1"), ("ATRACSYS_SYNC/bootstrap.py", b"x=1")])
def test_malicious_or_undeclared_archive_entry_is_rejected(tmp_path, extra):
    path, digest = archive(tmp_path, extra=extra)
    with pytest.raises(ValueError):
        updater.validate_archive(path, digest, REVISION)
    assert not (tmp_path.parent / "escape.py").exists()


def test_archive_and_each_source_checksum_are_independently_required(tmp_path):
    path, digest = archive(tmp_path, wrong_source_hash=True)
    with pytest.raises(ValueError, match="checksum"):
        updater.validate_archive(path, "0" * 64, REVISION)
    with pytest.raises(ValueError, match="Source hash"):
        updater.validate_archive(path, digest, REVISION)


def test_external_sdk_and_destination_inside_running_app_are_rejected(tmp_path):
    app = runtime(tmp_path)
    path, digest = archive(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        updater.install_update(app, path, app / "versions", digest, REVISION)
    config = json.loads((app / "config.json").read_text())
    config["sdk_root"] = str(tmp_path)
    (app / "config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="SDK"):
        updater.install_update(app, path, tmp_path / "versions", digest, REVISION)
    assert not (tmp_path / "versions").exists()


def test_foreign_selected_analysis_is_not_carried_to_new_installation(tmp_path):
    app = runtime(tmp_path)
    path, digest = archive(tmp_path)
    config = json.loads((app / "config.json").read_text())
    foreign = tmp_path / "foreign.py"
    foreign.write_text("raise RuntimeError('must never execute')")
    config["analysis_script"] = str(foreign)
    (app / "config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="[Aa]nalysis"):
        updater.install_update(app, path, tmp_path / "versions", digest, REVISION)
    assert not (tmp_path / "versions").exists()


def test_absolute_owned_sdk_and_bridge_keep_effective_custom_layout(tmp_path):
    app = runtime(tmp_path)
    (app / "sdk").rename(app / "licensed_sdk")
    (app / "native").rename(app / "local_bridge")
    config = json.loads((app / "config.json").read_text())
    config.update(sdk_root=str(app / "licensed_sdk"), native_bridge_path=str(app / "local_bridge/bin/atracsys_bridge.exe"))
    (app / "config.json").write_text(json.dumps(config))
    path, digest = archive(tmp_path)
    updated = updater.install_update(app, path, tmp_path / "versions", digest, REVISION)
    cfg = json.loads((updated / "config.json").read_text())
    assert cfg["sdk_root"] == "licensed_sdk"
    assert cfg["native_bridge_path"] == "local_bridge/bin/atracsys_bridge.exe"
    assert (updated / cfg["sdk_root"] / "local.dll").read_bytes() == b"local sdk"
    report = json.loads((updated / "sync_extension/build.json").read_text())
    source = updated / "sync_extension/atracsys_sync/bootstrap.py"
    assert report["files"]["sync_extension/atracsys_sync/bootstrap.py"] == hashlib.sha256(source.read_bytes()).hexdigest()
