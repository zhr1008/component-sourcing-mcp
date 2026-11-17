---
name: component-sourcing
description: 根据功能和电气约束选器件或替代料，按精确 C 号和 MPN 查参数、库存与数量价，核对立创/JLCPCB 或跨供应商证据，交付 AD 选型表，或按需下载 Datasheet、EasyEDA/STEP 和处理 Altium 库。
---

# 器件选型与采购

先识别这次要查现有器件、做选型决策、核 BOM 资格，还是生成指定工件。使用当前会话实际暴露的 `components` 签名。部分工具保留 `szlcsc_*` 前缀。

| 任务和停点 | 优先入口 |
| --- | --- |
| 已知 C 号或完整 MPN，查参数、资料或比较 | 复用已有详情，只补缺少的 `szlcsc_get_part(identifier=…, include_procurement=false)`。当前 `szlcsc_compare_parts` 会取采购数据，用于也要采购对比的任务。默认正文给所需结果、证据和可复制字段。 |
| 只查库存、采购数量价或装配库 | 分别用 `szlcsc_get_stock`、`szlcsc_get_pricing`（带数量）、`szlcsc_jlcpcb_search`。按所需字段结束。 |
| 按电气需求选用一颗或多颗器件，或为实际设计找替代 | `szlcsc_select(request=…)`，未识别的硬条件放 `conditions`，续调传回 `previous=selection`。明确需求以外的系列、后缀和封装再用 `szlcsc_discover` 补查。 |
| 已有 BOM 的资格、封装或供应风险 | 用 `qualify-pcba-sourcing` 按位号追溯资格。本技能提供身份、原厂和渠道证据。仅档案转换或补齐既定件渠道编号时，保留该任务要求的产物。 |
| 器件资料、跨供应商、价格阶梯或工具异常 | 查 [采购与检索细则](references/sourcing-queries.md) 的相关小节。 |
| 下载封装、新建库、读取保存的 AD 工程 | 查 [资料与 AD 资产](references/altium-assets.md)。明确要封装或一键库时才触发对应资产操作。 |
| 整理既有 SchLib/PcbLib 或维护长期归档 | 分别读 [现有库整理](references/altium-existing-library.md) 或 [长期归档与发布](references/archive-and-release.md)。 |

## 先取得必要证据

- 完整订购 MPN、渠道料号和封装独立核对。多条精确匹配时按品牌、封装和渠道身份消歧。缺失或冲突字段保留待核实，继续按证据范围交付。
- 国内库存与数量价使用立创国内来源。展示价、数量价、零售/装配库存分别处理。电气适用性和脚位/封装最终判定依原厂资料。商城和 EasyEDA 资产供核对；采购有货或库存未知均不决定电气等效。
- 只读当前任务所需的参考小节。批量选型按相同规格和硬约束复用查询，保留各位号与用途。核实将要交付的候选和明确要求比较的条目。证据仍有效时复用，出现具体缺口、条件或版本变化、或需要实时采购数据时补查。
- 只有候选评分时继续核对原厂脚位、封装和电气证据，再判断是否可直接替换。明确要求现货但没有数量时，查最终候选库存，等用户给出实际数量再查数量价。
- 复用已有公司/参考工程时保留源版本和身份依据。涉及系统接口变化时交给 `hardware-solution`。AD 活动文档、编译、写入、前台和外发按当前项目及宿主的具体授权处理。

## 器件字段与文件

把建议采用的器件和可复制的 AD 字段交给用户。大多数短名单默认最多五行，保留关键参数、证据链接和仍待核实的条件。依 [AD 字段规范](references/ad-field-rules.md) 核对精确 C 号商品页，复用已取得的同版证据。

实际形成可用于设计的 AD 选型或替代清单时默认提供逐格复制的 HTML。找现有器件、资料、比对既定型号、查价、只补 BOM 料号等按任务停点交付。明确请求的格式优先，Excel 按需。需文件时由 [本地固定导出程序](references/ad-selection-export.md) 从已核对 JSON 出表。后补 Excel 复用该 JSON，保留已有 HTML。
