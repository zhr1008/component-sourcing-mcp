from typing import Annotated

from pydantic import Field

from .altium_bundle import Expected, VisualEvidence, build_bundle, bundle_summary, review_bundle
from .altium_links import LibraryLinkError
from .errors import error_payload
from .server_base import _json, client, eda_client, mcp


@mcp.tool(output_schema=None)
def szlcsc_altium_build(
    c_code: Annotated[str, Field(pattern=r"^[Cc][1-9][0-9]{0,11}$")],
    output_root: str = "altium-bundles",
    target_board: str | None = None,
    datasheet_url: str | None = None,
    timeout: Annotated[int, Field(ge=10, le=300)] = 240,
) -> str:
    """仅在用户明确要求下载封装或一键生成 AD 库时调用。以国内立创 C 号生成「完整型号_C号」SchLib、PcbLib、嵌入并独立保存的 STEP、原始 EDA 数据和核验报告。尝试下载 Datasheet。target_board 仅接收已保存的 PcbDoc，离线盘点层叠与规则。生成不调用 Altium Designer。读取 observations 和 PDF 后用 szlcsc_altium_verify_bundle 提交可追溯证据。"""
    try:
        result = build_bundle(client, eda_client, c_code.upper(), output_root, target_board, datasheet_url, timeout)
        return _json(bundle_summary(result))
    except (LibraryLinkError, OSError, ValueError) as exc:
        return _json(error_payload(exc, code="ALTIUM_BUNDLE_FAILED"))


@mcp.tool(output_schema=None)
def szlcsc_altium_verify_bundle(
    bundle_dir: str,
    expected: Annotated[list[Expected], Field(max_length=5000)],
    orientation: VisualEvidence | None = None,
) -> str:
    """核对现成器件包的 observations。expected 填原厂或适用规范的独立期望值及页码/定位；工具与保存时实测值比较并写 verification.json。缺证据按项待核实。只有检查过实际 STEP 几何与原厂 Pin 1 后才提交 orientation。不启动 AD。"""
    try:
        return _json(bundle_summary(review_bundle(bundle_dir, expected, orientation)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _json(error_payload(exc, code="ALTIUM_BUNDLE_REVIEW_FAILED"))
