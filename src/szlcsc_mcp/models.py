from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class PriceTier:
    min_qty: int | None = None
    unit_price_cny: float | None = None
    max_qty: int | None = None
    currency: str = "CNY"


@dataclass(slots=True)
class Part:
    lcsc_code: str | None = None
    mpn: str | None = None
    name: str | None = None
    brand: str | None = None
    package: str | None = None
    category_id: int | None = None
    category: str | None = None
    product_id: int | None = None
    product_sign_id: str | None = None
    stock: int | None = None
    total_stock: int | None = None
    stock_status: str | None = None
    warehouse_stock: dict[str, int] = field(default_factory=dict)
    price_tiers: list[PriceTier] = field(default_factory=list)
    small_batch_price_cny: float | None = None
    min_order_qty: int | None = None
    full_pack_qty: int | None = None
    order_multiple: int | None = None
    packaging: str | None = None
    image_url: str | None = None
    datasheet_url: str | None = None
    product_url: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    source: str = "szlcsc.com"
    data_freshness: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def in_stock(self) -> bool | None:
        if self.stock is None and self.total_stock is None and not self.warehouse_stock:
            return None
        values = [x for x in (self.stock, self.total_stock) if x is not None]
        values.extend(self.warehouse_stock.values())
        return max(values or [0]) > 0

    @property
    def display_id(self) -> str:
        return self.lcsc_code or self.mpn or "unknown"
