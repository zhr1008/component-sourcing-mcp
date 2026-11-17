# AD 选型表本地导出

适用于需要落表的 AD 选型和明确要求文件的请求。精确料号、参数、库存或报价查询按请求回复。未指定文件的单条查料、资料核对和选型中途续筛可直接在对话交付。

## 从工具结果准备底稿

把本轮取得的 JSON 保存到本地。导出器支持 `szlcsc_select` 的 `results`、标准 MCP `structuredContent`、工具库的 `structured_content`，以及只有一个 JSON 文本块的 MCP `content`。也可以传入包裹工具结果的 JSON-RPC 响应。输入用 UTF-8，允许 BOM。

```powershell
python -X utf8 scripts/render_ad_selection.py candidates.json --draft --write-rows reviewed-selection.json
```

这一步只生成待核对的 JSON。每行的 `source` 保留工具原始型号、品牌、规格和证据，便于按 C 编号复查。导出只读取并列的表格字段。现行 `szlcsc_select` 只为部分阻容提供通用描述，缺少的具体描述在底稿里保留「待核实」。按精确 C 编号对照对应商品页的原字段，再核对原厂规格的电气适用性和适用封装。沿用已核过的字段和证据，只更新变化的行。字段核对完成后，把 `reviewed` 改为 `true`。也可在数据核对阶段直接写下面的结构，跳过底稿转换。

任务明确需要 AD 底稿时，同一命令也可直接读 `batch_lookup.py --output` 生成的完整报告。命中项填入 C 号、封装和链接，并保留原始记录与行号。Comment、Description 和工程字段留作待核对，未查或失败项从原报告跟进。

## 已核对的输入规格

```json
{
  "reviewed": true,
  "rows": [
    {
      "designator": "R1",
      "ad_comment": "10K",
      "lcsc_code": "C900000001",
      "purpose": "上拉",
      "package": "0402",
      "component_type": "贴片电阻",
      "generic_description": "贴片电阻/10kΩ/100mW/±1%/待核实/0402",
      "specific_description": "贴片电阻/10kΩ/TEST-0402/100mW/0402/C900000001/FH(风华)",
      "product_url": "https://example.org/product/C900000001"
    }
  ]
}
```

示例仅说明字段格式。已核对记录中的字段直接按文本保存，保留前导零、原始单位、品牌和标点。按技能中的 AD Comment/Description 规则先形成字段，再交给导出器。位号、用途可留空。全表无实际位号时自动省略位号列。通用描述只适用于阻容感，其他行留空，全表无阻容感时自动省略该列。显式未知的供货料号、封装、描述或链接写「待核实」。商品页链接可用 HTTP/HTTPS 独立打开，点击链接单元格的字段值复制原始链接。备选的适用条件写入用途。

## 生成文件

从技能目录运行以下命令，或使用脚本的绝对路径。程序和固定模板只用 Python 标准库。

```powershell
python -X utf8 scripts/render_ad_selection.py reviewed-selection.json --output export/ad-selection
python -X utf8 scripts/render_ad_selection.py reviewed-selection.json --output export/ad-selection --xlsx
python -X utf8 scripts/render_ad_selection.py reviewed-selection.json --output export/ad-selection --xlsx-only
```

第一条只生成 HTML。明确要两种文件时使用第二条。只要 Excel 或后续补 Excel 时用第三条，沿用同一份已核对 JSON 和输出名。Excel 的文本单元格和 HTML 字段使用同一列结构，按原值保留料号、封装和符号。输入也可用 `-` 读取 UTF-8 标准输入。确实需要待核对的文件预览时可显式加 `--draft`，HTML 标题和 Excel 工作表都会标为待核对。
