---
name: szlcsc
description: Source domestic China SZLCSC components by exact C-code/MPN or electrical/package filters, verify fresh domestic stock and CNY quantity pricing, discover separate JLCPCB basic/preferred/extended assembly libraries, retrieve Datasheets/EasyEDA assets, and generate, enrich or verify Altium component libraries.
---

# SZLCSC — 国内立创选型、资料、Altium 与已有库整理

用户要求国内立创商城、`item.szlcsc.com`、人民币按数量报价、实时/分仓库存、Cxxxx/MPN、封装与电气参数选型、JLCPCB 基础/优选/扩展贴片库、Datasheet、EasyEDA/STEP、Altium 库，或整理已有 SchLib/PcbLib 时使用本技能。

本说明适用于 Component Sourcing MCP v0.7.0-rc.4 的 45 个 Tool。配置存在、技能已安装和本轮工具实际可调用是不同证据；未暴露原生工具时可检查本地 stdio 服务，不宣称当前聊天已加载它们。预发布版不代表 Antigravity/Altium 稳定验收通过。

## 一、不可违反的数据与证据规则

1. 国内采购结论以 `szlcsc.com` 为准，不静默替换成国际 LCSC 美元价。
2. `null/unknown` 不是 0；缺失库存、价格、参数必须保持 unknown。
3. 成本比较默认使用最小采购数量对应的小批量人民币阶梯价 `small_batch_price_cny`。
4. 有合适现货时，不把缺货料作为主推荐。
5. 替代料工具只提供候选，不能仅靠候选评分声明 pin-to-pin/drop-in。
6. EasyEDA/SZLCSC Symbol、Footprint、3D 都是**辅助 CAD 资产**，不是制造商真值。
7. **Datasheet 是 pinout、package、terminal numbering、机械尺寸、land pattern、电气参数最终判定的唯一权威来源。**
8. 若 SZLCSC/EasyEDA 与 Datasheet 冲突，必须以 Datasheet 为准并报告冲突。
9. 不因“立创官方封装”或“EasyEDA 原理图”存在就判本地封装正确。
10. 不绕过验证码、登录、WASM 签名或其他访问控制。

## 二、选型调用路径

### 精确 C 编号

```text
szlcsc_get_part("Cxxxxxx")
```

需要替代时：

```text
szlcsc_find_alternatives(...)
szlcsc_compare_parts(...)
```

最终兼容性仍核 Datasheet。

精确查询不接受唯一模糊结果。MPN 对应多个 C 编号时检查 `AMBIGUOUS_PART`，回到候选的封装/品牌及明确 C 编号，不擅自选第一条。

### 明确 MPN

1. `szlcsc_search(MPN)`。
2. 必须确认返回 MPN exact match。
3. 再 `szlcsc_get_part(C编号)` 获取完整详情。
4. 同 MPN 多 C 编号时比较 package、brand、stock、price；不能因为搜索排第一就认为唯一。

普通搜索必须读取 `search_relevance.hard_pass`，不要盲信商城原始排序。

### 功能/参数选型

优先：

```text
szlcsc_parametric_search(...)
```

必须检查 `constraint_match.all_matched` 以及每个参数的 `actual / expected / matched`。

### 国内库存和按数量报价

采购前优先调用聚焦 Tool：

```text
szlcsc_get_stock(identifier="C2040", refresh=true)
szlcsc_get_pricing(identifier="C2040", quantity=10, refresh=true)
```

- `inventory_pool="SZLCSC_domestic_retail"` 是国内零售库存；读取 stock/total_stock/warehouse_stock、MOQ 和订购倍数。
- 报价读取 `requested_quantity / order_quantity / unit_price_cny / line_total_cny`，不得把高数量阶梯单价套给小批量；实际订购量可能因 MOQ/倍数调整。
- `full_pack_qty` 是整盘/整包装数量，不是 MOQ。仅显式购买下限可用于提高订购量；缺失 MOQ 保持 null。阶梯 `max_qty=null` 可表示末档无上限。
- `refresh=true` 绕过 TTL 缓存。检查 `data_freshness` 与 `warnings`；缓存时间是来源快照时间，不是价格有效期保证。
- 详情 Tool `szlcsc_get_part` 也支持 refresh。缺库存/价保持 null；请求失败不能用旧缓存冒充实时数据。

### JLCPCB 装配库

```text
szlcsc_jlcpcb_search(query="100nF", library_type="basic", package="0603", min_stock=10, page=1, page_size=10)
szlcsc_jlcpcb_categories(refresh=false)
```

- library_type 可用 all/basic/preferred/extended/no_fee；preferred 优先于 expand 分类，no_fee 合并 basic 与 preferred 两个分页窗口并去重，最多返回 2×page_size 条。
- `complete=false` 表示 no_fee 的某个库不可用；必须报告 `unavailable_libraries`，不能把保留的部分结果当成完整查询。
- `inventory_pool="JLCPCB_assembly"` 与国内零售是不同库存池，不能相加或互相代替。
- 装配报价币种未明确时保持 null；不能用国际美元价补齐国内 CNY 报价。
- 分类字典缓存 24 小时。403/429 或未知字段必须报告限制，不绕过验证码、登录、WASM 或指纹限制。

