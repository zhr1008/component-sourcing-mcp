"""Focused sourcing tools, resources and prompts registered on the shared server."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field

from .jlcpcb import JLCPCBClient
from .schemas import Identifier, LibraryType, Page, Quantity, Query, ResultLimit
from .server_base import client, mcp

jlc_client = JLCPCBClient(
    client.cache,
    timeout=client.timeout,
    delay=client.request_delay,
    retries=client.retries,
)
READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


@mcp.tool(annotations=READ_ONLY)
def szlcsc_get_stock(identifier: Identifier, refresh: bool = True) -> dict:
    """按精确 C 编号/MPN 查询国内 SZLCSC 零售库存、分仓库存和 MOQ。refresh=true 绕过 TTL 缓存；
    与 JLCPCB 贴片库存池不同。缺失库存保持 null；响应含缓存时间和补全失败警告。
    """
    part = client.get_part(identifier, refresh=refresh, include_static=False)
    return {
        "lcsc_code": part.lcsc_code,
        "mpn": part.mpn,
        "stock": part.stock,
        "total_stock": part.total_stock,
        "warehouse_stock": part.warehouse_stock,
        "in_stock": part.in_stock,
        "min_order_qty": part.min_order_qty,
        "full_pack_qty": part.full_pack_qty,
        "order_multiple": part.order_multiple,
        "source": part.source,
        "inventory_pool": "SZLCSC_domestic_retail",
        "data_freshness": part.data_freshness,
        "procurement": part.procurement,
        "warnings": part.warnings,
    }


@mcp.tool(annotations=READ_ONLY)
def szlcsc_get_pricing(
    identifier: Identifier, quantity: Quantity = 1, refresh: bool = True
) -> dict:
    """查询国内 CNY 阶梯价格，并按 quantity、MOQ、订购倍数计算实际订购量及对应单价。
    默认绕过缓存；阶梯未知/数量未覆盖时单价和总价为 null，禁止用美元价或批量最低价代替。
    """
    part = client.get_part(identifier, refresh=refresh, include_static=False)
    order_qty = max(quantity, part.min_order_qty or quantity)
    multiple = part.order_multiple or 1
    if multiple > 1:
        order_qty = ((order_qty + multiple - 1) // multiple) * multiple
    tiers = [
        t
        for t in part.price_tiers
        if t.currency in {"CNY", "RMB", "¥", "￥"}
        and t.min_qty is not None
        and t.min_qty <= order_qty
        and (t.max_qty is None or t.max_qty >= order_qty)
        and t.unit_price_cny is not None
    ]
    selected = max(tiers, key=lambda t: t.min_qty) if tiers else None
    price = selected.unit_price_cny if selected else None
    return {
        "lcsc_code": part.lcsc_code,
        "mpn": part.mpn,
        "currency": "CNY",
        "requested_quantity": quantity,
        "order_quantity": order_qty,
        "min_order_qty": part.min_order_qty,
        "full_pack_qty": part.full_pack_qty,
        "order_multiple": part.order_multiple,
        "unit_price_cny": price,
        "line_total_cny": str(Decimal(str(price)) * order_qty)
        if price is not None
        else None,
        "price_tiers": part.to_dict()["price_tiers"],
        "procurement": part.procurement,
        "advertised_unit_price_cny": part.procurement.get("advertised_unit_price_cny"),
        "source": part.source,
        "data_freshness": part.data_freshness,
        "warnings": part.warnings,
    }


@mcp.tool(annotations=READ_ONLY)
def szlcsc_jlcpcb_search(
    query: Query,
    library_type: LibraryType = "all",
    package: str | None = None,
    min_stock: Annotated[int, Field(ge=0, le=1_000_000_000)] = 0,
    page: Page = 1,
    page_size: ResultLimit = 10,
    refresh: bool = False,
) -> dict:
    """检索 JLCPCB 贴片装配库；按 basic 基础库、preferred 优选库、extended 扩展库或 no_fee 筛选。
    支持封装、最低贴片库存与分页。no_fee 每页合并 basic/preferred 两个分页窗口，最多 2×page_size 条。
    此数据源独立于国内 SZLCSC 零售库；不会把装配库存或未知币种价格当作国内库存/CNY。
    """
    return jlc_client.search(
        query,
        library_type=library_type,
        package=package,
        min_stock=min_stock,
        page=page,
        page_size=page_size,
        refresh=refresh,
    )


@mcp.tool(annotations=READ_ONLY)
def szlcsc_jlcpcb_categories(refresh: bool = False) -> dict:
    """获取 JLCPCB 装配库的分类/子分类层级、ID 和数量。分类字典缓存 24 小时，可 refresh 强制更新。"""
    return jlc_client.categories(refresh=refresh)


@mcp.resource("szlcsc://sourcing-guide", mime_type="text/plain")
def sourcing_guide() -> str:
    return (
        "For requirement/family discovery start with szlcsc_discover: inspect coverage, query_errors, "
        "package/pin variants and unknown requirements. Empty results never establish market absence. "
        "Use szlcsc_search / szlcsc_parametric_search for focused searches, then exact szlcsc_get_part. "
        "Before ordering use szlcsc_get_stock and szlcsc_get_pricing with refresh=true. "
        "CNY retail and JLCPCB assembly are different markets and stock pools. "
        "Null means unknown. Parametric matches and CAD assets never replace manufacturer datasheet verification. "
        "A cache hit is a dated snapshot. Public advertised prices without quantity intervals are not quantity quotes; "
        "procurement distinguishes them and supersedes disagreeing legacy API values."
    )


@mcp.prompt()
def select_domestic_component(requirements: str) -> str:
    """Guide a domestic component selection with explicit evidence and final fresh stock/price checks."""
    return (
        f"Select a domestic component for: {requirements}\n"
        "Separate hard constraints from preferences; do not invent a stock/price filter. Start szlcsc_discover "
        "with functional terms, then inspect returned families and package/pin variants; use family_queries to expand. "
        "Read coverage/errors/remaining candidates, never call one empty query market absence. Translate electrical constraints and inspect "
        "constraint_match.all_matched and unknown fields, resolve exact C-codes, verify manufacturer datasheets, "
        "then refresh domestic stock and CNY pricing for the requested quantity. Report unknowns, advertisement-only prices, "
        "and conflicts. Q1 suffixes or catalog text do not certify automotive qualification. "
        "Only use JLCPCB assembly tools when assembly library availability is requested."
    )
