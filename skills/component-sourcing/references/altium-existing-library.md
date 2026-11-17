# 已有 Altium SchLib/PcbLib 整理（v0.6+）

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
- 写回后重新 scan，并从保存的文件读回确认。确需用 Altium Designer 前台打开真实库时，先就本次窗口和操作取得用户许可。

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
