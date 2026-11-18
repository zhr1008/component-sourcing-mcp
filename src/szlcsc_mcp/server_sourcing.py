"""Focused sourcing tools, resources and prompts registered on the shared server."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field

from .errors import SourcingError, error_payload
from .jlcpcb import JLCPCBClient
from .models import Part
from .schemas import ConstraintText, Identifier, LibraryType, Page, Quantity, Query, ResultLimit
from .selection import SelectionCriteria, selection_criteria, shortlist
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
    return _quantity_quote(part, quantity)


def _quantity_quote(part: Part, quantity: int) -> dict:
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
def szlcsc_select(
    request: Query,
    previous: SelectionCriteria | None = None,
    conditions: Annotated[dict[ConstraintText, ConstraintText | None], Field(max_length=30)] | None = None,
    catalog_query: Query | None = None,
    quantity: Quantity | None = None,
    refresh: bool = False,
    preferred_brand: Annotated[ConstraintText, Field(max_length=40, description="证据相当时优先的品牌，保留其他品牌")] | None = None,
) -> dict:
    """中文器件选型首选入口。最多 5 个候选，每项提供型号、立创料号、关键参数、证据和 AD Comment/Description。

    从需求提取类目、封装和常见规格。显式条件可通过 conditions 补充，null 删除对应条件。
    接着改一项时，将上次返回的 selection 原样传入 previous，request 填本轮改动。新器件需求直接开始新查询。
    仅在给出采购数量时查询候选的最新零售库存及对应报价。价格适用数量未知时，展示价单独列出。
    """
    try:
        criteria, notes = selection_criteria(
            request, previous, conditions=conditions, catalog_query=catalog_query, quantity=quantity,
            preferred_brand=preferred_brand,
        )
        result = shortlist(client, criteria, refresh=refresh)
    except SourcingError as exc:
        return error_payload(exc)
    result["notes"] = notes
    if criteria.quantity is not None:
        for row in result["results"]:
            code = row["lcsc_code"]
            try:
                part = client.get_part(code, refresh=True, include_static=False)
                listing_mpn = row["evidence"]["listing_mpn"]
                if ((part.lcsc_code or "").casefold() != code.casefold()
                        or (part.mpn or "").casefold() != listing_mpn.casefold()):
                    raise SourcingError("商品详情与候选型号不一致", code="IDENTITY_MISMATCH")
                row["procurement"] = {
                    **_quantity_quote(part, criteria.quantity),
                    "stock": part.stock,
                    "total_stock": part.total_stock,
                    "warehouse_stock": part.warehouse_stock,
                    "in_stock": part.in_stock,
                }
            except SourcingError as exc:
                row["procurement"] = error_payload(exc)
    return result


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
        "For a shortlist from natural-language requirements start with szlcsc_select. Preserve its selection "
        "as previous when the user changes a condition. Present each AD Comment and Description verbatim. "
        "For exploratory family discovery use szlcsc_discover: inspect coverage, query_errors, "
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
    """按中文需求给出最多五个立创候选，支持继续筛选与按数量查价。"""
    return (
        f"按以下需求选型\n{requirements}\n"
        "调用 szlcsc_select。明确的硬条件可用 conditions 补充，优先展示有目录依据的匹配。"
        "把结果的 selection 留在对话上下文。用户只改一项时，将上次 selection 原样作为 previous，"
        "request 写本轮改动。新器件需求直接开始新查询。"
        "提供最多五个候选，每行写完整型号、立创料号、关键参数、依据、AD Comment 和 AD Description。"
        "照录两项 AD 字段。只在用户已给采购数量时查库存和报价。"
        "依据不足的候选标待核实，指出缺哪项。注明已应用条件以及排除候选最多的条件。"
        "涉及资质、引脚兼容性和使用边界时核对厂家资料。"
    )
