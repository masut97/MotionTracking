"""Visible live identities for free analysis; no inferred instance ordering."""


def selection_choices(visible, preferred=False):
    """Offer geometry-only binding only when exactly one live instance exists.

    Tracking IDs are observation IDs on some SDKs, not persistent identities.
    A geometry selector is evaluated for uniqueness again in every CSV frame.
    This does not identify physical objects across sequential replacements.
    """
    groups = {}
    for key, label in visible.items():
        groups.setdefault(str(key).split(':')[0], []).append((str(key), label))
    choices = {} if preferred else dict(visible)
    for geometry, instances in groups.items():
        if len(instances) == 1:
            choices[geometry] = f'Marker {geometry} · eindeutig je Bild · Tracking-ID darf wechseln'
        elif preferred:
            choices.update(instances)
    return choices


def csv_marker_choices(path):
    """Detect simultaneous ambiguity, not the number of IDs across a recording."""
    import csv
    groups, ambiguous, frame_ids = {}, set(), {}
    with open(path, encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'geometry_id', 'frame_counter'} <= set(reader.fieldnames or ()):
            raise ValueError('Eine Marker-Aufnahme mit geometry_id und frame_counter wählen.')
        previous = None
        for row in reader:
            # Frame keys include the recorded timestamp to distinguish counter
            # wraps/reconnections. Do not treat tracking IDs as frame counters.
            key = (row.get('frame_counter'), row.get('device_timestamp_us') or
                   row.get('computer_monotonic_s') or row.get('time_since_start_s'))
            if key != previous:
                frame_ids = {}
                previous = key
            geometry = row.get('geometry_id', '').strip()
            if row.get('visible') != '1' or not geometry:
                continue
            track = row.get('tracking_id', '').strip()
            selector = geometry + (':' + track if track else '')
            groups.setdefault(geometry, set()).add(selector)
            current = frame_ids.setdefault(geometry, set())
            current.add(selector)
            if len(current) > 1:
                ambiguous.add(geometry)
    result = {}
    for geometry, identities in groups.items():
        if geometry not in ambiguous:
            result[geometry] = f'Marker {geometry}'
        else:
            for identity in sorted(identities):
                result[identity] = f'Instanz {identity}'
    if len(result) > 128:
        raise ValueError('Mehr als 128 Instanz-IDs in dieser Aufnahme. Mehrfach vorkommende Geometrien mit wechselnden Tracking-IDs können nicht dauerhaft identifiziert werden.')
    return result
def visible_marker_choices(window):
    if not window.status_store.get().connected:
        return {}
    states = window.marker_registry.states()
    states = states.values() if isinstance(states, dict) else states
    dismissed = getattr(window, '_dismissed_geometry_ids', set())
    dismissed_instances = getattr(window, '_dismissed_markers', set())
    result = {}
    for state in states:
        pose = state.pose
        if (not state.visible or state.age_ms() > 500 or not pose.valid
                or pose.geometry_id in dismissed
                or getattr(state, 'identity_key', (pose.geometry_id, state.identity_id)) in dismissed_instances):
            continue
        key = f'{pose.geometry_id}:{pose.tracking_id}'
        result[key] = f'Geometry {pose.geometry_id} · Instance {state.identity_id} · Tracking {pose.tracking_id}'
    return result
