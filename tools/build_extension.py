"""Build a separate executable using the installed app's unmodified embedded modules.

This intentionally does not decompile or overwrite the existing tracker. Only the
entry point of a newly created executable is replaced. Python/Qt/SDK versions and
the other embedded entries remain byte-for-byte identical.
"""
import argparse
import hashlib
import io
import json
import marshal
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import subprocess
import zlib

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = Path.home() / "Documents" / "AtracsysTracking" / "App"

ENTRY = '''import sys
from pathlib import Path
extension = Path(sys.executable).resolve().parent / "sync_extension"
sys.path.insert(0, str(extension))
sys.path.insert(0, str(extension / "vendor"))
try:
    from atracsys_sync.bootstrap import main
    raise SystemExit(main())
except Exception:
    import traceback
    (Path(sys.executable).parent / "measurement_startup_error.log").write_text(traceback.format_exc(), encoding="utf-8")
    raise
'''


def select_analysis(selection, directory):
    """Preserve an explicitly disabled analysis and validate nonempty entries."""
    if not selection:
        return ""
    selected = Path(selection).name
    if "trochanter" in selected.lower():
        selected = "trochanter_auswertung.py"
    if not (directory / selected).is_file():
        selected = "auto_auswertung.py"
    return "analysis_scripts/" + selected


