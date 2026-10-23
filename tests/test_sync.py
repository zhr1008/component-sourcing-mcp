import json

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
