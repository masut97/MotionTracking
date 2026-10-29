import json
from pathlib import Path

import pytest

from atracsys_sync.version_store import retire_versions
from atracsys_sync.installation_cleanup import remove_old_installation


def version(store, char):
    root=store/(char*12);app=root/'App'
    (app/'sync_extension').mkdir(parents=True)
    (app/'sync_extension/build.json').write_text(json.dumps({'source_revision':char*40,'project':'MotionTracking'}))
    (app/'config.json').write_text(json.dumps({'measurement_directory':str(store.parent/'real-data')}))
    (app/'native').mkdir();(app/'native/old.dll').write_bytes(b'old runtime')
    (app/'geometries').mkdir();(app/'geometries/cal.json5').write_bytes(b'unique calibration')
    (root/'measurements').mkdir()
    for suffix in ('.csv','.json','.log'):
        (root/'measurements'/('real'+suffix)).write_bytes(b'protected specimen data'+suffix.encode())
    return app


def test_retire_only_inactive_managed_versions_and_preserve_data(tmp_path):
    store=tmp_path/'versions';active=version(store,'a');old=version(store,'b');busy=version(store,'c')
    (store/'unknown').mkdir()
    result=retire_versions(active,running=[busy/'native/old.dll'])
    assert len(result)==1 and result[0]['status']=='removed'
    assert not old.parent.exists()
    assert active.exists() and busy.exists() and (store/'unknown').exists()
    saved=tmp_path/'PreservedData'/('b'*12)/'files'
    assert (saved/'App/geometries/cal.json5').read_bytes()==b'unique calibration'
    assert (saved/'measurements/real.csv').read_bytes()==b'protected specimen data.csv'
    assert not (saved/'App/native/old.dll').exists()
    assert retire_versions(active,running=[busy/'native/old.dll'])==[]


def test_active_data_reference_prevents_removing_old_version(tmp_path):
    store=tmp_path/'versions';active=version(store,'a');old=version(store,'b')
    (active/'config.json').write_text(json.dumps({'measurement_directory':str(old.parent/'measurements')}))
    assert retire_versions(active,running=[])==[]
    assert old.exists()


def test_wrong_parent_or_running_installation_cannot_be_removed(tmp_path):
    old=version(tmp_path/'versions','b').parent
    with pytest.raises(ValueError):remove_old_installation(old,tmp_path,tmp_path/'saved',running=[])
    with pytest.raises(ValueError):remove_old_installation(old,old.parent,tmp_path/'saved',running=[old/'App/anything.exe'])
    assert old.exists()


def test_copy_failure_preserves_original_installation(tmp_path,monkeypatch):
    old=version(tmp_path/'versions','b').parent
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr('atracsys_sync.installation_cleanup.shutil.copy2',fail)
    with pytest.raises(OSError):remove_old_installation(old,old.parent,tmp_path/'saved',running=[])
    assert old.exists() and (old/'measurements/real.csv').is_file()


def test_unknown_directory_and_existing_preservation_are_never_deleted(tmp_path):
    unknown=tmp_path/'unknown';unknown.mkdir();(unknown/'important.txt').write_text('keep')
    with pytest.raises(ValueError):remove_old_installation(unknown,tmp_path,tmp_path/'saved',running=[])
    old=version(tmp_path/'versions','b').parent
    (tmp_path/'saved'/old.name).mkdir(parents=True)
    with pytest.raises(FileExistsError):remove_old_installation(old,old.parent,tmp_path/'saved',running=[])
    assert unknown.exists() and old.exists()