### MCP 错误与调用边界

失败检查 `isError` 与 `error.code / error.message / error.retryable / error.retry_after_seconds`。PART_NOT_FOUND/AMBIGUOUS_PART/INVALID_ARGUMENT 不盲目重试；RATE_LIMITED 按等待时间处理；ACCESS_DENIED 停止并报告访问条件。批次结果还需检查逐项失败。

关键上限：搜索/候选池 1–50，compare 20、下载 100、Datasheet 同步 200、Altium import batch 50、smoke 5；非空列表不得被静默截断。可读取 `szlcsc://sourcing-guide` 或获取 `select_domestic_component` Prompt 辅助选型。

## 三、Datasheet

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

## 四、EasyEDA / 新器件 Altium 生成

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

## 五、已有 Altium SchLib/PcbLib 整理（v0.6+）

这是**整理用户现有 AD 库**的路径，与 EasyEDA→Altium 新建库不同。

### 核心原则

- 保留用户现有 AD Symbol 图形与绘图风格。
- **不要用 EasyEDA/SZLCSC Symbol 替换已有 Symbol。**
- 读取现有 SchLib/PcbLib，补属性、建索引、核对关联。
- `altium-catalog.json` 管用户 AD 资产；`library-index.json` 管 C-code 采购/归档资产，两者不要混成一个数据库。

### 1. 状态与扫描

先：

```text
szlcsc_ad_library_status(catalog_root="...")
```

扫描：

```text
szlcsc_ad_library_scan(
  paths=["D:/Altium/SchLib", "D:/Altium/PcbLib"],
  catalog_root="D:/AltiumCatalog"
)
```

扫描应记录：

- SchLib 路径、Symbol 名称、参数、pins；
- Symbol 声明的 footprint implementation；
- PcbLib 路径、Footprint 名称、pads、基础几何；
- footprint link 的 `resolved / missing / ambiguous`；
- C-code/MPN/brand/package identity hints。

增量规则：

- 未变化文件复用上次解析；
- 修改过的文件才重新解析；
- 老库解析失败时保留 last-good catalog record，并标 `catalog_stale=true` / `stale_due_to_parse_error`；
- stale 记录不能描述成本次 freshly parsed。

### 2. Symbol ↔ Footprint 关联

关联优先级：

1. SchLib implementation 中的 footprint model name。
2. implementation DATAFILE，如 `ModelDatafileEntity0` / `ModelDatafileKind0`，用于同名 footprint 的 PcbLib 缩小范围。
3. 本地 PcbLib exact footprint name。
4. Parameter 中 footprint 字段仅作补充线索。

如果同名 footprint 仍有多个候选，必须 `ambiguous`，不能擅自挑一个。

### 3. 身份补全

```text
szlcsc_ad_library_enrich(catalog_root="...")
```

自动绑定策略必须保守：

- exact C-code → 可自动绑定；
- exact MPN → 可自动绑定；
- exact MPN 有多个 C-code 且 package 也无法唯一化 → `ambiguous`；
- Symbol 名称只有在明显是 MPN 且商城存在 exact MPN 时才可自动绑定；
- 普通名称/描述的模糊搜索**不能自动写成身份**。

补全记录应保留：

- C-code / MPN / brand / category / package；
- price / stock / MOQ / order multiple / packaging；
- product URL / Datasheet URL；
- price tiers；
- **完整 SZLCSC parameters 字典**；
- `last_checked_at`。

### 4. Datasheet 下载

```text
szlcsc_ad_library_datasheets(catalog_root="...")
```

只对已 resolved identity 的器件下载。相同 C-code 可共享同一 PDF。

下载后不自动称“已核验”；初始是待 Agent 阅读 Datasheet。

### 5. Datasheet-only 核验

先拿队列：

```text
szlcsc_ad_library_verification_queue(catalog_root="...")
```

Agent 必须实际读取 Datasheet，并提交结构化证据：

```text
szlcsc_ad_library_verify(
  asset_id="symbol:...",
  datasheet_evidence={
    "source":"datasheet",
    "mpn":"...",
    "package":"...",
    "pages":[...],
    "pins":[{"number":"1","name":"..."}],
    "terminal_numbers":[...],
    "mechanical_checks":[
      {"name":"pitch/body/EP/land_pattern/...","expected":"...","actual":"...","pass":true,"page":18}
    ]
  }
)
```

**禁止**：

- `source="easyeda"` 或 `source="szlcsc"` 作为最终 verification evidence；
- 没 Datasheet 页码却标 verified；
- 只比 pin count/pad count 就标 verified；
- 只看到立创 Footprint 一样就标 verified。

最终 `verified` 至少要求：

- resolved MPN 与 Datasheet 覆盖型号不冲突；
- Symbol pin-number set 与 Datasheet 一致；
- 本地 Footprint pad-number set 与 Datasheet terminal numbering 一致；
- footprint link 唯一 resolved；
- pitch/body/EP/land-pattern 等机械关键项有 Datasheet 页码证据且全部通过。

Pin name 差异可以记录为 alias/命名差异，但 pin number mismatch 是阻断项。

