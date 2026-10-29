from pathlib import Path

from szlcsc_mcp.downloader import _readme
from szlcsc_mcp.models import Part, PriceTier


def test_batch_does_not_count_partial_download_as_success(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from szlcsc_mcp import downloader

    client = SimpleNamespace(get_part=lambda value: Part(lcsc_code="C1", mpn="PART", image_url="https://alimg.szlcsc.com/a.jpg"), session=None, timeout=1)
    def fail_download(*args, **kwargs):
        raise OSError("upstream image unavailable")
    monkeypatch.setattr(downloader, "_download", fail_download)
    result = downloader.download_parts(client, ["C1"], tmp_path)
    assert result["successful"] == 0
    assert not result["results"][0]["ok"] and not result["results"][0]["complete"]
    assert result["results"][0]["errors"]


def test_readme_marks_unknown_not_zero():
    p = Part(lcsc_code="C1", mpn="TEST", price_tiers=[PriceTier(1, 2.0)])
    text = _readme(p)
    assert "国内库存：未知" in text
    assert "小批量参考单价：未知" in text

import pytest

from szlcsc_mcp.client import SZLCSCError
from szlcsc_mcp.downloader import _download


class _FakeResponse:
    status_code = 200
    def __init__(self, url: str, body: bytes = b"%PDF-test"):
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


class _FakeSession:
    def __init__(self, final_url: str | None = None, body: bytes = b"%PDF-test"):
        self.final_url = final_url
        self.body = body

    def get(self, url, **_kwargs):
        return _FakeResponse(self.final_url or url, self.body)


def test_official_ti_datasheet_host_is_allowed_for_pdf(tmp_path):
    target = tmp_path / "opa333.pdf"
    _download(
        _FakeSession(),
        "https://www.ti.com/lit/ds/symlink/opa333.pdf",
        target,
        1,
        pdf=True,
    )
    assert target.read_bytes().startswith(b"%PDF-")


def test_external_vendor_host_is_not_allowed_for_non_pdf_asset(tmp_path):
    with pytest.raises(SZLCSCError, match="unapproved host"):
        _download(
            _FakeSession(body=b"image"),
            "https://www.ti.com/logo.png",
            tmp_path / "logo.png",
            1,
            pdf=False,
        )


def test_redirect_final_host_is_revalidated(tmp_path):
    target = tmp_path / "redirect.pdf"
    with pytest.raises(SZLCSCError, match="unapproved host"):
        _download(
            _FakeSession(final_url="https://evil.example/payload.pdf"),
            "https://www.ti.com/lit/ds/symlink/opa333.pdf",
            target,
            1,
            pdf=True,
        )
    assert not target.exists()
    assert not (tmp_path / "redirect.pdf.part").exists()


def test_relative_szlcsc_datasheet_path_is_resolved_to_attachment_cdn(tmp_path):
    requested = []

    class CaptureSession:
        def get(self, url, **_kwargs):
            requested.append(url)
            return _FakeResponse(url, b"%PDF-relative")

    target = tmp_path / "rp2040.pdf"
    _download(
        CaptureSession(),
        "upload/public/pdf/source/20210621/C2040_96F425825606B7957D87DD32E2512CA9.pdf",
        target,
        1,
        pdf=True,
    )
    assert requested == [
        "https://atta.szlcsc.com/upload/public/pdf/source/20210621/C2040_96F425825606B7957D87DD32E2512CA9.pdf"
    ]
    assert target.read_bytes().startswith(b"%PDF-")


def test_nonforce_download_replaces_invalid_existing_pdf(tmp_path):
    target = tmp_path / "part.pdf"
    target.write_bytes(b"not a pdf")
    _download(_FakeSession(body=b"%PDF-new"), "https://atta.szlcsc.com/a.pdf", target, 1, pdf=True, force=False)
    assert target.read_bytes() == b"%PDF-new"


def test_nonforce_download_does_not_accept_directory_as_pdf(tmp_path):
    target = tmp_path / "part.pdf"
    target.mkdir()
    with pytest.raises((SZLCSCError, OSError)):
        _download(_FakeSession(), "https://atta.szlcsc.com/a.pdf", target, 1, pdf=True, force=False)


def test_existing_valid_pdf_is_reused_and_failed_refresh_preserves_it(tmp_path):
    target = tmp_path / "part.pdf"
    target.write_bytes(b"%PDF-existing")
    class NoNetwork:
        def get(self, *args, **kwargs): raise AssertionError("valid cache should be reused")
    _download(NoNetwork(), "https://atta.szlcsc.com/a.pdf", target, 1, pdf=True, force=False)
    with pytest.raises(SZLCSCError, match="not a PDF"):
        _download(_FakeSession(body=b"invalid"), "https://atta.szlcsc.com/a.pdf", target, 1, pdf=True, force=True)
    assert target.read_bytes() == b"%PDF-existing"
