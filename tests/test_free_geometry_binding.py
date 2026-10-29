import csv
import importlib
import json
from pathlib import Path

import pytest

from atracsys_sync.marker_selection import selection_choices, csv_marker_choices


def test_single_geometry_choices_survive_volatile_ids_but_duplicates_do_not_collapse():
    first = {'299:124688':'Ref', '302:124689':'Base', '351:124690':'Object'}
    later = {'299:200001':'Ref', '302:200002':'Base', '351:200003':'Object'}
    assert set(selection_choices(first, preferred=True)) == {'299','302','351'}
    assert selection_choices(first, preferred=True) == selection_choices(later, preferred=True)
    later['351:200004'] = 'Another'
    choices = selection_choices(later, preferred=True)
    assert '351' not in choices
    assert {'351:200003','351:200004'} <= set(choices)


def recording(path, free, duplicated=False):
    fields = ['frame_counter','device_timestamp_us','computer_monotonic_s','geometry_id','tracking_id',
              'visible','x_mm','y_mm','z_mm',*free.tracking.common.ROTATION_COLUMNS]
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for i in range(20):
            for g, x in [(299,0), (302,i), (351,3*i)] + ([(351,50)] if duplicated and i==8 else []):
                row = dict(frame_counter=i, device_timestamp_us=i*100000,
                           computer_monotonic_s=100+i*.1, geometry_id=g,
                           tracking_id=i*1000+g+(1 if x==50 else 0), visible=1,x_mm=x,y_mm=0,z_mm=0)
                row.update(zip(free.tracking.common.ROTATION_COLUMNS,[1,0,0,0,1,0,0,0,1]))
                writer.writerow(row)


@pytest.mark.parametrize('duplicated', [False, True])
def test_geometry_analysis_and_csv_import_with_tracking_id_change_every_frame(tmp_path, monkeypatch, duplicated):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'analysis_scripts'))
    free = importlib.import_module('free_auswertung')
    path = tmp_path/'volatile.csv'
    recording(path, free, duplicated)
    original = path.read_bytes()
    profile = dict(version=1, name='Stable geometry choices', reference=299, baseline_seconds=0,
                   markers={'299':'Reference', '302':'Base', '351':'Object'},
                   comparisons=[dict(name='Object vs base', anchor=302, target=351, outputs=['tx'], axis=[0,0,1])])
    figure = free.analyze(path, profile)
    result = json.loads(figure.with_suffix('.json').read_text())['results'][0]
    assert result['complete_samples'] == 20-int(duplicated)
    assert result['curves']['tx'][-1] == pytest.approx(38)
    assert path.read_bytes() == original
    choices = csv_marker_choices(path)
    if duplicated:
        assert '351' not in choices
    else:
        assert set(choices) == {'299','302','351'}
    profile['markers']['351:1'] = 'Alias of object'
    with pytest.raises(ValueError, match='Do not mix'):
        free.validate_profile(profile)
