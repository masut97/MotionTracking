from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import auswertung as common
from _lib import rom_quality as curve_quality


ROTATION_MODES = {"FE", "LB", "AR"}
TRANSLATION_MODES = {"AS", "LS", "AC"}
ALL_MODES = ROTATION_MODES | TRANSLATION_MODES

DEFAULT_CONFIG = {
    "markers": {
        "reference": 299,
        "clamp_up": 301,
        "clamp_down": 302,
        "vertebra_cranial": 311,
        "vertebra_caudal": 314,
    },
    "baseline_seconds": 1.0,
    "default_movement": "FE",
    "rotation_axes": {
        "FE": [0.0, 1.0, 0.0],
        "LB": [0.0, 0.0, 1.0],
        "AR": [0.0, 0.0, 1.0],
    },
    "legacy_rotation_axes": {
        "FE": [0.0, 0.0, 1.0],
        "LB": [0.0, 0.0, 1.0],
        "AR": [0.0, 0.0, 1.0],
    },
    "translation_axes": {
        "AS": [0.0, 0.0, 1.0],
        "LS": [0.0, 0.0, 1.0],
        "AC": [0.0, 0.0, 1.0],
    },
    "legacy_translation_axes": {
        "AS": [0.0, 0.0, 1.0],
        "LS": [0.0, 0.0, 1.0],
        "AC": [0.0, 0.0, 1.0],
    },
    "sign": {mode: 1.0 for mode in ALL_MODES},
    "registry_path": r"Motion Tracking",
    "zwick_file": "",
    "zwick_search_directories": [],
    "zwick_filename_patterns": ["*.tra"],
    "zwick_max_age_seconds": 300.0,
    "zwick_wait_seconds": 30.0,
    "zwick_export_leading_result_row": False,
    "zwick_columns": {
        "time": "Prüfzeit",
        "rotation_load": "Standarddrehmoment Torsion",
        "rotation_displacement": "Winkel Workaround",
        "translation_load": "Standardkraft 1",
        "translation_displacement": "Axial Workaround",
    },
    "rotation_legacy_offset_deg": 180000.0,
    "legacy_output_directory": "",
    "write_testxpert_rom_files": True,
    "load_limits": {
        "FE": [-7.5, 7.5],
        "LB": [-7.5, 7.5],
        "AR": [-7.5, 7.5],
        "AS": [-150.0, 150.0],
        "LS": [-150.0, 150.0],
        "AC": [-150.0, 400.0],
    },
    "quality": {
        **curve_quality.DEFAULTS,
        "minimum_clamp_rom_rotation_deg": 0.50,
        "minimum_clamp_rom_translation_mm": 0.10,
        "edge_peak_fraction": 0.03,
        "peak_gap_factor": 1.75,
        "registration_warning_mm": 1.0,
        "registration_critical_mm": 2.0,
    },
    "open_figure": True,
}


@dataclass(frozen=True)
class RomPose:
    rotation: common.Matrix
    translation_mm: tuple[float, float, float]
    registration_error_mm: float = math.nan


@dataclass(frozen=True)
class RomTrackingData:
    times_s: tuple[float, ...]
    poses: dict[int, tuple[RomPose, ...]]
    total_rows: int
    incomplete_samples: int
    source_format: str
    invalid_samples: int = 0
    received_times_s: tuple[float, ...] = ()
    unseen_marker_events: tuple = ()


@dataclass(frozen=True)
class RomZwickData:
    times_s: tuple[float, ...]
    loads: tuple[float, ...]
    displacements: tuple[float, ...]
    vertical_translation: tuple[float, ...]
    load_column: str
    displacement_column: str
    load_unit: str
    displacement_unit: str
    source: Path
    removed_leading_result_rows: int = 0
    repeated_time_samples: int = 0


@dataclass(frozen=True)
class RomMetrics:
    rom_range: float
    positive_load: float
    negative_load: float
    up_rom: float | None
    down_rom: float | None
    zero_point: float | None
    zero_indices: tuple[int, int] | None
    stored_offset: float


@dataclass(frozen=True)
class QualityFinding:
    severity: str
    code: str
    title: str
    detail: str
    solution: str = ""
    intervals: tuple = ()


def quality_summary(findings: Sequence[QualityFinding]) -> dict:
    status = ("critical" if any(item.severity == "critical" for item in findings)
              else "warning" if any(item.severity == "warning" for item in findings) else "ok")
    rating, label = {"ok": ("good", "Approved"),
                     "warning": ("medium", "Check data and if needed repeat"),
                     "critical": ("bad", "Not approved")}[status]
    return {"status": status, "rating": rating, "label": label,
            "rule_version": curve_quality.VERSION,
            "repeat_required": status == "critical",
            "action": "REPEAT TEST" if status == "critical" else label,
            "hints": [item.detail for item in findings],
            "solutions": [item.solution for item in findings if item.solution]}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Five-marker ROM analysis replacing the MATLAB functions"
    )
    parser.add_argument("input", type=Path, help="Atracsys CSV")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--bewegung",
        choices=tuple(sorted(ALL_MODES | {mode.lower() for mode in ALL_MODES})),
        default=None,
        help="FE, LB, AR, AS, LS oder AC",
    )
    parser.add_argument("--zwick", type=Path, default=None, help="testXpert .tra file")
    parser.add_argument(
        "--ohne-zwick",
        action="store_true",
        help="Retrospective camera analysis without searching for a .tra file",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--open", action="store_true", dest="force_open")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument(
        "--no-rom-files",
        action="store_true",
        help="Do not write UpROM/DnROM/ZeroPoint files for testXpert",
    )
    return parser.parse_args(argv)


def load_config(path: Path | None) -> tuple[dict, Path]:
    config_path = path or Path(__file__).with_name("rom_auswertung_config.json")
    user_config: dict = {}
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8-sig") as stream:
            loaded = json.load(stream)
        if not isinstance(loaded, dict):
            raise ValueError("The ROM configuration must be a JSON object.")
        user_config = loaded
    local_path = config_path.with_suffix(".local.json")
    if path is None and local_path.is_file():
        local = json.loads(local_path.read_text(encoding="utf-8-sig"))
        if not isinstance(local, dict):
            raise ValueError("Local ROM configuration must be a JSON object.")
        user_config = common._deep_merge(user_config, local)
    return common._deep_merge(DEFAULT_CONFIG, user_config), config_path


def _frame_key_and_time(row: dict[str, str]) -> tuple[str, float] | None:
    sample = (
        row.get("shared_sample_index", "").strip()
        or row.get("frame_counter", "").strip()
    )
    time_text = (
        row.get("time_since_start_s", "").strip()
        or row.get("computer_monotonic_s", "").strip()
    )
    if not sample or not time_text:
        return None
    try:
        value = float(time_text)
    except ValueError:
        return None
    return (sample, value) if math.isfinite(value) else None


def _valid_pose(pose: RomPose) -> bool:
    values = [value for row in pose.rotation for value in row] + list(pose.translation_mm)
    if not all(math.isfinite(value) for value in values):
        return False
    rotation = pose.rotation
    product = common.multiply(rotation, common.transpose(rotation))
    determinant = sum(rotation[0][j] * (rotation[1][(j+1)%3]*rotation[2][(j+2)%3]
                      - rotation[1][(j+2)%3]*rotation[2][(j+1)%3]) for j in range(3))
    return (abs(determinant-1)<.01 and all(abs(product[i][j]-(i==j))<.01
                                         for i in range(3) for j in range(3)))


