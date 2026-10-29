from types import SimpleNamespace

import pytest

from atracsys_sync import github_updates as github


REVISION = "a" * 40


def release(size=32, tag="update-" + REVISION):
    return {"tag_name": tag, "assets": [{"name": github.ASSET, "id": 12, "size": size, "digest": "sha256:" + "b" * 64}]}


@pytest.mark.parametrize("tag", ["latest", "update-abcd", "update-" + "A" * 40, "update-" + "a" * 41])
def test_unpinned_release_tag_is_rejected(monkeypatch, tag):
    monkeypatch.setattr(github, "_api", lambda *args: release(tag=tag))
    with pytest.raises(ValueError):
        github.latest_release("unused", {})


@pytest.mark.parametrize("size", [0, -1, 64 * 1024 * 1024 + 1])
def test_release_asset_bounds(monkeypatch, size):
    monkeypatch.setattr(github, "_api", lambda *args: release(size=size))
    with pytest.raises(ValueError):
        github.latest_release("unused", {})


def test_release_identity_is_full_commit_and_fixed_asset(monkeypatch):
    monkeypatch.setattr(github, "_api", lambda *args: release())
    result = github.latest_release("unused", {})
    assert result["revision"] == REVISION
    assert result["asset"]["id"] == 12


def test_older_release_asset_remains_downloadable_after_project_rename(monkeypatch):
    response = release()
    response['assets'][0]['name'] = github.LEGACY_ASSET
    monkeypatch.setattr(github, '_api', lambda *args: response)
    assert github.latest_release()['asset']['name'] == github.LEGACY_ASSET


@pytest.mark.parametrize("identifier", [True, -1, 0, "12"])
def test_invalid_asset_identifier_is_rejected(monkeypatch, identifier):
    response = release()
    response["assets"][0]["id"] = identifier
    monkeypatch.setattr(github, "_api", lambda *args: response)
    with pytest.raises(ValueError):
        github.latest_release("unused", {})


def test_public_request_never_reads_credentials_or_launches_a_process(monkeypatch):
    import io
    monkeypatch.setenv("GH_TOKEN", "test-value-not-to-send")
    monkeypatch.setenv("GITHUB_TOKEN", "another-test-value")
    monkeypatch.setattr(github.subprocess, "run", lambda *a, **k: pytest.fail("no credential or login commands"))
    calls = []
    class Opener:
        def open(self, request, **kwargs):
            calls.append(request)
            return io.BytesIO(b'{"public":true}')
    monkeypatch.setattr(github.urllib.request, "build_opener", lambda *a: Opener())
    assert github._api(None, "repos/owner/repository") == {"public": True}
    assert all("authorization" not in key.lower() for key in calls[0].headers)
    assert not hasattr(github, "ensure_login")


@pytest.mark.parametrize("url", ["http://github.com/data", "https://attacker.invalid/data", "https://github.com.attacker.invalid/data", "https://user:pass@github.com/data"])
def test_redirect_cannot_downgrade_tls_or_leave_github(url):
    with pytest.raises(ValueError):
        github._validate_url(url)


@pytest.mark.parametrize("content", [b"short", b"this is longer than advertised"])
def test_download_size_is_bounded_independently_of_remote_headers(monkeypatch, tmp_path, content):
    import io
    monkeypatch.setattr(github, "_open", lambda *a: io.BytesIO(content))
    with pytest.raises(ValueError):
        github._download_asset(None, {"id": 1, "size": 10}, tmp_path / "update.zip")


def test_release_digest_is_returned_for_archive_verification(monkeypatch, tmp_path):
    def download(app, asset, path, env):
        path.write_bytes(b"archive contents")
    monkeypatch.setattr(github, "_download_asset", download)
    archive, expected = github.download_release("unused", {"asset": release()["assets"][0]}, tmp_path, {})
    assert archive.read_bytes() == b"archive contents"
    assert expected == "b" * 64


@pytest.mark.parametrize("digest", ["sha256:nope", "sha256:" + "A" * 64, ""])
def test_bad_or_absent_checksum_never_authorizes_update(monkeypatch, tmp_path, digest):
    monkeypatch.setattr(github, "_download_asset", lambda app, asset, path, env: path.write_bytes(b"data"))
    asset = release()["assets"][0]
    asset["digest"] = digest
    with pytest.raises(ValueError):
        github.download_release("unused", {"asset": asset}, tmp_path, {})
