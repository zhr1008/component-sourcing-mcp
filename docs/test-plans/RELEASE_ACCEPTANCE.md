# Release 验收门槛

适用于 v0.5.0+。

## 核心原则

稳定 Release 的验收由**本地 Antigravity**完成。ChatGPT 负责开发、提交代码和生成测试要求，但不把自身开发环境中的单元测试/自检结果作为稳定 Release 验收结论。

正式本地报告统一保存为：

`docs/test-reports/vX.Y.Z-antigravity.md`

## Stable Release 必须满足

- GitHub `main` 上版本号、README、CHANGELOG、Release Notes 一致；
- 当前版本存在对应的 `docs/test-plans/vX.Y.Z-ANTIGRAVITY.md`；
- 当前版本存在 `docs/test-reports/vX.Y.Z-antigravity.md`；
- 报告明确记录测试环境、版本、commit SHA、Antigravity 作为执行端；
- 新增/修改功能对应测试项为 PASS；
- 关键回归不存在 FAIL；
- SKIP / NOT_RUN 必须写明原因，且不能覆盖本版本新增核心能力；
- 真实 SZLCSC、Datasheet、EasyEDA、Altium 等依赖外部环境的项目，按版本测试要求执行；
- 发布包版本与报告测试版本/commit 一致；
- Release Notes 与 CHANGELOG 已更新。

## Pre-release

允许在本地 Antigravity 验收前创建 Pre-release，用于：

- 提前分发给测试机；
- 验证安装/升级流程；
- 验证 GitHub Release 发布机制。

Pre-release **不得描述为稳定验收通过**。

## 发布阻断条件

以下任一项存在时不得创建 Stable Release：

- 当前版本没有 Antigravity 报告；
- 当前版本新增核心功能为 FAIL / SKIP / NOT_RUN；
- 数据未知被写成 0；
- 搜索出现已知跨器件类别硬误匹配；
- Datasheet 保存 HTML/错误页为 PDF；
- classified 主资产缺失却被 audit/repair 伪造为成功；
- CAD pin/pad 数量不一致且没有明确 FAIL/concern；
- 报告测试 commit 与拟发布 commit 不一致且没有重新验证。

## 报告状态

只允许：`PASS`、`FAIL`、`SKIP`、`NOT_RUN`；人工 Altium 项额外允许 `MANUAL_PASS` / `MANUAL_FAIL`。
