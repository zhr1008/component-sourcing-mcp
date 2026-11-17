# Datasheet、保存工程与新建 Altium 库

## Datasheet

批量同步：

```text
szlcsc_sync_datasheets(identifiers=[...], output_dir="...")
```

要求：

- 非空批次检查 `ok / processed / manifest_items`。
- `manifest.json.total_items=0` 不得当成功。
- 有失败必须报告每个器件的 C 编号/MPN/host/stage/type/message。
- 官方厂商 HTTPS PDF 允许按受控白名单下载；普通资产仍受 SZLCSC/LCSC host 约束。
- 下载结果需通过 PDF 内容检查。

## 离线操作 Altium 库与已保存工程

磁盘文件任务使用以下入口。只读核对只调用读工具。库写入在用户指定的副本进行，写后重新读取，检查步骤始终以磁盘文件为准。

本机未配置离线服务时，可参考仓库根目录下的 `docs/altium-offline.md`。离线服务的目录权限由 `allowed_paths` 管理。

| 输入或任务 | 使用方式 |
| --- | --- |
| `.SchLib` / `.PcbLib` 的引脚、焊盘、封装链接和几何检查 | 优先用 `altium-offline-library` MCP。用 `list_components` 查名称、`read_schlib` / `read_pcblib` 查图元、`validate_library` 查结构、`diff_libraries` 查差异。需要查看静态外观时用 `render_symbol` / `render_footprint`。 |
| 生成、复制或更新库条目 | 用 `altium-offline-library` 操作已授权的库副本。EasyEDA 转换、采购身份与 AD 属性补全仍用 `szlcsc_altium_*` / `szlcsc_ad_library_*`。可参考已装的 `altium-library` Skill 核对 Datasheet、焊盘和引脚。 |
| `.PrjPcb`、`.SchDoc`、`.PcbDoc` | 用本技能的 [inspect_saved.py](../scripts/inspect_saved.py) 读文件。先用工程清单识别目标，再把具体图纸或板文件传给脚本。 |
| 原理图封装链接、单针网络和驱动冲突初筛 | 如已装 `altium-schematic-review`，用它自带的 `audit_footprints.py <SchDoc> --libs <库目录>`、`net_erc.py <SchDoc>`。结合层级连接、器件分件和厂家资料复核候选问题。 |

用已安装 `altium-monkey` 的 Python 3.12 执行，例如复用 `components` 的 Altium 扩展环境。以下为在本技能目录运行的例子，也可用脚本绝对路径。

```text
python -X utf8 scripts/inspect_saved.py path/to/Design.PrjPcb
python -X utf8 scripts/inspect_saved.py path/to/Sheet.SchDoc --designator U1
python -X utf8 scripts/inspect_saved.py path/to/Board.PcbDoc --designator U1
```

`PrjPcb` 列出工程登记的文档路径，`SchDoc` 给出位号、Comment、引脚数量及封装引用，`PcbDoc` 给出器件及焊盘、走线、网络数量。输出标注 `source=saved_file`，列表默认前 30 项，可用 `--limit` 调整。仅当当前任务明确要求活动文档、未保存内容、原生编译/ERC/DRC 或 ECO 时，才走 AD 桥接。遇到离线工具缺失、解析失败或证据不足时，报告具体缺口，不自动改用 AD。

## EasyEDA / 新器件 Altium 生成

### 明确要求时一键生成并核对 AD 库

只有用户明确说「一键生成／转换并核对 AD 库」或「下载封装」才走本节。只写 C 号、查询型号或做普通选型时使用查询工具。只要原始 EasyEDA 资产时调用 `szlcsc_eda_assets`。

1. 按准确的立创 C 号调用 `szlcsc_altium_build`，指定可写的新输出目录。使用 `完整订购型号_C号` 命名目录、SchLib、PcbLib 和 STEP。产物中应有嵌入的 STEP，且改名后的 PcbLib 引用须从保存的 SchLib 中读回。缺少 STEP 或转换失败时报告具体问题。
2. 用户给出目标工程时，先用 `inspect_saved.py` 从已保存的 `.PrjPcb` 确认对应板文件，再将其 `.PcbDoc` 路径传给 `target_board`。多板工程按用户指定的板核对。产物与目标板分开保存。
3. 实际阅读原厂 Datasheet，确认覆盖完全一致的订购型号和封装。对照 `report_path` 中的 `observations`，把独立取得的期望值用 `szlcsc_altium_verify_bundle` 的 `expected` 提交。每项填 `group`、`field`、`value`、`source`、`reference`，Datasheet 必填实际页码 `page`。数值按报告单位和原点对齐，容差来自引用的资料。先完成有证据的字段，缺项保留待核实。
4. 核对引脚编号与定义、焊盘编号和几何、孔径、阻焊／锡膏模式及机械层用途。符号电气类型（例如默认 Passive）仅作提示。同一板的实际开窗按适用规则确定。只有看过实际 STEP 几何并与原厂图对照 Pin 1、贴板方向后才填写 `orientation`。转换器的 placement 告警也需保留。提供了目标板时，按板厂或工程规范对照已保存的物理层叠和规则。
5. 交付库文件和 `verification.json`。将 `generated` 与核验结论分开报告，分别列出已通过、不符合、待核实和提示。默认全程用离线 CAD 工具，生成资产时联网下载。任务缺少证据时继续保留待核实。只有用户另行明确要求 AD 原生 ERC／DRC、活动文档或 ECO 时才进入 AD 桥接。

### 单独下载、转换或诊断

需要 EasyEDA 资产：

```text
szlcsc_eda_assets(...)
```

需要新建 Altium 库：

```text
szlcsc_altium_setup()
szlcsc_altium_status()
szlcsc_altium_import(...)
```

批量和 smoke：

```text
szlcsc_altium_import_batch(...)
szlcsc_altium_smoke_test(...)
szlcsc_altium_review(...)
```

规则：

- Altium 解析/写回需 Python 3.12+ 和显式 `[altium]` extra；默认查料安装不含解析器。启用前阅读 `docs/dependency-security.md`，当前上游 lxml/Pillow 固定版本仍有已知漏洞。
- `ready=true` 且 `status="ready"` 才算转换环境就绪。
- Windows 默认安装器创建 `%LOCALAPPDATA%\szlcsc-mcp\altium-cruncher` 独立环境；失效的 uv launcher 可用 `scripts/install-altium-cruncher.ps1` 修复。
- 新转换结果必须检查 `quality.symbol_footprint_links.ok` 和保存后重读证据；smoke 包含真实 Footprint implementation 检查。
- 3D 要检查实际 OBJ/STEP 下载和 placement，不能只看 manifest。
- 自动生成 PASS 不代替 Altium Designer 人工核对。
- 必查 Symbol pins、Pin 1、pad number/size/pitch、推荐 land pattern、3D 原点/Z/旋转。
