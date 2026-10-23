from szlcsc_mcp.client import SZLCSCClient
from szlcsc_mcp.models import Part


def test_alternative_ranking_prioritizes_package_and_stock_before_price():
    src = Part(package="SOT-23-3", parameters={"输出电压": "3.3V"})
    a = Part(mpn="A", package="SOT-23-3", stock=100, small_batch_price_cny=1.2, parameters={"输出电压": "3.3V"})
    b = Part(mpn="B", package="SOT-89", stock=100, small_batch_price_cny=0.5, parameters={"输出电压": "3.3V"})
    c = Part(mpn="C", package="SOT-23-3", stock=0, small_batch_price_cny=0.4, parameters={"输出电压": "3.3V"})
    ranked = SZLCSCClient.rank_alternatives(src, [b, c, a])
    assert ranked[0].mpn == "A"
