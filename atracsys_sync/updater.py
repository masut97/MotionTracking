"""Validate source updates and stage a new, independent local runtime.

Downloading, credentials, application shutdown and shortcut switching belong to
the caller. This module never changes the current installation or opens devices.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

MANIFEST = "update-manifest.json"
MAX_FILES = 1024
MAX_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
SKIP_DIRECTORIES = {"measurements", "logs", "diagnostics", "screenshots", "__pycache__", ".pytest_cache", ".git"}
REQUIRED_SOURCE = {"atracsys_sync/bootstrap.py", "atracsys_sync/integration.py"}


def _sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None


def _relative(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("Invalid update path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError("Invalid update path: " + name)
    for part in path.parts:
        if part.endswith((".", " ")) or part.split(".")[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]
        } or any(char in part for char in '<>"|?*') or any(ord(char) < 32 for char in part):
            raise ValueError("Invalid Windows update path: " + name)
    return path


def _destination(name):
    path = _relative(name)
    if path.suffix != ".py":
        raise ValueError("Only owned Python sources are permitted: " + name)
    if path.parts[0] == "atracsys_sync" and len(path.parts) == 2:
        return PurePosixPath("sync_extension") / path
    if path.parts[0] == "analysis_scripts" and len(path.parts) >= 2:
        if name == "analysis_scripts/trochanter_analysis_core.py":
            return PurePosixPath("analysis_vendor/trochanter_analysis_core.py")
        return path
    if path.parts[0] == "measurement_scripts" and len(path.parts) == 2:
        return path
    raise ValueError("Update source outside owned code: " + name)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def validate_archive(archive, expected_sha256, revision):
    """Return verified source bytes; no archive member is extracted directly."""
    if not _hex(expected_sha256, 64) or not _hex(revision, 40):
        raise ValueError("A full SHA-256 and Git commit are required")
    archive = Path(archive)
    if archive.stat().st_size > MAX_BYTES or _sha256(archive) != expected_sha256:
        raise ValueError("Update archive checksum or size mismatch")
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > MAX_FILES:
            raise ValueError("Too many update archive entries")
        names, total = {}, 0
        for entry in entries:
            name = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
            _relative(name)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ValueError("Archive links or special files are not permitted")
            if entry.flag_bits & 1 or entry.file_size > MAX_FILE_BYTES:
                raise ValueError("Encrypted or oversized archive entry")
            folded = name.casefold()
            if folded in names:
                raise ValueError("Duplicate archive path")
            names[folded] = entry
            total += entry.file_size
        if total > MAX_BYTES:
            raise ValueError("Update payload is too large")
        manifest_entry = names.get(MANIFEST)
        if manifest_entry is None or manifest_entry.is_dir():
            raise ValueError("Update manifest missing")
        manifest = json.loads(source.read(manifest_entry), object_pairs_hook=_unique_object)
        if manifest.get("format_version") != 1 or manifest.get("revision") != revision:
            raise ValueError("Unsupported update manifest or revision mismatch")
        files = manifest.get("files")
        compatible = manifest.get("compatible_base_sha256")
        if not isinstance(files, dict) or not REQUIRED_SOURCE.issubset(files):
            raise ValueError("Update manifest lacks required sources")
        if not isinstance(compatible, list) or not compatible or not all(_hex(value, 64) for value in compatible):
            raise ValueError("Compatible base hashes are required")
        payload, destinations = {}, set()
        for name, expected in files.items():
            destination = _destination(name)
            if not _hex(expected, 64) or destination.as_posix().casefold() in destinations:
                raise ValueError("Invalid source hash or duplicate destination")
            destinations.add(destination.as_posix().casefold())
            entry = names.get(name.casefold())
            if entry is None or entry.filename != name or entry.is_dir():
                raise ValueError("Missing declared source: " + name)
            data = source.read(entry)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Source hash mismatch: " + name)
            compile(data, name, "exec")
            payload[destination.as_posix()] = data
        actual = {entry.filename for entry in entries if not entry.is_dir()}
        if actual != set(files) | {MANIFEST}:
            raise ValueError("Archive contains undeclared files")
        return manifest, payload


def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _copy_runtime(source, destination, preserve=False, excluded=()):
    """Copy regular local runtime files only, never following reparse points."""
    if _is_link(source):
        raise ValueError("Runtime directories must not be links: " + str(source))
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        if entry in excluded or not preserve and entry.name.casefold() in SKIP_DIRECTORIES:
            continue
        if _is_link(entry):
            raise ValueError("Runtime contains a link: " + str(entry))
        if entry.is_dir():
            protected = preserve or entry.name.casefold() in {"geometries", "back_up_geom", "calibration", "calibrations"}
            if protected or entry.name.casefold() not in SKIP_DIRECTORIES:
                _copy_runtime(entry, destination / entry.name, protected, excluded)
        elif entry.is_file() and (preserve or entry.suffix.lower() not in {".log", ".pyc", ".csv", ".part", ".tmp"}):
            shutil.copy2(entry, destination / entry.name)


def _configured_path(app, value):
    path = Path(value)
    return path if path.is_absolute() else app / path


def load_update_settings(app):
    """Read extension settings separately from the inherited strict app config."""
    app = Path(app).resolve()
    path = app / "update_settings.json"
    settings = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError("Invalid updater settings")
    directory = settings.get("update_directory") or str(app.parent / "versions")
    if not isinstance(directory, str):
        raise ValueError("Invalid update directory")
    return {"update_directory": str(_configured_path(app, directory).resolve())}


def install_update(current_app, archive, target_parent, expected_sha256, revision, *, verify_callback=None):
    """Stage source updates over local licensed files; return the new App path.

    ``verify_callback(stage_app)`` may raise, or return a report with passed=False,
    to prevent publication. The caller switches shortcuts only after success.
    """
    manifest, payload = validate_archive(archive, expected_sha256, revision)
    current_app = Path(current_app).resolve(strict=True)
    parent = Path(target_parent).resolve()
    if parent == current_app or parent.is_relative_to(current_app):
        raise ValueError("Update destination must be outside the running App")
    target = parent / revision[:12]
    if target == current_app or current_app.is_relative_to(target):
        raise FileExistsError("A fresh independent version directory is required")
    if target.exists():
        from .version_store import check_prepared
        check_prepared(target/'App',current_app,revision,expected_sha256,payload)
        if verify_callback is not None:
            result=verify_callback(target/'App')
            if result is False or isinstance(result,dict) and result.get('passed') is False:
                raise RuntimeError('Existing version verification failed')
        return target/'App'
    base = current_app / "AtracsysLiveTracker.exe"
    base_hash = _sha256(base)
    if base_hash not in manifest["compatible_base_sha256"]:
        raise ValueError("Update is incompatible with the licensed local base runtime")
    if not (current_app / "AtracsysLiveTracker_Messskripte.exe").is_file():
        raise ValueError("Extended tracker executable missing")
    previous_build = current_app / "sync_extension" / "build.json"
    previous_files = json.loads(previous_build.read_text(encoding="utf-8")).get("files", {}) if previous_build.exists() else {}
    for name, data in payload.items():
        existing = current_app / PurePosixPath(name)
        if name.startswith("measurement_scripts/") and existing.is_file():
            digest = _sha256(existing)
            if digest != hashlib.sha256(data).hexdigest() and previous_files.get(name) != digest:
                raise ValueError("Locally modified measurement script needs manual reconciliation: " + name)
    config = json.loads((current_app / "config.json").read_text(encoding="utf-8-sig"))
    update_settings = load_update_settings(current_app)
    legacy_directory = config.pop("update_directory", None)
    if legacy_directory and not (current_app / "update_settings.json").exists():
        update_settings["update_directory"] = str(_configured_path(current_app, legacy_directory).resolve())
    owned_paths = {}
    for setting, default in (("sdk_root", "sdk"), ("native_bridge_path", "native/bin/atracsys_bridge.exe")):
        effective = _configured_path(current_app, config.get(setting) or default).resolve(strict=True)
        if not effective.is_relative_to(current_app):
            raise ValueError("SDK and native bridge must belong to this local application")
        owned_paths[setting] = effective.relative_to(current_app).as_posix()
    selected = config.get("analysis_script")
    if selected:
        old_selected = _configured_path(current_app, selected).resolve(strict=True)
        if not old_selected.is_relative_to(current_app):
            raise ValueError("Selected analysis must belong to this local application")
        config["analysis_script"] = old_selected.relative_to(current_app).as_posix()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".update-", dir=parent) as temporary:
        stage = Path(temporary) / "version"
        app = stage / "App"
        measurement = config.get("measurement_directory")
        excluded = {_configured_path(current_app, measurement).resolve()} if measurement else set()
        _copy_runtime(current_app, app, excluded=excluded)
        from .project_identity import write_icon
        write_icon(app)
        from .local_migration import preserve_analysis_settings
        preserve_analysis_settings(current_app, app)
        if _sha256(app / base.name) != base_hash:
            raise ValueError("Local base runtime changed while staging the update")
        geometry = _configured_path(current_app, config.get("geometry_directory") or "geometries").resolve(strict=True)
        if geometry != current_app / "geometries":
            _copy_runtime(geometry, app / "geometries", preserve=True)
        for name, data in payload.items():
            destination = app / PurePosixPath(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        config["measurement_directory"] = str(_configured_path(current_app, measurement).resolve()) if measurement else str(current_app.parent / "measurements")
        config.update(owned_paths, geometry_directory="geometries", log_directory="logs")
        (app / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        (app / "update_settings.json").write_text(json.dumps(update_settings, indent=2), encoding="utf-8")
        (app / "logs").mkdir(exist_ok=True)
        from .version_store import local_state
        report = {"revision": revision, "archive_sha256": expected_sha256,
                  "origin_state":local_state(current_app), "prepared_state":local_state(app),
                  "previous_app": str(current_app), "base_sha256": base_hash,
                  "source_files": {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
        (app / "sync_extension" / "update.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        build_path = app / "sync_extension" / "build.json"
        build = json.loads(build_path.read_text(encoding="utf-8")) if build_path.exists() else {}
        build.update(source_revision=revision, output=str(target / "App" / "AtracsysLiveTracker_Messskripte.exe"),
                     output_sha256=_sha256(app / "AtracsysLiveTracker_Messskripte.exe"))
        build["files"] = {path.relative_to(app).as_posix(): _sha256(path)
                          for path in sorted(app.rglob("*")) if path.is_file() and path != build_path}
        build_path.write_text(json.dumps(build, indent=2), encoding="utf-8")
        if verify_callback is not None:
            result = verify_callback(app)
            if result is False or isinstance(result, dict) and result.get("passed") is False:
                raise RuntimeError("Updated runtime verification failed")
        report['prepared_state']=local_state(app)
        (app/'sync_extension/update.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        build['files']['sync_extension/update.json']=_sha256(app/'sync_extension/update.json')
        build_path.write_text(json.dumps(build,indent=2),encoding='utf-8')
        stage.rename(target)
    return target / "App"
