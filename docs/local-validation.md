# 本地验收与发布

本公开仓库的 CI 仅运行在 GitHub-hosted runner，不向维护者开发机派发 PR。
`examples/maintainer-private-validation.yml` 是给隔离私有自动化仓库使用的非活动模板。

正式 Altium/Datasheet 验收在专用本地 checkout 和独立测试目录执行：

```powershell
$candidate = (git rev-parse HEAD).Trim()
$env:SZLCSC_ANTIGRAVITY_TEST_ROOT = 'D:\ComponentTest\rc4'
$env:SZLCSC_ANTIGRAVITY_LEGACY_LIBRARY = 'D:\AltiumFixtures\legacy'
.\scripts\run-antigravity-validation.ps1 `
  -Version 'v0.7.0-rc.4' -Commit $candidate `
  -TestPlan 'docs/test-plans/v0.7.0-rc.4-ANTIGRAVITY.md' `
  -ReportPath 'docs/test-reports/v0.7.0-rc.4-antigravity.md'
```

仅在有只读源 fixture 时配置 legacy-library 变量；不设置时不自动探测私人目录。
脚本需要官方 Antigravity CLI 与对应权限/登录，不能虚构没有执行的人工步骤。
报告保留精确版本、提交、执行端与 PASS/FAIL/SKIP/NOT_RUN；公开前脱敏副本，原件私存。

稳定门槛见 [RELEASE_ACCEPTANCE.md](test-plans/RELEASE_ACCEPTANCE.md)。没有匹配当前
精确提交的正式报告时只允许 Pre-release。发布工作流从 main 手动 dispatch，禁止
移动既有标签或覆盖附件；有代码变化时使用新版本。
