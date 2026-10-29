"""Anonymous HTTPS downloads for public GitHub releases."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = "masut97/MotionTracking"
ASSET = "MotionTracking-update.zip"
LEGACY_ASSET = "AtracsysTracking-update.zip"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not (host in {"api.github.com", "github.com"} or host.endswith(".githubusercontent.com"))):
        raise ValueError("Ungültige Adresse für öffentliche Updates.")


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _open(url, accept):
    _validate_url(url)
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "MotionTracking-updater"})
    # Never look up credentials, launch a login command or send Authorization.
    opener = urllib.request.build_opener(SecureRedirect())
    try:
        return opener.open(request, timeout=45)
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError("Öffentliches GitHub-Update nicht erreichbar. Internetverbindung prüfen und später erneut versuchen.") from error


def _api(app, endpoint, env=None):
    with _open("https://api.github.com/" + endpoint, "application/vnd.github+json") as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("GitHub-Antwort ist zu groß.")
    return json.loads(data)


def latest_release(app=None, env=None):
    release = _api(app, f"repos/{REPOSITORY}/releases/latest", env)
    tag = release.get("tag_name", "")
    if not re.fullmatch(r"update-[0-9a-f]{40}", tag):
        raise ValueError("Die neueste GitHub-Version enthält kein unterstütztes Update.")
    assets = {}
    for item in release.get("assets", []):
        if item.get("name") in assets:
            raise ValueError("Mehrdeutige Dateien im GitHub-Update.")
        assets[item.get("name")] = item
    asset = assets.get(ASSET) or assets.get(LEGACY_ASSET)
    if (not asset or type(asset.get("id")) is not int or asset["id"] <= 0
            or type(asset.get("size")) is not int or not 0 < asset["size"] <= 64 * 1024 * 1024):
        raise ValueError("Geprüftes Update-Paket fehlt oder ist zu groß.")
    return {"revision": tag[7:], "asset": asset, "checksum": assets.get(asset['name'] + ".sha256")}


def _download_asset(app, asset, destination, env=None):
    if (type(asset.get("id")) is not int or asset["id"] <= 0
            or type(asset.get("size")) is not int or not 0 < asset["size"] <= 64 * 1024 * 1024):
        raise ValueError("Ungültige GitHub-Datei.")
    url = f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset['id']}"
    with _open(url, "application/octet-stream") as response, Path(destination).open("xb") as stream:
        received = 0
        while block := response.read(min(65536, asset["size"] - received + 1)):
            received += len(block)
            if received > asset["size"]:
                raise ValueError("Update-Download überschreitet die angekündigte Größe.")
            stream.write(block)
    if received != asset["size"]:
        raise ValueError("Update-Download ist unvollständig.")


def download_release(app, release, directory, env=None):
    archive = Path(directory) / ASSET
    _download_asset(app, release["asset"], archive, env)
    digest = release["asset"].get("digest", "")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        expected = digest[7:]
    else:
        checksum = release.get("checksum")
        if not checksum or not 0 < checksum.get("size", 0) < 1024:
            raise ValueError("GitHub-Prüfsumme fehlt.")
        path = archive.with_suffix(".zip.sha256")
        _download_asset(app, checksum, path, env)
        expected = path.read_text(encoding="utf-8").split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Ungültige GitHub-Prüfsumme.")
    return archive, expected
