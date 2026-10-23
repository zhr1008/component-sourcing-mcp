# Altium 实机专项测试规范

适用于 SZLCSC MCP v0.5.0+。本文件是强制实机验收要求；自动 smoke PASS 不能替代 Altium Designer 人工检查。

## A. 环境门槛

记录：Windows、Altium Designer、Python、SZLCSC MCP、`altium-cruncher`、`altium-monkey`、`wn-geometer` 版本。

执行 `szlcsc_altium_setup()` / `szlcsc_altium_status()`，只有同时满足下列条件才进入后续测试：

- `installed=true`
- `ready=true`
- `status=ready`
- `easyeda_import_available=true`
- `merge_available=true`
- 3D download support=true
- 3D placement support=true

缺任何依赖时结果必须为 SKIP/NOT RUN，而不是 PASS。

## B. 单器件 smoke

基准器件优先使用 `C2040`，再增加至少一个 SOT-23/SOIC 类小封装器件。

要求：

- SchLib 非空、可解析
- PcbLib 非空、可解析
- source pin ↔ generated pin/pad 计数可解释
- unsupported graphics 有明确计数
- mapping report 存在
- STEP 下载成功（源有 3D 时）
- 3D Body 已绑定而非仅下载 STEP 文件
- 3D placement verdict 不得隐瞒 fallback/needs_checking

任何 failed check 都应使 smoke FAIL。

## C. Altium Designer 人工检查

每个代表性器件人工打开 SchLib/PcbLib，至少核对：

1. Symbol 引脚总数、编号、名称、Pin 1。
2. EP/裸焊盘等特殊引脚是否与 Datasheet 一致。
3. Footprint Pad 总数、编号、尺寸、间距、形状。
4. 丝印、Courtyard/机械图形不应覆盖关键焊盘。
5. 原点合理。
6. STEP X/Y/Z、旋转、正反面、悬高。
7. Symbol ↔ Footprint 映射 100% 或逐项解释差异。

结果必须写 `MANUAL_PASS` / `MANUAL_FAIL` / `NOT_RUN`。

## D. Merge

至少用两个不同器件执行：

- SchLib merge
- PcbLib merge

检查合并文件可在 Altium 打开，器件数量正确、名称冲突策略符合预期、共享 CAD 只合并一次。

## E. 已知 warning 处理

例如 C2040 曾出现 `51/57 off-grid pin hotspots from source EasyEDA`。这类 warning：

- 必须原样保留在 concerns；
- 不因人工通过就删除 warning；
- 也不能仅因 warning 存在就自动判结构性失败；
- 人工检查结论与自动 warning 应同时记录。

## F. Release 判定

Altium 能力要在 Release 中标记为“实机已验证”，必须同时满足：工具链 PASS、smoke PASS、merge PASS、至少一项 MANUAL_PASS。否则只能写“单元测试通过/未完成实机验证”。
