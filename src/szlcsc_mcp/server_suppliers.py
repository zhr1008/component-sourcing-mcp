"""Supplier-neutral tools; legacy domestic and CAD tools retain their contracts."""

from typing import Annotated

from pydantic import Field, StringConstraints

from .schemas import Constraints, Page, Quantity, Query
from .server_base import client, mcp
from .server_sourcing import READ_ONLY, jlc_client
from .supplier_models import ProviderName
from .suppliers import build_registry

registry = build_registry(client, jlc_client)
Providers = Annotated[
    list[ProviderName],
    Field(
        min_length=1,
        max_length=5,
        description="Distinct supplier names. Omit to attempt all implemented suppliers; absent credentials are reported as partial failures.",
    ),
]
SupplierIdentifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    Field(
        description="Exact provider-specific SKU or exact MPN; C-codes belong only to SZLCSC/JLCPCB"
    ),
]
FilterText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
PerProviderLimit = Annotated[
    int,
    Field(
        ge=1,
        le=50,
        description="Page size for EACH provider; combined results can contain up to 4 times this count. No fetched results are silently truncated.",
    ),
]


@mcp.tool(annotations={**READ_ONLY, "openWorldHint": False})
def component_providers() -> dict:
    """列出供应商实现/凭据配置状态、官方申请渠道及所需环境变量名，不返回密钥。
    configured 表示已配置，last_request_status 才记录本进程实际查询结果。华秋需取得官方协议后适配。
    """
    return registry.status()


@mcp.tool(annotations=READ_ONLY)
def component_search(
    query: Query,
    providers: Providers | None = None,
    manufacturer: FilterText | None = None,
    package: FilterText | None = None,
    constraints: Constraints | None = None,
    in_stock: bool = False,
    page: Page = 1,
    limit: PerProviderLimit = 5,
    refresh: bool = False,
) -> dict:
    """统一检索立创、JLCPCB、贸泽和 DigiKey。支持封装、制造商、电气条件与每供应商分页。
    先用 component_providers 检查凭据；缺少密钥不会阻断其他供应商，必须检查 complete/provider_errors。
    返回紧凑器件身份及供应商独立报价；金额保留原币种，未知库存不是 0。
    """
    return registry.search(
        query,
        providers=providers,
        manufacturer=manufacturer,
        package=package,
        constraints=constraints,
        in_stock=in_stock,
        page=page,
        limit=limit,
        refresh=refresh,
    )


@mcp.tool(annotations=READ_ONLY)
def component_get(
    provider: ProviderName,
    identifier: SupplierIdentifier,
    manufacturer: FilterText | None = None,
    package: FilterText | None = None,
    refresh: bool = True,
) -> dict:
    """按供应商 SKU 或明确 MPN 精确查询器件、包装变体、参数、库存与原币阶梯价。
    多个身份返回 AMBIGUOUS_PART；可用 manufacturer/package 或更精确 SKU 消除歧义。
    """
    return registry.get(
        provider,
        identifier,
        manufacturer=manufacturer,
        package=package,
        refresh=refresh,
    )


@mcp.tool(annotations=READ_ONLY)
def component_offers(
    mpn: SupplierIdentifier,
    quantity: Quantity = 1,
    providers: Providers | None = None,
    manufacturer: FilterText | None = None,
    package: FilterText | None = None,
    refresh: bool = True,
) -> dict:
    """按明确 MPN 汇总多个供应商的库存、MOQ、订购倍数和数量对应的阶梯报价。
    同制造商/MPN/封装、币种及市场的报价分别分组；不换汇、不合并库存，运费税费未知。
    SKU 应先通过 component_get 解析为 MPN；部分供应商失败时检查 complete/provider_errors。
    """
    return registry.offers(
        mpn,
        quantity=quantity,
        providers=providers,
        manufacturer=manufacturer,
        package=package,
        refresh=refresh,
    )


@mcp.resource("components://supplier-guide", mime_type="text/plain")
def supplier_guide() -> str:
    return (
        "Use component_providers for official application channels and credential variable names. "
        "Fill a private copy of suppliers.example.json and set COMPONENT_SUPPLIERS_CONFIG, or use environment variables. "
        "Never submit keys, OAuth tokens or raw authenticated responses to issues or Git. "
        "Use component_search for discovery, component_get for exact supplier identity, then component_offers for quantity pricing. "
        "Inspect complete/provider_errors, currency, MOQ, packaging and freshness. Do not add stock pools or convert currencies implicitly. "
        "Mouser and DigiKey require each user's own approved API credentials; Huaqiu is an application/documentation channel pending an adapter contract."
    )


@mcp.prompt()
def compare_supplier_offers(mpn: str, quantity: str = "1") -> str:
    """Guide an evidence-based comparison without exposing credentials or mixing currencies."""
    return (
        f"Compare supplier offers for exact MPN {mpn}, requested quantity {quantity}. "
        "Check component_providers, resolve manufacturer and package using component_get, then call component_offers. "
        "Keep retail/assembly pools, packaging, region, currency, tax and shipping separate. "
        "Report missing credentials, partial results, unknown price/stock and source timestamps. Never ask for API secrets in chat."
    )
