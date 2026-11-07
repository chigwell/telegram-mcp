"""Signed download and upload URLs for a client on another machine."""

import inspect
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from telegram_mcp import media_links, runtime
from telegram_mcp.tools import media
from telegram_mcp.transport.http import create_streamable_http_app

BASE = "https://tg.example/acct"
_prepare_upload = inspect.unwrap(media.prepare_upload)
_download_media = inspect.unwrap(media.download_media)


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = (tmp_path / "files").resolve()
    root.mkdir()
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root, tmp_path / "other"])
    monkeypatch.setenv("TELEGRAM_MEDIA_BASE_URL", BASE + "/")
    return root


@pytest.fixture
def client():
    with TestClient(create_streamable_http_app(runtime.mcp)) as c:
        yield c


def _local(url):
    parts = urlsplit(url)
    return parts.path.removeprefix("/acct") + "?" + parts.query


@pytest.mark.asyncio
async def test_upload_round_trip_lands_under_first_root(root, client):
    slot = json.loads(await _prepare_upload("../../etc/report 1.pdf"))
    assert slot["file_path"].startswith(str(root / "uploads"))
    assert slot["file_path"].endswith("/report 1.pdf")

    response = client.put(_local(slot["url"]), content=iter([b"%PDF-", b"bytes"]))

    assert response.status_code == 201
    assert response.json() == {"file_path": slot["file_path"], "size": 10}
    assert Path(slot["file_path"]).read_bytes() == b"%PDF-bytes"
    assert not Path(slot["file_path"] + ".part").exists()


@pytest.mark.asyncio
async def test_upload_url_takes_one_upload(root, client):
    url = _local(json.loads(await _prepare_upload("a.txt"))["url"])
    assert client.put(url, content=b"one").status_code == 201
    assert client.put(url, content=b"two").status_code == 404


@pytest.mark.asyncio
async def test_bad_upload_urls_are_refused(root, client):
    slot = json.loads(await _prepare_upload("a.txt"))
    parts = urlsplit(slot["url"])
    path = parts.path.removeprefix("/acct")
    rel = path.removeprefix(media_links.UPLOAD_ROUTE_PREFIX)
    assert (
        client.put(path + "?" + parts.query.replace("sig=", "sig=0"), content=b"x").status_code
        == 404
    )
    assert (
        client.put(path.replace("a.txt", "b.txt") + "?" + parts.query, content=b"x").status_code
        == 404
    )
    past = int(time.time()) - 1
    expired = media_links._signature("upload", rel, past)
    assert client.put(f"{path}?expires={past}&sig={expired}", content=b"x").status_code == 404

    future = int(time.time()) + 60
    escaping = "../escape.txt"
    sig = media_links._signature("upload", escaping, future)
    assert media_links.resolve_upload(escaping, str(future), sig, root) is None
    # A download signature never authorises an upload.
    sig = media_links._signature("media", rel, future)
    assert media_links.resolve_upload(rel, str(future), sig, root) is None


@pytest.mark.asyncio
async def test_oversize_upload_is_refused(root, client, monkeypatch):
    monkeypatch.setitem(media.MAX_FILE_BYTES, "send_file", 4)
    slot = json.loads(await _prepare_upload("big.bin"))
    assert client.put(_local(slot["url"]), content=b"12345").status_code == 413
    assert not Path(slot["file_path"]).exists()
    assert not Path(slot["file_path"] + ".part").exists()


@pytest.mark.asyncio
async def test_no_upload_without_base_root_or_name(root, monkeypatch):
    assert not (await _prepare_upload("..")).startswith("{")
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [])
    assert not (await _prepare_upload("a.txt")).startswith("{")
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.delenv("TELEGRAM_MEDIA_BASE_URL")
    assert not (await _prepare_upload("a.txt")).startswith("{")


def test_download_url_serves_file_and_refuses_bad_links(root, client, tmp_path):
    (root / "downloads").mkdir()
    file = root / "downloads" / "photo 1.jpg"
    file.write_bytes(b"jpeg")
    url = media_links.download_url(file, root)
    assert url.startswith(BASE + "/media/downloads/photo%201.jpg?")

    response = client.get(_local(url))
    assert response.status_code == 200 and response.content == b"jpeg"

    assert client.get(_local(url).replace("sig=", "sig=0")).status_code == 404
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    assert media_links.download_url(outside, root) is None
    future = int(time.time()) + 60
    sig = media_links._signature("media", "../secret.txt", future)
    assert media_links.resolve_download("../secret.txt", str(future), sig, root) is None


@pytest.mark.asyncio
async def test_download_media_reports_the_url(root, monkeypatch):
    target = root / "downloads" / "doc.pdf"
    target.parent.mkdir()
    message = SimpleNamespace(id=7, media=object(), file=SimpleNamespace(size=3))

    class Client:
        async def get_messages(self, entity, ids):
            return message

        async def download_media(self, target_media, file, progress_callback):
            path = Path(file).with_suffix(".pdf")
            path.write_bytes(b"pdf")
            return str(path)

    async def resolve_entity(chat_id, cl):
        return "entity"

    async def resolve_path(**kwargs):
        return Path(kwargs["raw_path"] or target).resolve(), None

    monkeypatch.setattr(media, "get_client", lambda account=None: Client())
    monkeypatch.setattr(media, "resolve_entity", resolve_entity)
    monkeypatch.setattr(media, "_resolve_writable_file_path", resolve_path)

    result = await _download_media(123, 7, file_path=str(target))

    assert result.startswith(f"Media downloaded to {target}. From another machine, fetch it from ")
    assert f"{BASE}/media/downloads/doc.pdf?" in result


def test_sweep_removes_only_stale_upload_slots(root):
    uploads = root / "uploads"
    for slot, age in (("old", 3 * media_links.URL_TTL_SECONDS), ("new", 0)):
        (uploads / slot).mkdir(parents=True)
        (uploads / slot / "f").write_bytes(b"x")
        stamp = time.time() - age
        os.utime(uploads / slot, (stamp, stamp))
    media_links.sweep_expired_uploads(root)
    assert [p.name for p in uploads.iterdir()] == ["new"]