def _load_current_tracking(path: Path, marker_ids: Sequence[int], *, explicit_instances=False,
                           reject_duplicate_poses=False, allow_geometry_tracking_changes=False) -> RomTrackingData:
    frames: dict[str, dict[int, RomPose]] = {}
    frame_times: dict[str, float] = {}
    total_rows = 0
    invalid_rows = 0
    unseen = {}
    wanted = set(marker_ids)
    ambiguous = set()
    geometry_tracks = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = set(reader.fieldnames or ())
        required = {
            "geometry_id",
            "visible",
            "x_mm",
            "y_mm",
            "z_mm",
            *common.ROTATION_COLUMNS,
        }
        missing = required.difference(headers)
        if missing:
            raise ValueError(
                "The Atracsys ROM file is missing columns: "
                + ", ".join(sorted(missing))
            )
        for row in reader:
            total_rows += 1
            key_and_time = _frame_key_and_time(row)
            if key_and_time is None:
                invalid_rows += 1
                continue
            sample, sample_time = key_and_time
            frames.setdefault(sample, {})
            if sample in frame_times and abs(frame_times[sample]-sample_time)>1e-8:
                invalid_rows += 1
            frame_times.setdefault(sample, sample_time)
            try:
                marker_id = int(row["geometry_id"])
            except (TypeError, ValueError):
                if row.get('visible','')=='0':unseen.setdefault(sample,set()).update(wanted)
                continue
            if explicit_instances:
                tracking_id = row.get('tracking_id', '').strip()
                qualified = f'{marker_id}:{tracking_id}'
                if qualified in wanted:
                    marker_id = qualified
                elif marker_id not in wanted:
                    continue
            if marker_id not in wanted:
                continue
            if (explicit_instances and not allow_geometry_tracking_changes
                    and isinstance(marker_id, int) and row.get('tracking_id', '').strip()):
                geometry_tracks.setdefault(marker_id, set()).add(row['tracking_id'].strip())
            if row.get("visible", "") != "1":
                if row.get("visible", "") == "0":
                    unseen.setdefault(sample,set()).add(marker_id)
                else:
                    invalid_rows += 1
                continue
            try:
                rotation = common._matrix([float(row[name]) for name in common.ROTATION_COLUMNS])
                error_text = row.get("registration_error_mm", "").strip()
                pose = RomPose(rotation=rotation,
                    translation_mm=(float(row["x_mm"]),float(row["y_mm"]),float(row["z_mm"])),
                    registration_error_mm=float(error_text) if error_text else math.nan)
                if not _valid_pose(pose):
                    raise ValueError("Invalid marker pose")
            except (TypeError,ValueError):
                invalid_rows += 1
                continue
            if (explicit_instances or reject_duplicate_poses) and marker_id in frames[sample]:
                ambiguous.add((sample, marker_id))
                invalid_rows += 1
            frames.setdefault(sample, {})[marker_id] = pose

    for sample, marker_id in ambiguous:
        frames[sample].pop(marker_id, None)
    if explicit_instances and not allow_geometry_tracking_changes:
        for marker_id, tracks in geometry_tracks.items():
            if len(tracks) > 1:
                # Even non-overlapping tracking IDs must not be spliced into one object.
                for poses in frames.values():
                    poses.pop(marker_id, None)

    complete = [
        (frame_times[key], poses)
        for key, poses in frames.items()
        if all(marker_id in poses for marker_id in marker_ids)
    ]
    # Acquisition order is evidence: sorting would hide backwards timestamps.
    if not complete:
        raise ValueError(
            "No complete valid samples for the required markers " + ", ".join(map(str, marker_ids)) + " were found."
        )
    start = complete[0][0]
    return RomTrackingData(
        times_s=tuple(sample_time - start for sample_time, _ in complete),
        poses={
            marker_id: tuple(poses[marker_id] for _, poses in complete)
            for marker_id in marker_ids
        },
        total_rows=total_rows,
        incomplete_samples=len(frames) - len(complete),
        source_format="atracsys_long_csv",
        invalid_samples=invalid_rows,
        received_times_s=tuple(value-start for value in frame_times.values()),
        unseen_marker_events=tuple((frame_times[key]-start,tuple(sorted(ids, key=str))) for key,ids in unseen.items()),
    )


