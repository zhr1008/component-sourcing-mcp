# 批量精确查料

已知多个完整订购型号或国内立创 C 号时，用 [batch_lookup.py](../scripts/batch_lookup.py)。脚本读取结构化输入，把重复料号映射回原行，逐项返回当前 `components` 的精确详情。单个料号直接调用工具。按功能、封装约束或首选条件找新器件时，用 `szlcsc_select`。

```powershell
python -X utf8 scripts/batch_lookup.py parts.json --plan
python -X utf8 scripts/batch_lookup.py parts.json
python -X utf8 scripts/batch_lookup.py parts.csv --column 立创料号 --output lookup.json
python -X utf8 scripts/batch_lookup.py parts.csv --column 立创料号 --reuse lookup.json --output next-lookup.json
```

`parts.json` 用 UTF-8 字符串数组，例如 `["C2040","C14663","C2040"]`。CSV 必须指定已有料号列且每条记录有完整料号。CSV 只取该列，请求序列保留首次出现顺序，结果里的 `input_rows` 对应原数据记录。JSON 编号从 1 开始，CSV 的表头记为第 1 行。文本 MPN 的订购后缀和数字前导零保留。输入错误在查询前报告。只需预检时用 `--plan`。

执行需要当前 Codex 中启用的 stdio `components` 和已安装 MCP Python SDK 的解释器。脚本从当前 Codex MCP 配置获取启动方式，使用一个本机 MCP 会话，顺序调用 `szlcsc_get_part(identifier=…, include_procurement=false)`。按需传 `--refresh` 绕过缓存。发送范围是所选列的精确料号。项目名、位号、BOM 其余列和本地文件都留在本机。

用 `--reuse` 显式传入仍适用的前次完整技术详情 JSON。复用身份一致、已成功且未带采购数据的记录，按本轮输入重排、重映射原行号。旧失败、未查和新增料号会补查。全命中时无需启动 MCP。报告记录 `reused`、`to_query` 和逐项状态。使用 `--refresh` 时全量刷新。允许所选列缺料号时传 `--skip-blank`，保留空行索引并返回有缺项。

结果按 `found`、`error`、`unqueried` 报告各项。遇到型号歧义、未命中或身份不符，保留本项错误并核对其他料号。如果服务报告限流或访问拒绝，停止未查项并保留已完成结果。库存、按数量报价和工程核证分别使用相应入口。

短清单可直接读标准输出 JSON。大清单用 `--output` 保存完整详情，并只在标准输出读摘要。目标目录应已存在，结果文件需使用新文件名。退出码 0 表示全部命中或计划完成，1 表示有失败或未查项，2 表示输入、启动或写出失败。最终技术判断仍按精确商品页和原厂资料的各自证据范围。
