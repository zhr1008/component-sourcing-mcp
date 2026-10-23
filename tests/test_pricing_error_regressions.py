"""Regressions captured from the installed rc.1 live functional test."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastmcp import Client

from szlcsc_mcp.errors import SourcingError
from szlcsc_mcp.normalize import part_from_dict

FIXTURE = Path(__file__).parent / "fixtures/c2040_pricing_20261020.json"


def raw_part():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_full_pack_quantity_is_not_a_minimum_purchase():
    part = part_from_dict(raw_part())
    assert part.min_order_qty is None
    assert part.full_pack_qty == 3400
    assert part.order_multiple == 1


def test_explicit_minimum_purchase_wins_over_whole_pack():
    part = part_from_dict({**raw_part(), "minBuyNumber": 1})
    assert part.min_order_qty == 1
    assert part.full_pack_qty == 3400


def test_upstream_minus_one_price_ceiling_is_open_ended():
    part = part_from_dict(raw_part())
    assert len(part.price_tiers) == 6
    assert part.price_tiers[-1].min_qty == 1000
    assert part.price_tiers[-1].max_qty is None
    assert part.price_tiers[-1].unit_price_cny == 4.43


@pytest.mark.parametrize(
    "quantity,unit,total",
    [(10, 5.74, "57.40"), (1000, 4.43, "4430.00"), (3400, 4.43, "15062.00")],
)
def test_actual_c2040_quantity_quote_via_real_mcp(monkeypatch, quantity, unit, total):
    from szlcsc_mcp import server_sourcing
    from szlcsc_mcp.server import mcp

    monkeypatch.setattr(
        server_sourcing.client,
        "get_part",
        Mock(return_value=part_from_dict(raw_part())),
    )

    async def run():
        async with Client(mcp) as client:
            result = await client.call_tool(
                "szlcsc_get_pricing", {"identifier": "C2040", "quantity": quantity}
            )
            data = result.structured_content
            assert not result.is_error
            assert data["order_quantity"] == quantity
            assert data["min_order_qty"] is None
            assert data["full_pack_qty"] == 3400
            assert data["unit_price_cny"] == unit
            assert data["line_total_cny"] == total

    asyncio.run(run())


@pytest.mark.parametrize(
    "code,retryable,wait",
    [
        ("ACCESS_DENIED", False, 60),
        ("RATE_LIMITED", True, 30),
        ("NETWORK_ERROR", True, None),
    ],
)
@pytest.mark.parametrize(
    "tool,arguments,owner,method",
    [
        ("szlcsc_get_stock", {"identifier": "C2040"}, "client", "get_part"),
        (
            "szlcsc_get_pricing",
            {"identifier": "C2040", "quantity": 10},
            "client",
            "get_part",
        ),
        ("szlcsc_jlcpcb_categories", {}, "jlc_client", "categories"),
    ],
)
def test_domain_error_survives_fastmcp_toolerror_wrapper(
    monkeypatch, code, retryable, wait, tool, arguments, owner, method
):
    from szlcsc_mcp import server_sourcing
    from szlcsc_mcp.server import mcp

    failure = SourcingError(
        "public upstream failure", code=code, retryable=retryable, retry_after=wait
    )
    monkeypatch.setattr(
        getattr(server_sourcing, owner), method, Mock(side_effect=failure)
    )

    async def run():
        async with Client(mcp) as client:
            result = await client.call_tool(tool, arguments, raise_on_error=False)
            assert result.is_error
            error = result.structured_content["error"]
            assert error["code"] == code
            assert error["message"] == "public upstream failure"
            assert error["retryable"] is retryable
            if wait is not None:
                assert error["retry_after_seconds"] == wait
            assert not (await client.call_tool("szlcsc_cache_status")).is_error

    asyncio.run(run())