def _legacy_timestamp_seconds(value: float) -> float:
    integer = int(round(value))
    milliseconds = integer % 1000
    seconds = (integer // 1000) % 100
    minutes = (integer // 100000) % 100
    hours = integer // 10000000
    return hours * 3600.0 + minutes * 60.0 + seconds + milliseconds / 1000.0


def _legacy_matrix(values: Sequence[float]) -> common.Matrix:
    # MATLAB schrieb matrix(1:9) spaltenweise: R11,R21,R31,R12,...
    if len(values) != 9:
        raise ValueError("Invalid rotation matrix in the MATLAB CSV.")
    return (
        (values[0], values[3], values[6]),
        (values[1], values[4], values[7]),
        (values[2], values[5], values[8]),
    )


def _load_legacy_tracking(path: Path, marker_ids: Sequence[int]) -> RomTrackingData:
    marker_order = tuple(marker_ids)
    poses: dict[int, list[RomPose]] = {marker_id: [] for marker_id in marker_order}
    times: list[float] = []
    total_rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        headers = next(reader, None)
        if headers is None or len(headers) < 61:
            raise ValueError("The historical MATLAB CSV does not have the expected 61 columns.")
        day_offset = 0.0
        previous_time: float | None = None
        for row in reader:
            total_rows += 1
            if len(row) < 61:
                continue
            try:
                values = [float(value.strip()) for value in row[:61]]
            except ValueError:
                continue
            if not all(math.isfinite(value) for value in values):
                continue
            stamp = int(round(values[0]))
            if stamp<0 or stamp//10000000>=24 or (stamp//100000)%100>=60 or (stamp//1000)%100>=60:
                continue
            sample_poses = []
            for index in range(len(marker_order)):
                start = 1+index*12
                sample_poses.append(RomPose(_legacy_matrix(values[start+3:start+12]),tuple(values[start:start+3])))
            if not all(_valid_pose(pose) for pose in sample_poses):
                continue
            sample_time = _legacy_timestamp_seconds(values[0])
            if previous_time is not None and sample_time + day_offset < previous_time - 43200:
                day_offset += 86400.0
            sample_time += day_offset
            previous_time = sample_time
            times.append(sample_time)
            for marker_id, pose in zip(marker_order,sample_poses):
                poses[marker_id].append(pose)
    if not times:
        raise ValueError("The historical MATLAB CSV contains no valid measurement rows.")
    first = times[0]
    return RomTrackingData(
        times_s=tuple(value - first for value in times),
        poses={marker_id: tuple(values) for marker_id, values in poses.items()},
        total_rows=total_rows,
        incomplete_samples=total_rows-len(times),
        source_format="legacy_matlab_wide_csv",
        invalid_samples=total_rows-len(times),
    )


def load_tracking(path: Path, marker_ids: Sequence[int]) -> RomTrackingData:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        headers = [value.strip() for value in next(csv.reader(stream), [])]
    if "geometry_id" in headers:
        return _load_current_tracking(path, marker_ids)
    return _load_legacy_tracking(path, marker_ids)


def movement_axis(config: dict, mode: str, source_format: str) -> Sequence[float]:
    legacy = source_format == "legacy_matlab_wide_csv"
    if mode in ROTATION_MODES:
        key = "legacy_rotation_axes" if legacy else "rotation_axes"
    else:
        key = "legacy_translation_axes" if legacy else "translation_axes"
    configured = config.get(key)
    if not isinstance(configured, dict) or mode not in configured:
        fallback_key = "rotation_axes" if mode in ROTATION_MODES else "translation_axes"
        configured = config[fallback_key]
    return configured[mode]


def _normalized_stem(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")


def determine_movement(
    explicit: str | None,
    input_path: Path,
    zwick_path: Path | None,
    registry: dict[str, str],
    config: dict,
) -> tuple[str, str]:
    if explicit:
        return explicit.upper(), "command_line"
    paths = []
    if zwick_path is not None:
        paths.append((zwick_path, "zwick_filename"))
    paths.append((input_path, "atracsys_filename"))
    for path, source in paths:
        stem = _normalized_stem(path)
        for mode in ("FE", "LB", "AR", "AS", "LS", "AC"):
            if re.search(rf"(^|_){mode.lower()}($|_)", stem):
                return mode, source
    registry_mode = registry.get("Movement", "").strip().upper()
    if registry_mode in ALL_MODES:
        return registry_mode, "registry_fallback"
    fallback = str(config.get("default_movement", "FE")).strip().upper()
    if fallback not in ALL_MODES:
        raise ValueError("default_movement is invalid.")
    return fallback, "config_default"


def _resolve_zwick_path(
    input_path: Path,
    command_line: Path | None,
    config: dict,
    registry: dict[str, str],
) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    explicit = common._explicit_zwick_path(command_line, config, registry)
    deadline = time.monotonic() + max(0.0, float(config.get("zwick_wait_seconds", 30.0)))
    while True:
        candidates: list[Path] = []
        if explicit is not None:
            if explicit.is_dir():
                for pattern in config.get("zwick_filename_patterns", ["*.tra"]):
                    candidates.extend(explicit.glob(str(pattern)))
                candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            elif explicit.is_file():
                candidates = [explicit]
        else:
            candidates = common._scan_zwick_candidates(input_path, config, registry)
        for candidate in candidates:
            if common._file_is_stable(candidate, 0.1):
                return candidate.resolve(), warnings
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    if explicit is not None:
        warnings.append(f"The specified Zwick file was not found: {explicit}")
    else:
        warnings.append("No time-matched testXpert .tra file was found.")
    return None, warnings


def _column_index(headers: Sequence[str], configured: str) -> int:
    wanted = common._normalized_text(configured)
    for index, header in enumerate(headers):
        if common._normalized_text(header) == wanted:
            return index
    raise ValueError(f"Zwick column '{configured}' was not found.")


def load_zwick(path: Path, mode: str, config: dict) -> RomZwickData:
    lines = common._decode_table(path).splitlines()
    delimiter = common._detect_delimiter(lines)
    rows = [
        [cell.strip().strip('"') for cell in row]
        for row in csv.reader(lines, delimiter=delimiter)
    ]
    columns = dict(config.get("zwick_columns", {}))
    load_key = "rotation_load" if mode in ROTATION_MODES else "translation_load"
    displacement_key = (
        "rotation_displacement" if mode in ROTATION_MODES else "translation_displacement"
    )
    names = {
        "time": str(columns.get("time", "Prüfzeit")),
        "load": str(columns.get(load_key, "")),
        "displacement": str(columns.get(displacement_key, "")),
    }
    header_index: int | None = None
    indices: dict[str, int] = {}
    for index, headers in enumerate(rows[:50]):
        try:
            indices = {key: _column_index(headers, value) for key, value in names.items()}
        except ValueError:
            continue
        header_index = index
        break
    if header_index is None:
        raise ValueError(
            "Required testXpert columns are missing: " + ", ".join(names.values())
        )
    headers = rows[header_index]
    units = rows[header_index + 1] if header_index + 1 < len(rows) else []
    time_values: list[float] = []
    loads: list[float] = []
    displacements: list[float] = []
    cycles: list[float] = []
    cycle_index = next((i for i, header in enumerate(headers)
                        if common._normalized_text(header) in {"zyklus", "cycle"}), None)
    required = max(indices.values())
    for row in rows[header_index + 1 :]:
        if len(row) <= required:
            continue
        try:
            sample_time = common._parse_number(row[indices["time"]])
            load = common._parse_number(row[indices["load"]])
            displacement = common._parse_number(row[indices["displacement"]])
        except ValueError:
            continue
        if all(math.isfinite(value) for value in (sample_time, load, displacement)):
            time_values.append(sample_time)
            loads.append(load)
            displacements.append(displacement)
            try:
                cycles.append(common._parse_number(row[cycle_index]) if cycle_index is not None else math.nan)
            except (ValueError, IndexError):
                cycles.append(math.nan)
    if len(loads) < 2:
        raise ValueError("The testXpert file contains fewer than two valid data rows.")

    removed_result_rows = 0
    # This is an explicit local export-format setting, not a generic timestamp repair.
    # testXpert can prepend aggregate results: t=0, angle=0, cycle=last cycle,
    # followed by the actual chronological series at t=0, cycle=0.
    if (config.get("zwick_export_leading_result_row", False) and len(loads) >= 3
            and time_values[0] == time_values[1] == 0.0
            and displacements[0] == 0.0
            and math.isfinite(cycles[0]) and cycles[0] > cycles[1]
            and cycles[1] == 0.0):
        time_values, loads, displacements = time_values[1:], loads[1:], displacements[1:]
        removed_result_rows = 1
    repeated_time_samples = sum(b == a for a, b in zip(time_values, time_values[1:]))

    load_unit = units[indices["load"]] if indices["load"] < len(units) else ""
    displacement_unit = (
        units[indices["displacement"]]
        if indices["displacement"] < len(units)
        else ""
    )
    normalized_load_unit = common._normalized_text(load_unit)
    normalized_displacement_unit = common._normalized_text(displacement_unit)
    if mode in ROTATION_MODES and "nmm" in normalized_load_unit:
        loads = [value / 1000.0 for value in loads]
        load_unit = "Nm"
    if mode in ROTATION_MODES and normalized_displacement_unit == "rad":
        displacements = [math.degrees(value) for value in displacements]
        displacement_unit = "°"
    if mode in TRANSLATION_MODES and normalized_displacement_unit == "m":
        displacements = [value * 1000.0 for value in displacements]
        displacement_unit = "mm"
    start = time_values[0]
    return RomZwickData(
        times_s=tuple(value - start for value in time_values),
        loads=tuple(loads),
        displacements=tuple(displacements),
        vertical_translation=tuple(displacements),
        load_column=headers[indices["load"]],
        displacement_column=headers[indices["displacement"]],
        load_unit=load_unit,
        displacement_unit=displacement_unit,
        source=path,
        removed_leading_result_rows=removed_result_rows,
        repeated_time_samples=repeated_time_samples,
    )


def _matrix_vector(
    matrix: common.Matrix, vector: Sequence[float]
) -> tuple[float, float, float]:
    return tuple(
        sum(matrix[row][column] * float(vector[column]) for column in range(3))
        for row in range(3)
    )  # type: ignore[return-value]


def _baseline_count(times_s: Sequence[float], seconds: float) -> int:
    count = sum(value <= seconds for value in times_s)
    return min(len(times_s), max(5, count))


def _marker_rotation(
    data: RomTrackingData,
    reference_id: int,
    marker_id: int,
    axis: Sequence[float],
    baseline_count: int,
    sign: float,
) -> tuple[float, ...]:
    normalized_axis = common._normalize_axis(axis)
    rotations = [
        common.multiply(common.transpose(reference.rotation), marker.rotation)
        for reference, marker in zip(data.poses[reference_id], data.poses[marker_id])
    ]
    baseline = common.average_rotation(rotations[:baseline_count])
    result: list[float] = []
    previous: common.Quaternion | None = None
    for rotation in rotations:
        delta = common.multiply(rotation, common.transpose(baseline))
        quaternion = common.matrix_to_quaternion(delta)
        if previous is not None and sum(a * b for a, b in zip(previous, quaternion)) < 0:
            quaternion = tuple(-value for value in quaternion)  # type: ignore[assignment]
        previous = quaternion
        rotvec = common.quaternion_to_rotvec(quaternion)
        result.append(
            math.degrees(sum(a * b for a, b in zip(rotvec, normalized_axis))) * sign
        )
    return tuple(result)


def _positions_in_reference(
    data: RomTrackingData, reference_id: int, marker_id: int
) -> tuple[tuple[float, float, float], ...]:
    result = []
    for reference, marker in zip(data.poses[reference_id], data.poses[marker_id]):
        difference = tuple(
            marker.translation_mm[index] - reference.translation_mm[index]
            for index in range(3)
        )
        result.append(_matrix_vector(common.transpose(reference.rotation), difference))
    return tuple(result)


def _marker_translation(
    data: RomTrackingData,
    reference_id: int,
    marker_id: int,
    axis: Sequence[float],
    baseline_count: int,
    sign: float,
) -> tuple[float, ...]:
    normalized_axis = common._normalize_axis(axis)
    raw = [
        sum(a * b for a, b in zip(position, normalized_axis)) * sign
        for position in _positions_in_reference(data, reference_id, marker_id)
    ]
    baseline = statistics.mean(raw[:baseline_count])
    return tuple(value - baseline for value in raw)


def _fixture_translation_prediction(data, reference_id, clamp_id, bone_id, axis, count, sign):
    """Expected bone-point motion under rigid fixation, including rotation lever arms."""
    clamp_positions=_positions_in_reference(data,reference_id,clamp_id)
    bone_positions=_positions_in_reference(data,reference_id,bone_id)
    rotations=[common.multiply(common.transpose(ref.rotation),clamp.rotation)
               for ref,clamp in zip(data.poses[reference_id],data.poses[clamp_id])]
    offsets=[_matrix_vector(common.transpose(rotation),tuple(b-c for b,c in zip(bone,clamp)))
             for rotation,bone,clamp in zip(rotations[:count],bone_positions[:count],clamp_positions[:count])]
    offset=tuple(statistics.mean(value[i] for value in offsets) for i in range(3))
    normalized=common._normalize_axis(axis)
    raw=[]
    for rotation,position in zip(rotations,clamp_positions):
        arm=_matrix_vector(rotation,offset)
        raw.append(sum((p+a)*component for p,a,component in zip(position,arm,normalized))*sign)
    baseline=statistics.mean(raw[:count])
    return tuple(value-baseline for value in raw)


def calculate_motion(
    data: RomTrackingData,
    mode: str,
    axis: Sequence[float],
    markers: dict,
    baseline_seconds: float,
    sign: float,
) -> tuple[dict[str, tuple[float, ...]], int]:
    reference_id = int(markers["reference"])
    baseline_count = _baseline_count(data.times_s, baseline_seconds)
    named_markers = {
        "clamp_up": int(markers["clamp_up"]),
        "clamp_down": int(markers["clamp_down"]),
        "vertebra_cranial": int(markers["vertebra_cranial"]),
        "vertebra_caudal": int(markers["vertebra_caudal"]),
    }
    if mode in ROTATION_MODES:
        curves = {
            name: _marker_rotation(
                data, reference_id, marker_id, axis, baseline_count, sign
            )
            for name, marker_id in named_markers.items()
        }
        segmental, _ = common.projected_segment_angle(
            data,  # type: ignore[arg-type]
            reference_id,
            named_markers["vertebra_cranial"],
            named_markers["vertebra_caudal"],
            axis,
            baseline_seconds,
            sign,
        )
        clamp_segmental, _ = common.projected_segment_angle(
            data,  # type: ignore[arg-type]
            reference_id,
            named_markers["clamp_up"],
            named_markers["clamp_down"],
            axis,
            baseline_seconds,
            sign,
        )
    else:
        curves = {
            name: _marker_translation(
                data, reference_id, marker_id, axis, baseline_count, sign
            )
            for name, marker_id in named_markers.items()
        }
        for label,clamp_name,bone_name in (('cranial','clamp_up','vertebra_cranial'),('caudal','clamp_down','vertebra_caudal')):
            curves[label+'_fixture_expected']=_fixture_translation_prediction(data,reference_id,
                named_markers[clamp_name],named_markers[bone_name],axis,baseline_count,sign)
        segmental = tuple(
            cranial - caudal
            for cranial, caudal in zip(
                curves["vertebra_cranial"], curves["vertebra_caudal"]
            )
        )
        baseline = statistics.mean(segmental[:baseline_count])
        segmental = tuple(value - baseline for value in segmental)
        clamp_segmental = tuple(
            clamp_up - clamp_down
            for clamp_up, clamp_down in zip(
                curves["clamp_up"], curves["clamp_down"]
            )
        )
        clamp_baseline = statistics.mean(clamp_segmental[:baseline_count])
        clamp_segmental = tuple(
            value - clamp_baseline for value in clamp_segmental
        )
    curves["segmental"] = tuple(segmental)
    curves["clamp_segmental"] = tuple(clamp_segmental)
    return curves, baseline_count


def _has_token(path: Path, token: str) -> bool:
    return bool(re.search(rf"(^|_){re.escape(token.lower())}($|_)", _normalized_stem(path)))


def find_zero_and_rom(
    zwick: RomZwickData,
    mode: str,
    is_active_rom: bool,
    rotation_offset: float,
) -> RomMetrics:
    loads = zwick.loads
    displacements = zwick.displacements
    maximum_index = max(range(len(loads)), key=loads.__getitem__)
    minimum_index = min(range(len(loads)), key=loads.__getitem__)
    offset = rotation_offset if mode in ROTATION_MODES else 0.0
    stored = tuple(value + offset for value in displacements)
    zero_indices: tuple[int, int] | None = None
    zero_point: float | None = None
    up_rom: float | None = None
    down_rom: float | None = None
    if is_active_rom:
        first_range = range(0, maximum_index + 1)
        first_zero = min(first_range, key=lambda index: abs(loads[index]))
        lower, upper = sorted((maximum_index, minimum_index))
        second_range = range(lower, upper + 1)
        second_zero = min(second_range, key=lambda index: abs(loads[index]))
        zero_indices = (first_zero, second_zero)
        zero_point = (stored[first_zero] + stored[second_zero]) / 2.0
        up_rom = stored[maximum_index]
        down_rom = stored[minimum_index]
    return RomMetrics(
        rom_range=max(displacements) - min(displacements),
        positive_load=max(loads),
        negative_load=min(loads),
        up_rom=up_rom,
        down_rom=down_rom,
        zero_point=zero_point,
        zero_indices=zero_indices,
        stored_offset=offset,
    )


def _span(values: Sequence[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(value)]
    return max(finite) - min(finite) if finite else 0.0


def _edge_extrema(values: Sequence[float], edge_fraction: float) -> list[str]:
    if len(values) < 5 or _span(values) <= 1e-12:
        return []
    edge_count = max(1, math.ceil((len(values) - 1) * edge_fraction))
    maximum_index = max(range(len(values)), key=values.__getitem__)
    minimum_index = min(range(len(values)), key=values.__getitem__)
    result = []
    if maximum_index <= edge_count:
        result.append("positive peak at the beginning")
    elif maximum_index >= len(values) - 1 - edge_count:
        result.append("positive peak at the end")
    if minimum_index <= edge_count:
        result.append("negative peak at the beginning")
    elif minimum_index >= len(values) - 1 - edge_count:
        result.append("negative peak at the end")
    return result


def _tracking_evidence(tracking, findings):
    """Separate visibility observations from absent recorded frames; do not infer SDK causes."""
    events=tracking.unseen_marker_events
    received=tracking.received_times_s
    long_form=tracking.source_format=='atracsys_long_csv' and bool(received)
    gaps=[finding for finding in findings if finding.code in {'peak_data_gap','borderline_gap'}]
    origins={}
    result=[]
    for finding in findings:
        if finding in gaps:
            from dataclasses import replace
            grouped={}
            for start,end in finding.intervals:
                inside=[t for t in received if start<t<end]
                invisible={t for t,_ids in events if start<t<end}
                if long_form and inside and all(t in invisible for t in inside):origin='marker_not_recognized'
                elif long_form and not inside:origin='no_recorded_camera_frame'
                else:origin='unknown_or_invalid_pose'
                grouped.setdefault(origin,[]).append((start,end))
                origins[origin]=origins.get(origin,0)+1
            for origin,intervals in grouped.items():
                if origin=='marker_not_recognized':
                    title='Peak unavailable: marker not recognized'
                    detail='Received frames explicitly mark required markers as invisible.'
                    solution='Reposition the markers and inspect/clean or replace dirty or damaged fiducials. Keep all markers in view and repeat.'
                elif origin=='no_recorded_camera_frame':
                    title='Peak unavailable: no recorded camera frame'
                    detail='No saved frame exists inside the interval; this is not evidence of dirty fiducials.'
                    solution='Inspect acquisition logs, camera/network/PC load, recording frequency and export. Recover complete data or repeat; do not diagnose a visibility failure from this gap alone.'
                else:
                    title='Peak unavailable: gap origin unknown'
                    detail='This export does not establish whether frames or marker poses were omitted.'
                    solution='Compare acquisition and visibility logs with the original export. Resolve the gap source and repeat if the peak cannot be recovered.'
                result.append(replace(finding,title=title,detail=f'{len(intervals)} affected intervals. '+detail,
                                      solution=solution,intervals=tuple(intervals)))
            continue
        result.append(finding)
    recorded=received if long_form else tracking.times_s
    positive=[b-a for a,b in zip(recorded,recorded[1:]) if b>a]
    recorded_gaps=[]
    if positive:
        median=statistics.median(positive)
        recorded_gaps=[(a,b) for a,b in zip(recorded,recorded[1:]) if b-a>1.75*median]
    if recorded_gaps:
        if long_form:
            result.append(QualityFinding('info','recorded_camera_frame_gap','No recorded camera frame inside interval',
                f'{len(recorded_gaps)} timestamp intervals have no saved frame. Acquisition, transport, scheduling and export must be distinguished; unseen marker spheres are not established by this evidence.',
                'Inspect camera/acquisition logs, network, PC load and the selected recording rate. Recover missing observations or repeat if motion or a peak is unverified.',tuple(recorded_gaps)))
        else:
            result.append(QualityFinding('info','unknown_tracking_gap','Tracking gap: origin unknown',
                'This historical export contains no frame heartbeat or explicit visibility flags. Missing camera frames cannot be distinguished from omitted marker poses.',
                'Use original camera and visibility logs where available; do not diagnose dirty reflectors from timestamps alone.',tuple(recorded_gaps)))
    if events:
        steps=[b-a for a,b in zip(received,received[1:]) if b>a]
        step=statistics.median(steps) if steps else .01
        groups=[]
        for time,ids in events:
            if groups and time-groups[-1][1]<=1.75*step:
                groups[-1]=(groups[-1][0],time,groups[-1][2]|set(ids))
            else:groups.append((time,time,set(ids)))
        fraction=len(events)/max(1,len(received))
        severity='critical' if fraction>.05 else 'warning' if fraction>.02 else 'info'
        ids=sorted({marker for _,markers in events for marker in markers})
        result.append(QualityFinding(severity,'marker_not_recognized','Marker not recognized in a received frame',
            f'{len(events)} received frames explicitly lack visible marker poses for IDs {ids}. A camera frame exists; this is not a missing-camera-frame diagnosis.',
            'Check marker placement and orientation, occlusion, and dirty/damaged reflectors. Clean/replace fiducials, restore visibility, and repeat if the affected motion cannot be recovered.',
            tuple((a-step/2,b+step/2) for a,b,_ in groups)))
    return result,{'gap_origin_counts':origins,'explicit_invisible_frame_count':len(events),
                   'recorded_frame_gap_count':len(recorded_gaps),
                   'received_frame_count':len(received) if received else None,
                   'visibility_evidence_available':long_form}


def evaluate_quality(
    tracking: RomTrackingData,
    curves: dict[str, tuple[float, ...]],
    mode: str,
    zwick: RomZwickData | None,
    is_active_rom: bool,
    config: dict,
    zwick_expected: bool = True,
) -> tuple[list[QualityFinding], dict]:
    settings = dict(config.get("quality", {}))
    raw, metrics = curve_quality.screen(tracking.times_s, curves, mode, settings)
    findings = [QualityFinding(**item) for item in raw]
    findings,evidence=_tracking_evidence(tracking,findings)
    metrics.update(evidence)
    def add(level, code, title, detail, solution):
        findings.append(QualityFinding(level, code, title, detail, solution))
    metrics["invalid_input_rows"] = tracking.invalid_samples
    if tracking.invalid_samples:
        add("critical", "invalid_input_data", "Invalid acquisition records",
            f"{tracking.invalid_samples} records contain invalid poses, timestamps or inconsistent frame times.",
            "Check marker geometry, export precision and frame timestamps. Recover intact raw records or repeat the test.")
    total = len(tracking.times_s) + tracking.incomplete_samples
    fraction = tracking.incomplete_samples / max(total, 1)
    metrics["incomplete_sample_fraction"] = fraction
    if fraction > .05:
        add("critical", "incomplete_marker_samples", "Too many incomplete five-marker frames",
            f"{fraction:.1%} of frames lack usable poses for every required marker.",
            "Restore all five markers, inspect fixation and visibility, then repeat the test.")
    elif fraction > .02:
        add("warning", "incomplete_marker_samples", "Borderline marker coverage",
            f"{fraction:.1%} of frames are incomplete; verify that no extrema were missed.",
            "Inspect missing intervals against the load curve; repeat if an extremum is unverified.")
    elif fraction:
        add("info", "minor_marker_loss", "Minor marker loss", f"{fraction:.1%} incomplete frames; gap impact is checked separately.",
            "Keep markers visible. No repeat is requested from this percentage alone.")
    errors = [pose.registration_error_mm for poses in tracking.poses.values() for pose in poses
              if math.isfinite(pose.registration_error_mm)]
    maximum = max(errors) if errors else None
    metrics["maximum_registration_error_mm"] = maximum
    if maximum is not None and maximum > float(settings.get("registration_warning_mm", 1.0)):
        critical = maximum > float(settings.get("registration_critical_mm", 2.0))
        add("critical" if critical else "warning", "registration_error", "Marker registration error",
            f"Maximum marker fit error: {maximum:.2f} mm. This is not a calibrated ROM accuracy estimate.",
            "Clean/check fiducials, verify marker geometry and camera visibility; repeat if the fit remains unstable.")
    if is_active_rom:
        edges = _edge_extrema(curves.get("segmental", ()), float(settings.get("edge_peak_fraction", .03)))
        if edges:
            add("critical", "camera_peak_at_edge", "A motion peak may be truncated", ", ".join(edges)+".",
                "Start recording before loading; retain both extrema and the end of the cycle, then repeat.")
    if zwick is None:
        if zwick_expected:
            add("critical", "missing_zwick_data", "Required load data are missing",
                "Loading limits, duration and the complete load cycle cannot be verified.",
                "Recover the matching .tra file and rerun. If unavailable, repeat the test with load export enabled.")
        else:
            add("info", "camera_only_analysis", "Camera-trace assessment only",
                "Load limits and machine motion are not verified; approval applies only to the recorded camera trace.",
                "For complete test approval, analyze the matching .tra file and verify the loading protocol.")
    else:
        finite = all(math.isfinite(v) for values in (zwick.times_s, zwick.loads, zwick.displacements) for v in values)
        if not finite or len(zwick.loads)<10 or len(set(map(len,(zwick.times_s,zwick.loads,zwick.displacements))))!=1:
            add("critical", "invalid_load_data", "Invalid load data", "Load arrays must contain aligned finite samples.",
                "Check the testXpert export and units; recover the complete export or repeat the test.")
        else:
            if any(b<a for a,b in zip(zwick.times_s,zwick.times_s[1:])):
                add("critical", "invalid_load_time", "Load timestamps are not increasing", "Temporal alignment is ambiguous.",
                    "Recover original ordered machine data or repeat with correct timestamps.")
            elif zwick.repeated_time_samples:
                add("info", "repeated_load_timestamps", "Repeated machine timestamps retained",
                    f"{zwick.repeated_time_samples} sample(s) share a timestamp; sample order and all extrema are preserved.",
                    "No time correction or hardware synchronization is inferred.")
            if zwick.removed_leading_result_rows:
                add("info", "export_result_row_excluded", "Leading export result row excluded",
                    "The configured testXpert aggregate row at time zero is not a chronological measurement sample.",
                    "The original TRA file is unchanged; its measured series is retained without rescaling time.")
            camera_duration = tracking.times_s[-1]-tracking.times_s[0] if tracking.times_s else 0
            machine_duration = zwick.times_s[-1]-zwick.times_s[0]
            mismatch = abs(camera_duration-machine_duration)/max(camera_duration,machine_duration,1e-9)
            metrics["duration_mismatch_fraction"] = mismatch
            if mismatch>.10:
                add("critical", "duration_mismatch", "Camera/load durations disagree", f"Duration difference: {mismatch:.1%}.",
                    "Check file pairing, start/stop timing and dropped data. Recover matching traces or repeat the full test.")
            elif mismatch>.07:
                add("warning", "duration_mismatch", "Borderline recording alignment", f"Duration difference: {mismatch:.1%}.",
                    "Confirm that both traces include the same complete loading cycle; repeat if not.")
            if is_active_rom:
                expected = config.get("load_limits",{}).get(mode,())
                if len(expected)==2 and expected[0]<0<expected[1]:
                    achieved = min(max(zwick.loads)/expected[1], min(zwick.loads)/expected[0])
                    metrics["minimum_load_limit_fraction"] = achieved
                    if achieved < .95:
                        add("critical", "load_limit_not_reached", "Prescribed load limit was not reached",
                            f"The less complete loading direction reached {achieved:.1%} of its configured target.",
                            "Verify load units and limits, specimen safety and the complete export; repeat at the prescribed load.")
                    elif achieved < .98:
                        add("warning", "borderline_load_limit", "Load limit is marginally below target",
                            f"The less complete direction reached {achieved:.1%} of target.",
                            "Check the protocol tolerance and raw load trace; repeat if the target was not met.")
                    overshoot = max(max(zwick.loads)/expected[1],min(zwick.loads)/expected[0])
                    metrics["maximum_load_limit_fraction"] = overshoot
                    if overshoot>1.10:
                        add("critical", "load_limit_exceeded", "Load exceeds the prescribed limit",
                            f"One loading direction reached {overshoot:.1%} of its target.",
                            "Check the controller, units and protocol. Inspect specimen integrity before any repeat test.")
                if _edge_extrema(zwick.loads,float(settings.get("edge_peak_fraction",.03))):
                    add("critical", "zwick_peak_at_edge", "Load peak may be truncated", "A load extremum is at a recording boundary.",
                        "Capture both load extrema and the complete cycle, then repeat the test.")
    return findings, metrics


def write_testxpert_rom_files(
    zwick: RomZwickData,
    metrics: RomMetrics,
    mode: str,
    config: dict,
) -> list[Path]:
    if metrics.up_rom is None or metrics.down_rom is None or metrics.zero_point is None:
        return []
    configured = str(config.get("legacy_output_directory", "")).strip()
    output = common._expand_path(configured) if configured else zwick.source.parent
    output.mkdir(parents=True, exist_ok=True)
    pressure = "wp" if _has_token(zwick.source, "wp") else "p"
    values = {
        output / f"{mode}_{pressure}_UpROM.txt": metrics.up_rom,
        output / f"{mode}_{pressure}_DnROM.txt": metrics.down_rom,
        output / f"{mode}_{pressure}_ZeroPoint.txt": metrics.zero_point,
    }
    if mode == "AR" and zwick.vertical_translation:
        values[output / f"{pressure}_Vert_TransSP.txt"] = statistics.mean(
            zwick.vertical_translation
        )
    for path, value in values.items():
        path.write_text(f"{value:.12g}", encoding="ascii")
    return list(values)


def _svg_multi_plot(
    series: Sequence[tuple[str, Sequence[float], Sequence[float], str]],
    box: tuple[float, float, float, float],
    x_label: str,
    y_label: str,
    title: str,
    horizontal_lines: Sequence[float] = (),
    alert_level: str | None = None,
    vertical_bands: Sequence[tuple[float, float, str]] = (),
    split_gap_factor: float | None = None,
    dashed_labels: Sequence[str] = (),
) -> str:
    left, top, width, height = box
    x_values = [value for _, xs, _, _ in series for value in xs if math.isfinite(value)]
    y_values = [value for _, _, ys, _ in series for value in ys if math.isfinite(value)]
    y_values.extend(float(value) for value in horizontal_lines)
    x_min, x_max, x_ticks = common._nice_ticks(x_values)
    y_min, y_max, y_ticks = common._nice_ticks(y_values)

    def sx(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * width

    def sy(value: float) -> float:
        return top + height - (value - y_min) / (y_max - y_min) * height

    parts = [
        f'<text x="{left:.1f}" y="{top - 58 - 22*((len(series)-1)//2):.1f}" class="panel-title">{html.escape(title)}</text>',
        f'<rect x="{left:.1f}" y="{top:.1f}" width="{width:.1f}" height="{height:.1f}" class="plot-bg"/>',
    ]
    for tick in x_ticks:
        x = sx(tick)
        parts.append(f'<line x1="{x:.2f}" y1="{top:.2f}" x2="{x:.2f}" y2="{top + height:.2f}" class="grid" vector-effect="non-scaling-stroke"/>')
        parts.append(f'<text x="{x:.2f}" y="{top + height + 24:.2f}" class="tick" text-anchor="middle">{html.escape(common._format_tick(tick))}</text>')
    for tick in y_ticks:
        y = sy(tick)
        parts.append(f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{left + width:.2f}" y2="{y:.2f}" class="grid" vector-effect="non-scaling-stroke"/>')
        parts.append(f'<text x="{left - 10:.2f}" y="{y + 4:.2f}" class="tick" text-anchor="end">{html.escape(common._format_tick(tick))}</text>')
    for value in horizontal_lines:
        y = sy(float(value))
        parts.append(f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{left + width:.2f}" y2="{y:.2f}" class="limit" vector-effect="non-scaling-stroke"/>')
    for start, end, label in vertical_bands:
        clipped_start = max(x_min, min(x_max, float(start)))
        clipped_end = max(x_min, min(x_max, float(end)))
        band_x = sx(min(clipped_start, clipped_end))
        band_width = max(4.0, abs(sx(clipped_end) - sx(clipped_start)))
        band_class=('visibility-gap' if 'marker not recognized' in label.lower() else
                    'frame-gap' if 'no recorded camera frame' in label.lower() else
                    'unknown-gap' if 'origin unknown' in label.lower() else 'peak-gap')
        parts.append(
            f'<rect x="{band_x:.2f}" y="{top:.2f}" width="{band_width:.2f}" '
            f'height="{height:.2f}" class="{band_class}"><title>{html.escape(label)}</title></rect>'
        )
    frame_class = "frame" if alert_level is None else f"frame alert-{alert_level}"
    parts.append(f'<rect x="{left:.1f}" y="{top:.1f}" width="{width:.1f}" height="{height:.1f}" class="{frame_class}" vector-effect="non-scaling-stroke"/>')

    # Draw dashed fixture traces last so coincident bone/fixture curves remain visible.
    for label, xs, ys, color in sorted(series,key=lambda item:item[0].lower().startswith('clamp') or item[0] in dashed_labels):
        points = [
            (index, x, y)
            for index, (x, y) in enumerate(zip(xs, ys))
            if math.isfinite(x) and math.isfinite(y)
        ]
        gap_after = set()
        if split_gap_factor is not None:
            steps = [
                xs[index + 1] - xs[index]
                for index in range(len(xs) - 1)
                if math.isfinite(xs[index])
                and math.isfinite(xs[index + 1])
                and xs[index + 1] > xs[index]
            ]
            if steps:
                typical_step = statistics.median(steps)
                gap_after = {
                    index
                    for index in range(len(xs) - 1)
                    if math.isfinite(xs[index])
                    and math.isfinite(xs[index + 1])
                    and xs[index + 1] - xs[index] > typical_step * split_gap_factor
                }
        if len(points) > 2500:
            stride = math.ceil(len(points) / 2500)
            # Preserve narrow peaks as well as endpoints in the display reduction.
            selected = set()
            for start in range(0,len(points),stride):
                end=min(len(points),start+stride)
                selected.update((start,end-1,
                    min(range(start,end),key=lambda i:points[i][2]),
                    max(range(start,end),key=lambda i:points[i][2])))
            selected.add(len(points) - 1)
            original_to_point = {original_index: point_index for point_index, (original_index, _x, _y) in enumerate(points)}
            for gap_index in gap_after:
                if gap_index in original_to_point:
                    selected.add(original_to_point[gap_index])
                if gap_index + 1 in original_to_point:
                    selected.add(original_to_point[gap_index + 1])
            points = [points[index] for index in sorted(selected)]
        if len(points) >= 2:
            first_index, first_x, first_y = points[0]
            commands = [f"M {sx(first_x):.2f} {sy(first_y):.2f}"]
            previous_index = first_index
            for original_index, x, y in points[1:]:
                command = "M" if any(
                    gap_index in gap_after
                    for gap_index in range(previous_index, original_index)
                ) else "L"
                commands.append(f"{command} {sx(x):.2f} {sy(y):.2f}")
                previous_index = original_index
            dash = ' stroke-dasharray="10 6"' if label.lower().startswith("clamp") or label in dashed_labels else ""
            parts.append(f'<path class="data-line" d="{" ".join(commands)}" fill="none" stroke="{color}" stroke-width="2" vector-effect="non-scaling-stroke"{dash}/>')
    legend_x = left + 10
    legend_y = top - 20 - 22*((len(series)-1)//2)
    for index, (label, _, _, color) in enumerate(series):
        row = index // 2
        column = index % 2
        x = legend_x + column * (width / 2)
        y = legend_y + row * 22
        dash = ' stroke-dasharray="8 4"' if label.lower().startswith("clamp") or label in dashed_labels else ""
        parts.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + 24:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="3" vector-effect="non-scaling-stroke"{dash}/>')
        parts.append(f'<text x="{x + 31:.1f}" y="{y + 4:.1f}" class="legend">{html.escape(label)}</text>')
    parts.append(f'<text x="{left + width / 2:.1f}" y="{top + height + 55:.1f}" class="axis-label" text-anchor="middle">{html.escape(x_label)}</text>')
    parts.append(f'<text x="{left - 64:.1f}" y="{top + height / 2:.1f}" class="axis-label" text-anchor="middle" transform="rotate(-90 {left - 64:.1f} {top + height / 2:.1f})">{html.escape(y_label)}</text>')
    return "\n".join(parts)


def render_figure(
    output: Path,
    source_name: str,
    data: RomTrackingData,
    curves: dict[str, tuple[float, ...]],
    mode: str,
    axis: Sequence[float],
    markers: dict,
    zwick: RomZwickData | None,
    metrics: RomMetrics | None,
    quality_findings: Sequence[QualityFinding],
    quality_metrics: dict[str, float | None],
    zwick_expected: bool,
    config: dict,
    warnings: Sequence[str],
) -> None:
    from _lib.rom_presentation import write_report
    unit = "°" if mode in ROTATION_MODES else "mm"
    blue, orange = "#2266ad", "#c45c13"
    # Mark each interval once per observation type, even when several rules refer to it.
    bands = {}
    for finding in quality_findings:
        label = finding.title.lower()
        kind = ("marker not recognized" if "marker not recognized" in label else
                "no recorded camera frame" if "no recorded camera frame" in label else
                "origin unknown" if "origin unknown" in label else "other signal fault")
        for start, end in finding.intervals:
            bands[(start, end, kind)] = (start, end, kind)
    gap_factor = float(config.get("quality", {}).get("peak_gap_factor", 1.75))
    def camera_panel(title, items, ylabel):
        series = [(label, data.times_s, curves[key], color) for label, key, color in items]
        return title, _svg_multi_plot(series, (85., 110., 750., 345.),
            "Time [s]", ylabel, title, vertical_bands=tuple(bands.values()), split_gap_factor=gap_factor)
    panels = [camera_panel("Segment motion · cranial relative to caudal", [
        (f"Clamps ({markers['clamp_up']}–{markers['clamp_down']})", "clamp_segmental", blue),
        (f"Vertebrae ({markers['vertebra_cranial']}–{markers['vertebra_caudal']})", "segmental", orange),
    ], f"Segment motion [{unit}]"),
    camera_panel("Upper pair · cranial vertebra / upper clamp", [
        (f"Clamp ({markers['clamp_up']})", "clamp_up", blue),
        (f"Cranial vertebra ({markers['vertebra_cranial']})", "vertebra_cranial", orange),
    ], f"Motion in reference [{unit}]"),
    camera_panel("Lower pair · caudal vertebra / lower clamp", [
        (f"Clamp ({markers['clamp_down']})", "clamp_down", blue),
        (f"Caudal vertebra ({markers['vertebra_caudal']})", "vertebra_caudal", orange),
    ], f"Motion in reference [{unit}]")]
    if zwick is not None and metrics is not None:
        title = "testXpert · load versus displacement"
        panels.append((title, _svg_multi_plot(
            [("testXpert", zwick.displacements, zwick.loads, blue)],
            (85., 110., 750., 345.), f"Displacement [{zwick.displacement_unit}]",
            f"Load [{zwick.load_unit}]", title,
            horizontal_lines=[float(v) for v in config.get("load_limits", {}).get(mode, [])])))
    duration = max(data.times_s)-min(data.times_s)
    stats = [("Vertebral segment ROM", f"{curve_quality.span(curves['segmental']):.3f} {unit}"),
             ("Clamp segment ROM", f"{curve_quality.span(curves['clamp_segmental']):.3f} {unit}"),
             ("Recorded duration", f"{duration:.2f} s"),
             ("Complete marker samples", str(len(data.times_s)))]
    write_report(output, source_name=source_name, mode=mode, markers=markers, axis=axis,
        quality=quality_summary(quality_findings), findings=quality_findings,
        quality_metrics=quality_metrics, camera_only=not zwick_expected, stats=stats,
        panels=panels, warnings=warnings)


def write_motion_csv(
    output: Path,
    times_s: Sequence[float],
    curves: dict[str, tuple[float, ...]],
    mode: str,
) -> None:
    unit = "deg" if mode in ROTATION_MODES else "mm"
    names = (
        "clamp_up",
        "clamp_down",
        "vertebra_cranial",
        "vertebra_caudal",
        "clamp_segmental",
        "segmental",
    )
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", *[f"{name}_{mode}_{unit}" for name in names]])
        writer.writerows(
            zip(times_s, *(curves[name] for name in names))
        )


def write_unusable_report(input_path: Path, output_dir: Path, mode: str, error: Exception) -> None:
    """No valid poses must produce an explicit result, never a blank success."""
    import textwrap
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{input_path.stem}_{mode}_ROM"
    figure = output_dir / (stem+"_figure.svg")
    finding = QualityFinding("critical","invalid_input_data","No usable five-marker curve",
        str(error),"Check all five marker IDs, visibility, geometry and the CSV export. Recover valid raw data or repeat the test.")
    lines = ["Not approved — REPEAT TEST", finding.title, *textwrap.wrap(finding.detail,100),
             *textwrap.wrap("SOLUTION: "+finding.solution,100)]
    height = 65+35*len(lines)
    texts="".join(f'<text x="30" y="{45+35*i}" font-size="{26 if i==0 else 17}">{html.escape(line)}</text>' for i,line in enumerate(lines))
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" style="font-family:Arial;fill:#991b1b"><rect width="100%" height="100%" fill="#fff1f2"/>{texts}</svg>'
    figure.write_text(svg,encoding='utf-8')
    figure.with_suffix('.html').write_text('<!doctype html><meta charset="utf-8"><title>Not approved – ROM quality</title>'+svg,encoding='utf-8')
    report={"status":"ok","analysis":"five_marker_rom","input":str(input_path),"movement":mode,
            "segmental_rom":None,"quality":{**quality_summary([finding]),"findings":[finding.__dict__]},
            "outputs":{"figure_svg":str(figure),"figure_html":str(figure.with_suffix('.html'))}}
    (output_dir/(stem+"_report.json")).write_text(json.dumps(report,indent=2),encoding='utf-8')


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config, config_path = load_config(args.config)
    input_path = args.input.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Atracsys file was not found: {input_path}")
    markers = dict(config["markers"])
    marker_ids = tuple(int(markers[name]) for name in (
        "reference", "clamp_up", "clamp_down", "vertebra_cranial", "vertebra_caudal"
    ))
    registry = common._registry_values(str(config.get("registry_path", "Motion Tracking")))
    if args.ohne_zwick:
        zwick_path = None
        warnings = ["Retrospective camera analysis without a testXpert .tra file."]
    else:
        zwick_path, warnings = _resolve_zwick_path(
            input_path, args.zwick, config, registry
        )
    mode, mode_source = determine_movement(
        args.bewegung, input_path, zwick_path, registry, config
    )
    sign = float(config.get("sign", {}).get(mode, 1.0))
    baseline_seconds = float(config.get("baseline_seconds", 1.0))
    output_dir = args.output_dir.resolve() if args.output_dir else input_path.parent/f"{input_path.stem}_ROM_auswertung"
    try:
        tracking = load_tracking(input_path, marker_ids)
        axis = movement_axis(config, mode, tracking.source_format)
        curves, baseline_count = calculate_motion(tracking,mode,axis,markers,baseline_seconds,sign)
    except (ValueError,OverflowError) as error:
        write_unusable_report(input_path,output_dir,mode,error)
        return 0

    zwick: RomZwickData | None = None
    metrics: RomMetrics | None = None
    is_active_rom = _has_token(input_path, "a") or (zwick_path is not None and _has_token(zwick_path, "a"))
    if zwick_path is not None:
        try:
            zwick = load_zwick(zwick_path, mode, config)
            metrics = find_zero_and_rom(
                zwick,
                mode,
                is_active_rom,
                float(config.get("rotation_legacy_offset_deg", 180000.0)),
            )
        except (OSError, ValueError) as error:
            warnings.append(f"The testXpert file could not be evaluated: {error}")

    written_rom_files: list[Path] = []
    if (
        zwick is not None
        and metrics is not None
        and bool(config.get("write_testxpert_rom_files", True))
        and not args.no_rom_files
    ):
        written_rom_files = write_testxpert_rom_files(zwick, metrics, mode, config)

    quality_findings, quality_metrics = evaluate_quality(
        tracking,
        curves,
        mode,
        zwick,
        is_active_rom,
        config,
        not args.ohne_zwick,
    )

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else input_path.parent / f"{input_path.stem}_ROM_auswertung"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_path = output_dir / f"{input_path.stem}_{mode}_ROM_figure.svg"
    html_path = figure_path.with_suffix(".html")
    motion_path = output_dir / f"{input_path.stem}_{mode}_ROM_bewegung.csv"
    report_path = output_dir / f"{input_path.stem}_{mode}_ROM_report.json"
    render_figure(
        figure_path,
        input_path.name,
        tracking,
        curves,
        mode,
        axis,
        markers,
        zwick,
        metrics,
        quality_findings,
        quality_metrics,
        not args.ohne_zwick,
        config,
        warnings,
    )
    write_motion_csv(motion_path, tracking.times_s, curves, mode)

    report = {
        "status": "ok",
        "analysis": "five_marker_rom",
        "input": str(input_path),
        "input_format": tracking.source_format,
        "config": str(config_path.resolve()),
        "markers": markers,
        "movement": mode,
        "movement_source": mode_source,
        "camera_only": bool(args.ohne_zwick),
        "projection_axis_in_reference_kos": list(common._normalize_axis(axis)),
        "sign": sign,
        "complete_samples": len(tracking.times_s),
        "incomplete_samples": tracking.incomplete_samples,
        "duration_s": tracking.times_s[-1],
        "baseline_seconds": baseline_seconds,
        "baseline_samples": baseline_count,
        "segmental_rom": max(curves["segmental"]) - min(curves["segmental"]),
        "clamp_segmental_rom": max(curves["clamp_segmental"]) - min(curves["clamp_segmental"]),
        "segmental_unit": "deg" if mode in ROTATION_MODES else "mm",
        "zwick": None if zwick is None else {
            "source": str(zwick.source.resolve()),
            "samples": len(zwick.loads),
            "load_column": zwick.load_column,
            "displacement_column": zwick.displacement_column,
            "load_unit": zwick.load_unit,
            "displacement_unit": zwick.displacement_unit,
            "removed_leading_result_rows": zwick.removed_leading_result_rows,
            "repeated_time_samples": zwick.repeated_time_samples,
            "duration_s": zwick.times_s[-1] - zwick.times_s[0],
        },
        "rom_metrics": None if metrics is None else {
            "range": metrics.rom_range,
            "positive_load": metrics.positive_load,
            "negative_load": metrics.negative_load,
            "up_rom": metrics.up_rom,
            "down_rom": metrics.down_rom,
            "zero_point": metrics.zero_point,
            "stored_rotation_offset": metrics.stored_offset,
        },
        "testxpert_rom_files": [str(path.resolve()) for path in written_rom_files],
        "quality": {
            **quality_summary(quality_findings),
            "approval_scope": "camera_trace_only" if args.ohne_zwick else "camera_and_load",
            "full_test_approved": not args.ohne_zwick and quality_summary(quality_findings)["status"] == "ok",
            "metrics": quality_metrics,
            "findings": [
                {
                    "severity": finding.severity,
                    "code": finding.code,
                    "title": finding.title,
                    "detail": finding.detail,
                    "solution": finding.solution,
                    "intervals_s": list(finding.intervals),
                }
                for finding in quality_findings
            ],
        },
        "warnings": warnings,
        "outputs": {
            "figure_svg": str(figure_path.resolve()),
            "figure_html": str(html_path.resolve()),
            "motion_csv": str(motion_path.resolve()),
            "report": str(report_path.resolve()),
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    should_open = bool(config.get("open_figure", True))
    if args.force_open:
        should_open = True
    if args.no_open:
        should_open = False
    if should_open:
        try:
            common.open_figure_in_browser(html_path)
        except OSError as error:
            print(f"Warning: the ROM figure could not be opened: {error}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ROM ANALYSIS ERROR: {error}", file=sys.stderr)
        raise