def _build_payload(source, target):
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Für diese installierte Anwendung ist Python 3.11 erforderlich.")
    target.mkdir(parents=True, exist_ok=True)
    original = source / "AtracsysLiveTracker.exe"
    archive = CArchiveReader(str(original))
    content = original.read_bytes()
    cookie = struct.unpack(archive._COOKIE_FORMAT, content[archive._end_offset - archive._COOKIE_LENGTH:archive._end_offset])
    if cookie[4] != 311 or "frozen_main" not in archive.toc:
        raise RuntimeError("Unerwartetes Programmpaket; kein Build ausgeführt.")
    payload = io.BytesIO()
    toc = []
    for name, (offset, size, original_size, compressed, kind) in archive.toc.items():
        blob = content[archive._start_offset + offset:archive._start_offset + offset + size]
        if name == "frozen_main":
            data = marshal.dumps(compile(ENTRY, "measurement_entry.py", "exec"))
            blob = zlib.compress(data)
            compressed, original_size = 1, len(data)
        toc.append((payload.tell(), len(blob), original_size, compressed, kind, name))
        payload.write(blob)
    for option in archive.options:
        toc.append((payload.tell(), 0, 0, 0, "o", option))
    toc_offset = payload.tell()
    table = CArchiveWriter._serialize_toc(toc)
    payload.write(table)
    payload.write(struct.pack(archive._COOKIE_FORMAT, cookie[0], payload.tell() + archive._COOKIE_LENGTH,
                              toc_offset, len(table), cookie[4], cookie[5]))
    output = target / "AtracsysLiveTracker_Messskripte.exe"
    temporary = output.with_suffix(".exe.tmp")
    temporary.write_bytes(content[:archive._start_offset] + payload.getvalue())
    check = CArchiveReader(str(temporary))
    for name in archive.toc:
        if name != "frozen_main" and archive.extract(name) != check.extract(name):
            raise RuntimeError(f"Paketprüfung fehlgeschlagen: {name}")
    temporary.replace(output)
    extension = target / "sync_extension"
    shutil.copytree(ROOT / "atracsys_sync", extension / "atracsys_sync", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    import serial
    shutil.copytree(Path(serial.__file__).parent, extension / "vendor" / "serial", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    scripts = target / "measurement_scripts"
    scripts.mkdir(exist_ok=True)
    for path in (ROOT / "measurement_scripts").glob("*.py"):
        destination = scripts / path.name
        shutil.copy2(path, destination)
    # Never remove locally imported scripts as a side effect of a build.
    if (ROOT / "MESSSKRIPTE.md").exists():
        shutil.copy2(ROOT / "MESSSKRIPTE.md", target / "MESSSKRIPTE.md")
    if (ROOT / "HARDWARE_STATUS_2026-09-15.md").exists():
        shutil.copy2(ROOT / "HARDWARE_STATUS_2026-09-15.md", target / "HARDWARE_STATUS_2026-09-15.md")
    report = {"base_executable": str(original), "base_sha256": hashlib.sha256(content).hexdigest(),
              "output": str(output), "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
              "preserved_embedded_entries": len(archive.toc) - 1, "source": str(ROOT),
              "hardware_synchronization_implemented": False,
              "software_time_alignment_supported": True}
    report["source_revision"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (extension / "build.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def build(source, target, config=None):
    """Validate a fresh complete stage, then publish without modifying old installs."""
    owned = ("atracsys_sync", "analysis_scripts", "measurement_scripts")
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *owned], cwd=ROOT).returncode
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", *owned], cwd=ROOT)
    if dirty or untracked.strip():
        raise RuntimeError("Commit the owned source tree before producing a versioned build.")
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists() or source == target or source in target.parents or target in source.parents:
        raise ValueError("Build requires a NEW target outside the base application.")
    for name in ("AtracsysLiveTracker.exe", "_internal", "sdk", "native", "assets", "geometries", "analysis_vendor"):
        if not (source / name).exists():
            raise FileNotFoundError(f"Incomplete base runtime: {source / name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=target.parent) as temporary:
        stage = Path(temporary) / "App"
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "logs", "screenshots", "measurements", "diagnostics")
        shutil.copytree(source, stage, ignore=ignore)
        sys.path.insert(0, str(ROOT))
        from atracsys_sync.local_migration import preserve_analysis_settings
        preserve_analysis_settings(source, stage)
        report = _build_payload(source, stage)
        from atracsys_sync.project_identity import VERSION
        report['application_version']=VERSION
        report['project']='MotionTracking'
        from atracsys_sync.project_identity import write_icon
        write_icon(stage)
        shutil.copytree(ROOT / "analysis_scripts", stage / "analysis_scripts", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.local.json"))
        # Library implementation is not a selectable analysis entry point.
        core = stage / "analysis_scripts" / "trochanter_analysis_core.py"
        shutil.copy2(core, stage / "analysis_vendor" / core.name)
        annotations = stage / "analysis_scripts/failure_annotations.local.json"
        if annotations.is_file() and not (stage / "analysis_vendor" / annotations.name).exists():
            shutil.copy2(annotations, stage / "analysis_vendor" / annotations.name)
        core.unlink()  # Own newly built staging file only; never an input file.
        cfg = json.loads(Path(config or source / "config.json").read_text(encoding="utf-8-sig"))
        # Geometry/calibration belongs to the machine supplying the settings.
        config_root = Path(config).resolve().parent if config else source
        geometry_root = Path(cfg.get("geometry_directory") or "geometries")
        if not geometry_root.is_absolute():
            geometry_root = config_root / geometry_root
        if not geometry_root.is_dir():
            raise FileNotFoundError(f"Configured geometries missing: {geometry_root}")
        shutil.copytree(geometry_root, stage / "geometries", dirs_exist_ok=True, ignore=ignore)
        cfg["geometry_directory"] = "geometries"
        cfg.pop("update_directory", None)
        version_store = target.parent.parent if target.parent.parent.name == 'versions' else target.parent / 'versions'
        (stage / "update_settings.json").write_text(json.dumps(
            {"update_directory": str(version_store)}, indent=2), encoding="utf-8")
        cfg.update(sdk_root="sdk", native_bridge_path="native/bin/atracsys_bridge.exe", log_directory="logs")
        cfg["analysis_script"] = select_analysis(cfg.get("analysis_script"), stage / "analysis_scripts")
        (stage / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        cli_source = ROOT / "build" / "github-cli"
        if (cli_source / "gh.exe").is_file():
            (stage / "update_tools").mkdir(exist_ok=True)
            for name in ("gh.exe", "LICENSE", "provenance.json"):
                shutil.copy2(cli_source / name, stage / "update_tools" / name)
        if not (stage / "update_tools" / "gh.exe").is_file():
            raise FileNotFoundError("Official GitHub CLI must be bundled for private in-app updates.")
        report["output"] = str(target / "AtracsysLiveTracker_Messskripte.exe")
        report["files"] = {}
        for path in sorted(stage.rglob("*")):
            if path.is_file() and path != stage / "sync_extension" / "build.json":
                with path.open("rb") as stream:
                    report["files"][path.relative_to(stage).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        (stage / "sync_extension" / "build.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        stage.rename(target)
    print(json.dumps({"target": str(target), "files": len(report["files"])}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_APP)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="Preserve settings from this configuration")
    args = parser.parse_args()
    build(args.source.resolve(), args.target.resolve(), args.config)
