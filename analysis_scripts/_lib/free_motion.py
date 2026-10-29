"""General relative rigid-body motion; no anatomical assumptions or acceptance rules."""
import copy
import math
import statistics
import re

import auswertung as algebra


OUTPUTS = {
    'tx': ('Displacement X', 'mm'), 'ty': ('Displacement Y', 'mm'),
    'tz': ('Displacement Z', 'mm'), 'displacement': ('Distance from baseline', 'mm'),
    'path': ('Cumulative relative path', 'mm'),
    'rotation_axis': ('Projected rotation', 'deg'),
    'rotation_total': ('Net rotation magnitude', 'deg'),
}


def marker_selector(value):
    """Legacy geometry IDs or explicit geometry:tracking identity; never an ordinal."""
    text = str(value)
    if type(value) not in (int, str) or not re.fullmatch(r'[1-9]\d*(?::[0-9]+)?', text):
        raise ValueError('Use a geometry ID or geometry:tracking ID.')
    parts = text.split(':')
    if any(str(int(p)) != p or int(p) > 2147483647 for p in parts):
        raise ValueError('Invalid geometry/tracking ID.')
    return int(text) if len(parts) == 1 else text


def validate_profile(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != 1:
        raise ValueError('Unsupported free-analysis profile format (version 1 required).')
    if set(value)-{'version','name','markers','reference','baseline_seconds','comparisons'}:
        raise ValueError('Unknown profile field; check the profile format.')
    profile = copy.deepcopy(value)
    name = str(profile.get('name', '')).strip()
    if not name or len(name) > 80:
        raise ValueError('A profile name of 1–80 characters is required.')
    profile['name'] = name
    markers = profile.get('markers')
    if not isinstance(markers, dict) or not 2 <= len(markers) <= 128:
        raise ValueError('Define 2–128 marker IDs and names.')
    normalized = {}
    for marker, label in markers.items():
        marker_id = marker_selector(marker)
        label = str(label).strip()
        if not label or len(label) > 80:
            raise ValueError('Each marker needs a name of 1–80 characters.')
        normalized[str(marker_id)] = label
    if len({name.casefold() for name in normalized.values()}) != len(normalized):
        raise ValueError('Marker names must be distinct.')
    profile['markers'] = normalized
    for key in normalized:
        if ':' in key and key.split(':')[0] in normalized:
            raise ValueError('Do not mix a geometry-only selector with an explicit instance of the same geometry.')
    ref = profile.get('reference')
    if marker_selector(ref) != ref or str(ref) not in normalized:
        raise ValueError('Select a reference marker from the defined marker IDs.')
    baseline = profile.get('baseline_seconds', 1.)
    if type(baseline) not in (int, float) or not math.isfinite(baseline) or not 0 <= baseline <= 300:
        raise ValueError('Baseline duration must be between 0 and 300 seconds.')
    profile['baseline_seconds'] = float(baseline)
    pairs = profile.get('comparisons')
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 32:
        raise ValueError('Define 1–32 comparisons.')
    names = set()
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError('Invalid comparison.')
        if set(pair)-{'name','anchor','target','outputs','axis'}:
            raise ValueError('Unknown comparison field; check the profile format.')
        pair['name'] = str(pair.get('name', '')).strip()
        if not pair['name'] or len(pair['name']) > 80 or pair['name'].casefold() in names:
            raise ValueError('Comparison names must be distinct and contain 1–80 characters.')
        names.add(pair['name'].casefold())
        for field in ('anchor', 'target'):
            if marker_selector(pair.get(field)) != pair[field] or str(pair[field]) not in normalized:
                raise ValueError('Each comparison needs two defined marker IDs.')
        if pair['anchor'] == pair['target']:
            raise ValueError('A marker cannot be compared with itself.')
        chosen = pair.get('outputs')
        if not isinstance(chosen, list) or not chosen or any(not isinstance(key, str) or key not in OUTPUTS for key in chosen) or len(set(chosen)) != len(chosen):
            raise ValueError('Choose at least one distinct supported motion quantity per comparison.')
        axis = pair.get('axis', [0, 0, 1])
        if (not isinstance(axis, (list, tuple)) or len(axis) != 3 or
                any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e6 for v in axis)):
            raise ValueError('The rotation axis needs three finite components.')
        pair['axis'] = list(algebra._normalize_axis(axis))
    return profile


def vector(matrix, values):
    return tuple(sum(a*b for a, b in zip(row, values)) for row in matrix)


def relative_motion(data, reference, anchor, target, baseline_seconds, axis):
    """Anchor-local relative motion, expressed in instantaneous reference axes.

    q = R_anchor^T (p_target-p_anchor). Displacement is R_ref^T R_anchor (q-q0).
    Delta rotation is log((R_anchor^T R_target) S0^T), transformed by the same basis.
    Thus rigid marker offsets cancel under common movement, including rotation.
    Rotation vectors are shortest-branch quantities, not independent Euler angles.
    """
    if len(data.times_s) < 2 or any(b <= a for a, b in zip(data.times_s, data.times_s[1:])):
        raise ValueError('At least two complete samples with strictly increasing timestamps are required. Acquisition order was retained.')
    count = sum(t <= baseline_seconds for t in data.times_s) if baseline_seconds > 0 else 1
    count = max(1, count)
    ref, first, second = (data.poses[i] for i in (reference, anchor, target))
    local, orientations, bases = [], [], []
    for r, a, b in zip(ref, first, second):
        a_t = algebra.transpose(a.rotation)
        local.append(vector(a_t, tuple(y-x for x, y in zip(a.translation_mm, b.translation_mm))))
        orientations.append(algebra.multiply(a_t, b.rotation))
        bases.append(algebra.multiply(algebra.transpose(r.rotation), a.rotation))
    origin = tuple(statistics.mean(p[i] for p in local[:count]) for i in range(3))
    rotation0 = algebra.average_rotation(orientations[:count])
    result = {key: [] for key in OUTPUTS}
    path = 0.
    axis = algebra._normalize_axis(axis)
    branch_limit = False
    for index, (point, orientation, basis) in enumerate(zip(local, orientations, bases)):
        shift = vector(basis, tuple(p-o for p, o in zip(point, origin)))
        delta = algebra.multiply(orientation, algebra.transpose(rotation0))
        rotvec = vector(basis, algebra.quaternion_to_rotvec(algebra.matrix_to_quaternion(delta)))
        angle = math.degrees(math.sqrt(sum(v*v for v in rotvec)))
        if index:
            path += math.dist(point, local[index-1])
        for key, value in zip(('tx', 'ty', 'tz'), shift):
            result[key].append(value)
        result['displacement'].append(math.sqrt(sum(v*v for v in shift)))
        result['path'].append(path)
        result['rotation_axis'].append(math.degrees(sum(a*b for a, b in zip(axis, rotvec))))
        result['rotation_total'].append(angle)
        branch_limit |= angle >= 175.
        if index:
            step = algebra.multiply(orientation, algebra.transpose(orientations[index-1]))
            step_vector = algebra.quaternion_to_rotvec(algebra.matrix_to_quaternion(step))
            branch_limit |= math.degrees(math.sqrt(sum(v*v for v in step_vector))) >= 175.
    # A path across an unobserved interval is only the observed endpoint distance.
    if not all(math.isfinite(value) for values in result.values() for value in values):
        raise ValueError('Relative motion is nonfinite. Check pose values and units; no motion values were fabricated.')
    return {key: tuple(value) for key, value in result.items()}, count, branch_limit
