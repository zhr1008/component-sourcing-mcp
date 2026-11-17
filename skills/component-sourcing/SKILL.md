---
name: component-sourcing
description: 根据功能和电气约束选器件或替代料，按精确 C 号和 MPN 查参数、库存与数量价，核对立创/JLCPCB 或跨供应商证据，交付 AD 选型表，或按需下载 Datasheet、EasyEDA/STEP 和处理 Altium 库。
---

# 器件选型与采购

按用户需要的证据和交付物选择能完成任务的最短流程。各层可直接进入，按本层停点交付。使用当前会话实际暴露的 `components` 签名。

| 执行深度 | 用法和停点 |
| --- | --- |
| 定向查询 | 已知 C 号或完整 MPN 时先用手头证据，只补缺字段。技术详情用 `szlcsc_get_part(include_procurement=false)`，库存、数量价和装配库分别用 `szlcsc_get_stock`、`szlcsc_get_pricing`、`szlcsc_jlcpcb_search`。给所问字段和来源即可。 |
| 批量核对 | 已有料号清单时用 [批量精确查料](references/batch-lookup.md)，按原行去重，续跑用 `--reuse` 只补新增、失败和未查项。只转物料档案或补既定料号时按 `bom-material-archive` 的 CSV 终点。 |
| 候选筛选与工程选型 | 按功能或规格找候选用 `szlcsc_select`，续调传 `previous=selection`。批量同规格用 [选型条件分组](references/selection-depth.md#批量同条件分组) 先将同条件归为一次查询。候选初筛给匹配与缺口；确定工程选用或替代时核对最终候选并按需要出表。 |
| 专项核验 | 器件或 BOM 资格交 `qualify-pcba-sourcing`。要求封装下载、新建库或原厂资料时读 [资料与 AD 资产](references/altium-assets.md)，实际需核的项目另选 [现有库整理](references/altium-existing-library.md) 或 [长期归档与发布](references/archive-and-release.md)。 |

采购阶梯、跨供应商、扩展搜索与错误处理见 [采购与检索细则](references/sourcing-queries.md)。选择边界和重复选型的命令见 [分级流程](references/selection-depth.md)。

## 先取得必要证据

- 完整订购 MPN、渠道料号和封装独立核对。多条精确匹配时按品牌、封装和渠道身份消歧。缺失或冲突字段保留待核实，继续按证据范围交付。
- 国内库存与数量价使用立创国内来源。展示价、数量价、零售/装配库存分别处理。电气适用性和脚位/封装最终判定依原厂资料。商城和 EasyEDA 资产供核对；采购有货或库存未知均不决定电气等效。
- 只读当前任务所需的参考小节。证据仍符合本轮的身份、版本和字段需求时复用。出现具体缺口、条件或版本变化、或需要实时采购数据时补查。
- 只有候选评分时继续核对原厂脚位、封装和电气证据，再判断是否可直接替换。明确要求现货但没有数量时，查最终候选库存，等用户给出实际数量再查数量价。
- 复用已有公司/参考工程时保留源版本和身份依据。涉及系统接口变化时交给 `hardware-solution`。AD 活动文档、编译、写入、前台和外发按当前项目及宿主的具体授权处理。

## 器件字段与文件

把建议采用的器件和可复制的 AD 字段交给用户。大多数短名单默认最多五行，保留关键参数、证据链接和仍待核实的条件。依 [AD 字段规范](references/ad-field-rules.md) 核对精确 C 号商品页，复用已取得的同版证据。

实际形成可用于设计的 AD 选型或替代清单时默认提供逐格复制的 HTML。找现有器件、资料、比对既定型号、查价、只补 BOM 料号等按任务停点交付。明确请求的格式优先，Excel 按需。需文件时由 [本地固定导出程序](references/ad-selection-export.md) 从已核对 JSON 出表。后补 Excel 复用该 JSON，保留已有 HTML。
