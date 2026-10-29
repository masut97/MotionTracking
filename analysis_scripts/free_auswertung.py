"""Freely configured marker comparisons for current Atracsys CSV recordings."""
import argparse
import csv
import hashlib
import json
import statistics
import sys
import uuid
import webbrowser
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
if str(DIRECTORY) not in sys.path:
    sys.path.insert(0, str(DIRECTORY))

import rom_auswertung as tracking
from _lib.free_motion import OUTPUTS, relative_motion, validate_profile, marker_selector

PROFILE_PATH = DIRECTORY / 'free_analysis_config.local.json'


def load_profile(path=None):
    path = Path(path) if path else PROFILE_PATH
    if not path.is_file():
        raise ValueError('No free-analysis profile is configured. Open “Freie Zuordnung” in the application and save a profile first.')
    return validate_profile(json.loads(path.read_text(encoding='utf-8-sig')))


def analyze(source, profile, output=None):
    profile = validate_profile(profile)
    source = Path(source).resolve()
    with source.open(encoding='utf-8-sig', newline='') as stream:
        headers = next(csv.reader(stream), [])
    if 'geometry_id' not in headers:
        raise ValueError('Free analysis requires a current CSV containing explicit geometry_id values. Anonymous historical column positions cannot safely identify arbitrary markers.')
    with source.open(encoding='utf-8-sig', newline='') as stream:
        first = next((row for row in csv.DictReader(stream) if tracking._frame_key_and_time(row)), None)
    if first is None:
        raise ValueError('No finite acquisition timestamp was found.')
    elapsed = bool(first.get('time_since_start_s', '').strip())
    time_adjustment = tracking._frame_key_and_time(first)[1] if elapsed else 0.
    time_origin = 'recording start (original time_since_start_s)' if elapsed else 'first recorded frame (monotonic-clock difference)'
    results = []
    for index, pair in enumerate(profile['comparisons'], 1):
        ref, anchor, target = profile['reference'], pair['anchor'], pair['target']
        result = dict(index=index, comparison=pair, issues=[], times_s=[], curves={}, statistics={})
        try:
            data = tracking._load_current_tracking(source, sorted({ref, anchor, target}, key=str),
                explicit_instances=True, allow_geometry_tracking_changes=True)
        except ValueError:
            result['issues'].append(f'No complete unambiguous valid samples for reference {ref}, anchor {anchor}, target {target}. Check geometry/tracking IDs, marker visibility and pose validity. Multiple instances require explicit tracking IDs.')
            results.append(result)
            continue
        try:
            curves, baseline_count, branch_limit = relative_motion(data, ref, anchor, target, profile['baseline_seconds'], pair['axis'])
        except (ValueError, ArithmeticError) as error:
            result['issues'].append(str(error)); results.append(result); continue
        offset = data.received_times_s[0]
        times = tuple(t-offset+time_adjustment for t in data.times_s)
        intervals = [b-a for a, b in zip(data.times_s, data.times_s[1:])]
        dt = statistics.median(intervals)
        gaps = [(a, b) for a, b in zip(times, times[1:]) if b-a > 1.75*dt]
        received = [t-offset+time_adjustment for t in data.received_times_s]
        unseen = {t-offset+time_adjustment for t,_ids in data.unseen_marker_events}
        bands = []
        for a,b in gaps:
            inside = [t for t in received if a<t<b]
            label = ('No recorded camera frame' if not inside else
                     'Marker not recognized' if all(t in unseen for t in inside) else 'Gap origin unknown')
            bands.append((a,b,label))
        if data.incomplete_samples:
            result['issues'].append(f'{data.incomplete_samples} received frames lack one or more required valid marker poses. Restore visibility; check marker IDs and placement. Unrelated comparisons are evaluated independently.')
        if data.invalid_samples:
            result['issues'].append(f'{data.invalid_samples} invalid input rows. Inspect the original recording/export; no invalid pose was filled in.')
        if data.unseen_marker_events:
            ids = sorted({marker for _time,markers in data.unseen_marker_events for marker in markers}, key=str)
            result['issues'].append(f'Received frames explicitly flag these required marker IDs as not visible: {ids}. Check placement, occlusion and reflectors. This is distinct from absent saved camera frames.')
        if any(b <= a for a,b in zip(data.received_times_s, data.received_times_s[1:])):
            result['issues'].append('The recorded frame heartbeat has repeated or backwards timestamps. Inspect acquisition/export order; the original ordering was retained.')
        if gaps:
            result['issues'].append(f'{len(gaps)} gaps between complete observations. Curves are broken at gaps. The cumulative path is a lower-bound endpoint estimate across missing observations; extrema may be unobserved. Check acquisition and visibility logs.')
        if times[0] > 1.75*dt:
            result['issues'].append(f'Complete observations start {times[0]:.3g} s after the stated time origin. Baseline starts at the first complete sample.')
        if branch_limit:
            result['issues'].append('Rotation approaches the 180-degree shortest-rotation branch. Signed projections may jump; multi-turn rotation is not resolved. No unwrapping was applied.')
        if baseline_count == len(times):
            result['issues'].append('The baseline covers every complete sample. Shorten the baseline or record a static baseline before the motion.')
        chosen = {key: curves[key] for key in pair['outputs']}
        result.update(times_s=times, curves=chosen, gaps=gaps, gap_bands=bands, baseline_samples=baseline_count,
                      complete_samples=len(times), incomplete_samples=data.incomplete_samples,
                      statistics={key: dict(minimum=min(values), maximum=max(values), observed_range=max(values)-min(values),
                                            final=values[-1], unit=OUTPUTS[key][1]) for key, values in chosen.items()})
        results.append(result)
    output = (Path(output) if output else source.parent / (source.stem+'_free_analysis')) / ('run_'+uuid.uuid4().hex[:12])
    output.mkdir(parents=True, exist_ok=False)
    digest = hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    report = dict(analysis='free_marker_motion', input=str(source), profile=profile, profile_sha256=digest,
                  identity_policy='Geometry-only selectors require one unambiguous valid pose per received frame; tracking-ID changes are allowed. Explicit geometry:tracking selectors never substitute another ID. Physical replacements sharing one geometry cannot be distinguished.',
                  protocol_approval=False, reference_axes='instantaneous recorded reference-marker axes',
                  time_origin=time_origin,
                  rotation_convention='shortest rotation vector; axis projection is not an Euler angle; net magnitude 0–180 deg',
                  baseline='Mean relative position and mean relative orientation from the first complete sample through the chosen duration; zero seconds uses the first sample.',
                  results=results)
    figure = output/(source.stem+'_free_report.html')
    from _lib.free_presentation import write_report
    write_report(figure, report)
    for result in results:
        if not result['curves']:
            continue
        path = output/f'comparison_{result["index"]:02d}_motion.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            keys = list(result['curves'])
            writer.writerow(['time_since_recording_start_s' if elapsed else 'time_since_first_recorded_frame_s', *[key+'_'+OUTPUTS[key][1] for key in keys]])
            writer.writerows(zip(result['times_s'], *(result['curves'][key] for key in keys)))
    (output/(source.stem+'_free_report.json')).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    (output/'profile_snapshot.json').write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding='utf-8')
    return figure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args(argv)
    result = analyze(args.input, load_profile(args.config), args.output_dir)
    print(json.dumps({'status': 'ok', 'figure_html': str(result)}, ensure_ascii=False))
    if not args.no_open:
        webbrowser.open(result.resolve().as_uri())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
