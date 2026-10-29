import json

import pytest
import requests

from szlcsc_mcp.downloader import sync_datasheets
from szlcsc_mcp.models import Part


class DummySession:
    pass


class DummyClient:
    session = DummySession()
    timeout = 1

    def get_part(self, identifier):
        return Part(lcsc_code="C1", mpn="ABC", brand="X", datasheet_url=None)


def test_sync_writes_incremental_manifest_without_datasheet(tmp_path):
    result = sync_datasheets(DummyClient(), ["C1", "C1"], tmp_path)
    assert result["ok"] is True
    assert result["complete"] is False
    assert result["processed"] == 1
    assert result["no_datasheet"] == 1
    assert result["manifest_items"] == 1
    manifest_path = tmp_path / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["total_items"] == 1
    assert len(manifest["items"]) == 1
    assert len(manifest["parts"]) == 1
    assert manifest["items"][0]["status"] == "no_datasheet"


def test_empty_sync_is_not_reported_as_success(tmp_path):
    result = sync_datasheets(DummyClient(), ["", "   "], tmp_path)
    assert result["ok"] is False
    assert result["processed"] == 0
    assert "No non-empty identifiers" in result["error"]
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["total_items"] == 0


class RejectingClient:
    session = DummySession()
    timeout = 1

    def get_part(self, identifier):
        return Part(
            lcsc_code="C9",
            mpn="FAILPART",
            brand="Example",
            datasheet_url="https://unapproved.example.com/fail.pdf",
        )


def test_sync_surfaces_per_part_download_failure_details(tmp_path):
    result = sync_datasheets(RejectingClient(), ["C9"], tmp_path)
    assert result["ok"] is False
    assert result["errors"] == 1
    assert result["failure_summary"] == {"download_datasheet": 1}
    failure = result["failed_results"][0]
    assert failure["requested_identifier"] == "C9"
    assert failure["lcsc_code"] == "C9"
    assert failure["mpn"] == "FAILPART"
    assert failure["datasheet_host"] == "unapproved.example.com"
    assert failure["error_stage"] == "download_datasheet"
    assert failure["error_type"] == "SZLCSCError"
    assert "unapproved host" in failure["error_message"]

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    row = manifest["parts"]["C9"]
    assert row["error_stage"] == "download_datasheet"
    assert row["datasheet_host"] == "unapproved.example.com"


class _RelativeFakeResponse:
    status_code = 200
    def __init__(self, url, body=b"%PDF-relative"):
        self.url = url
        self.body = body
        self.headers = {"Content-Length": str(len(body))}

    def raise_for_status(self):
        return None

    def iter_content(self, _chunk_size):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _RelativeFakeSession:
    def get(self, url, **_kwargs):
        return _RelativeFakeResponse(url)


class RelativeDatasheetClient:
    timeout = 1

    def __init__(self):
        self.session = _RelativeFakeSession()

    def get_part(self, identifier):
        return Part(
            lcsc_code="C2040",
            mpn="RP2040",
            brand="Raspberry Pi(树莓派)",
            datasheet_url="upload/public/pdf/source/20210621/C2040_96F425825606B7957D87DD32E2512CA9.pdf",
        )


def test_sync_accepts_relative_szlcsc_datasheet_path(tmp_path):
    result = sync_datasheets(RelativeDatasheetClient(), ["C2040"], tmp_path, force=True)
    assert result["ok"] is True
    assert result["downloaded"] == 1
    assert result["errors"] == 0
    row = result["results"][0]
    assert row["datasheet_url"].startswith("upload/public/pdf/source/")
    assert row["datasheet_url_resolved"].startswith("https://atta.szlcsc.com/upload/public/pdf/source/")
    assert row["datasheet_host"] == "atta.szlcsc.com"


@pytest.mark.parametrize("preview", [False, True])
def test_library_sync_pages_all_501_targets(monkeypatch, tmp_path, preview):
    from szlcsc_mcp import server, server_base

    index = {"parts": {f"C{i:03d}": {"mpn": "PART", "stock": 1} for i in range(501)}}
    calls = []

    class Client:
        def get_part(self, identifier, *, refresh=False):
            calls.append((identifier, refresh))
            return Part(lcsc_code=identifier, mpn="PART", stock=2)

    monkeypatch.setattr(server, "client", Client())
    monkeypatch.setattr(server_base, "client", server.client)
    monkeypatch.setattr(server, "load_index", lambda root: index)
    monkeypatch.setattr(server_base, "load_index", lambda root: index)
    writes = []

    def upsert(root, part, review, *, dedupe_cad):
        writes.append(part.lcsc_code)
        return {"entry": {"mpn": part.mpn, "stock": part.stock}}

    monkeypatch.setattr(server_base, "upsert_part", upsert)
    tool = server.szlcsc_library_sync_preview if preview else server_base.szlcsc_library_sync
    first = json.loads(tool(str(tmp_path)))
    assert first["count"] == 500
    assert (first["remaining_count"], first["has_more"], first["next_offset"]) == (1, True, 500)
    second = json.loads(tool(str(tmp_path), offset=first["next_offset"]))
    assert (second["count"], second["remaining_count"], second["has_more"], second["next_offset"]) == (1, 0, False, None)
    assert second["results"][0]["identifier"] == "C500"
    assert len(calls) == 501 and all(refresh for _, refresh in calls)
    assert len(writes) == (0 if preview else 501)
    if preview:
        assert first["library_write_performed"] is False