若 pin/pad 一致但机械证据不足，应保持 `mechanical_review_required`，不能升级 `verified`。

### 6. SchLib 属性补全

只读计划：

```text
szlcsc_ad_library_property_plan(
  catalog_root="...",
  parameter_mode="summary",
  overwrite_existing=false
)
```

规则：

- 缺字段 → `add`；
- 已有同值 → `keep`；
- 已有不同值 → 默认 `conflict`；
- 只有用户明确允许 `overwrite_existing=true` 才变成 `update`。

写回：

```text
szlcsc_ad_library_property_apply(
  catalog_root="...",
  dry_run=true,
  backup=true,
  overwrite_existing=false
)
```

真实 apply 前必须先看 plan/dry-run。

允许补充：

- Manufacturer
- Manufacturer Part Number
- LCSC Part #
- SZLCSC Category
- Package
- Description
- Features
- Datasheet
- SZLCSC URL
- Small Batch Price CNY
- SZLCSC Stock
- MOQ
- Packaging
- `Spec: <参数>`（all 模式）

真实写回要求：

- 默认备份 SchLib；
- 只修改 parameter records；
- 不改 Symbol graphics；
- 不改 pin geometry；
- 不改 footprint implementation/link；
- 写回后建议重新 scan，并用 Altium Designer 打开真实库确认。

### 7. 目录表

```text
szlcsc_ad_library_export(catalog_root="...", export_format="both")
```

JSON/CSV 必须包含：

- Symbol / SchLib；
- Footprint / PcbLib；
- C-code / MPN / brand / category / package；
- price / stock / MOQ / links；
- pin/pad count；
- verification status；
- local parameters；
- **完整 SZLCSC parameters**；
- timestamps/issues。

CSV 使用 UTF-8 BOM，保证 Windows Excel 中文可直接查看。

### 8. 增量更新

```text
szlcsc_ad_library_refresh(
  catalog_root="...",
  max_age_hours=24
)
```

本地 CAD 和动态采购数据分开更新：

- 只重扫发生变化的 SchLib/PcbLib；
- 只刷新超过 max age 的商城 metadata；
- price/stock/MOQ 变化不使 Datasheet verification 失效；
- C-code/MPN/package identity 变化必须把已有 verification 标 stale，重新核验。

## 六、长期采购/归档库

长期归档仍使用：

```text
szlcsc_library_add(...)
szlcsc_library_search(...)
szlcsc_library_sync_preview(...)
szlcsc_library_sync(...)
szlcsc_library_audit(...)
szlcsc_library_orphans(...)
szlcsc_library_repair(...)
szlcsc_library_export(...)
szlcsc_library_migration_plan(...)
szlcsc_library_migrate(...)
szlcsc_library_merge(...)
```

固定规则：

- C-code 是采购条目主键；
- MPN+package 是 CAD 去重键；
- `classified/` 是长期 canonical；`flat/` 是浏览镜像；
- 文件名固定 `类型-型号-C编号`；
- archive layout v2：`layout_version=2`、`naming_policy=category-mpn-c-code`；
- migration 默认 dry-run，真实执行先备份 index；
- 旧资产只 copy，不自动 move/delete；
- canonical 同名不同 SHA256 必须 blocker；
- repair 默认 dry-run；
- orphan scan 只报告、不删除。

## 七、替代料最终核对顺序

1. Datasheet covered MPN。
2. Package / terminal numbering。
3. pinout / pin function。
4. 输入输出与绝对最大额定值。
5. 精度、容差、温漂等性能参数。
6. 稳定性条件与外围要求。
7. thermal / temperature。
8. 国内库存。
9. `small_batch_price_cny`。

## 八、失败降级

- C-code 查不到：报告失败，不改国际商城。
- MPN 无 exact：不要强绑，转参数化搜索或人工确认候选。
- price/stock 缺失：unknown，不猜 0。
- Datasheet 缺失：不能完成最终 package/pin verification。
- SchLib/PcbLib parse 失败：保留 last-good stale record，报告具体文件。
- footprint link 多候选：ambiguous。
- property conflict：保持 conflict，不自动覆盖。
- `altium-cruncher` 缺失只影响转换/merge，不应假装生成文件。
- migration 有 blocker：停止真实迁移。

## 九、测试/发布纪律

正式规范：

- `docs/test-plans/FULL_TEST_PLAN.md`
- `docs/test-plans/ALTIUM_TEST_PLAN.md`
- `docs/test-plans/RELEASE_ACCEPTANCE.md`
- 每版本 `docs/test-plans/vX.Y.Z-ANTIGRAVITY.md`

开发侧只开发、提交和写测试要求；Antigravity 在用户 Windows 本机完成验收。

`pytest` 全过不等于实机全部 PASS。涉及真实 SZLCSC、Datasheet、现有 SchLib/PcbLib、SchLib 写回、Altium Designer 打开检查、EasyEDA/STEP、Altium smoke 的项目未执行时必须写 SKIP/NOT_RUN。

Stable Release 必须有对应 Antigravity 报告，包含准确 commit、`Overall: PASS`、FAIL/SKIP 清单。
