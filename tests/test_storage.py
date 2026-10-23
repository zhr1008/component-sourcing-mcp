from pathlib import Path

from szlcsc_mcp.models import Part
from szlcsc_mcp.storage import archive_existing_files, asset_basename, category_segments


def _part(category: str = "电压基准芯片") -> Part:
    return Part(
        lcsc_code="C5291487",
        mpn="TPR3333-S3TR",
        category=category,
        package="SOT-23G-3",
    )


def test_asset_basename_uses_category_mpn_and_c_code():
    assert asset_basename(_part()) == "电压基准芯片-TPR3333-S3TR-C5291487"


def test_category_hierarchy_is_preserved_when_present():
    assert category_segments("集成电路(IC) / 电源管理(PMIC) / 电压基准芯片") == [
        "集成电路(IC)",
        "电源管理(PMIC)",
        "电压基准芯片",
    ]


def test_category_alias_slashes_do_not_create_fake_hierarchy():
    assert category_segments("单片机(MCU/MPU/SOC)") == ["单片机(MCU_MPU_SOC)"]


def test_rp2040_basename_keeps_full_category_without_stray_parenthesis():
    part = Part(
        lcsc_code="C2040",
        mpn="RP2040",
        category="单片机(MCU/MPU/SOC)",
        package="QFN-56",
    )
    assert asset_basename(part) == "单片机(MCU_MPU_SOC)-RP2040-C2040"


def test_archive_creates_classified_and_flat_views(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sch = source / "C5291487.SchLib"
    pcb = source / "C5291487.PcbLib"
    step = source / "model.step"
    sch.write_bytes(b"sch")
    pcb.write_bytes(b"pcb")
    step.write_bytes(b"step")

    result = archive_existing_files(
        _part(),
        tmp_path / "library",
        schlibs=[sch],
        pcblibs=[pcb],
        step_files=[step],
    )

    base = "电压基准芯片-TPR3333-S3TR-C5291487"
    classified = tmp_path / "library" / "classified"
    flat = tmp_path / "library" / "flat"
    assert (classified / "schlib" / "电压基准芯片" / f"{base}.SchLib").read_bytes() == b"sch"
    assert (classified / "pcblib" / "电压基准芯片" / f"{base}.PcbLib").read_bytes() == b"pcb"
    assert (classified / "3d" / "电压基准芯片" / f"{base}.step").read_bytes() == b"step"
    assert (flat / f"{base}.SchLib").is_file()
    assert (flat / f"{base}.PcbLib").is_file()
    assert (flat / f"{base}.step").is_file()
    assert result["basename"] == base


def test_archive_datasheet_creates_classified_and_flat_pdf(tmp_path):
    from szlcsc_mcp.downloader import archive_part_assets

    class Response:
        status_code = 200
        url = "https://atta.szlcsc.com/upload/public/test.pdf"
        headers = {"Content-Length": "13"}

        def raise_for_status(self):
            return None

        def iter_content(self, _size):
            yield b"%PDF-testdata"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Session:
        def get(self, _url, **_kwargs):
            return Response()

    class Client:
        session = Session()
        timeout = 1

    part = _part()
    part.datasheet_url = "https://atta.szlcsc.com/upload/public/test.pdf"
    result = archive_part_assets(Client(), part, tmp_path / "library")
    base = asset_basename(part)
    assert (tmp_path / "library" / "classified" / "datasheets" / "电压基准芯片" / f"{base}.pdf").is_file()
    assert (tmp_path / "library" / "flat" / f"{base}.pdf").is_file()
    assert result["errors"] == []