@pytest.mark.parametrize("preview", [False, True])
def test_library_sync_refreshes_cached_inventory(monkeypatch, tmp_path, preview):
    from szlcsc_mcp import server, server_base
    from szlcsc_mcp.cache import SqliteCache
    from szlcsc_mcp.client import SZLCSCClient
    from szlcsc_mcp.freshness import bypass
    from szlcsc_mcp.library import load_index, upsert_part

    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    calls = []

    def get(*args, **kwargs):
        calls.append(args[0])
        result = requests.Response()
        result.status_code = 200
        result._content = json.dumps({"code": 200, "result": {"productList": [
            {"code": "C1", "stockNumber": len(calls), "model": "PART"}
        ]}}).encode()
        result.url = "https://so.szlcsc.com/test"
        return result

    monkeypatch.setattr(client.session, "get", get)
    assert client.get_part("C1").stock == 1
    assert client.get_part("C1").stock == 1
    library_root = tmp_path / "library"
    upsert_part(library_root, Part(lcsc_code="C1", mpn="PART", stock=1), None)
    before = load_index(library_root)
    monkeypatch.setattr(server, "client", client)
    monkeypatch.setattr(server_base, "client", client)
    tool = server.szlcsc_library_sync_preview if preview else server_base.szlcsc_library_sync
    result = json.loads(tool(str(library_root)))
    assert result["results"][0]["changes"]["stock"] == {"old": 1, "new": 2}
    assert len(calls) == 2
    assert bypass.get() is False
    if preview:
        assert load_index(library_root) == before
        assert result["library_write_performed"] is False
    else:
        assert load_index(library_root)["parts"]["C1"]["stock"] == 2


@pytest.mark.parametrize("preview", [False, True])
def test_library_sync_explicit_pages_preserve_errors_and_boundaries(monkeypatch, tmp_path, preview):
    from szlcsc_mcp import server, server_base
    from szlcsc_mcp.client import SZLCSCError

    index = {"parts": {"C1": {"mpn": "PART"}}}
    calls = []

    class Client:
        def get_part(self, identifier, *, refresh=False):
            calls.append(identifier)
            if identifier == "bad":
                raise SZLCSCError("lookup failed")
            return Part(lcsc_code=identifier, mpn="PART")

    monkeypatch.setattr(server, "client", Client())
    monkeypatch.setattr(server_base, "client", server.client)
    monkeypatch.setattr(server, "load_index", lambda root: index)
    monkeypatch.setattr(server_base, "load_index", lambda root: index)
    monkeypatch.setattr(server_base, "upsert_part", lambda *args, **kwargs: {"entry": {}})
    tool = server.szlcsc_library_sync_preview if preview else server_base.szlcsc_library_sync
    targets = ["bad", "C2", "C1"]
    first = json.loads(tool(str(tmp_path), identifiers=targets, limit=1))
    assert first["results"][0]["ok"] is False
    assert first["next_offset"] == 1 and first["remaining_count"] == 2
    second = json.loads(tool(str(tmp_path), identifiers=targets, offset=1, limit=1))
    assert second["results"][0]["identifier"] == "C2"
    if preview:
        assert first["results"][0]["status"] == "lookup_error"
        assert second["results"][0]["status"] == "not_in_library"
    last = json.loads(tool(str(tmp_path), identifiers=targets, offset=2, limit=1))
    assert last["results"][0]["identifier"] == "C1"
    assert last["has_more"] is False and last["next_offset"] is None
    beyond = json.loads(tool(str(tmp_path), identifiers=targets, offset=99))
    assert beyond["count"] == beyond["remaining_count"] == 0
    assert calls == targets
    for kwargs in ({"offset": -1}, {"limit": 0}, {"limit": 501}, {"limit": True}):
        with pytest.raises(ValueError):
            tool(str(tmp_path), **kwargs)
