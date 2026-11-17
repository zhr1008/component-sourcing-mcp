# 长期归档与发布

## 长期采购/归档库

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

## 测试/发布纪律

正式规范：

- `docs/test-plans/FULL_TEST_PLAN.md`
- `docs/test-plans/ALTIUM_TEST_PLAN.md`
- `docs/test-plans/RELEASE_ACCEPTANCE.md`
- 每版本 `docs/test-plans/vX.Y.Z-ANTIGRAVITY.md`

开发侧只开发、提交和写测试要求；Antigravity 在用户 Windows 本机完成验收。

`pytest` 全过不等于实机全部 PASS。涉及真实 SZLCSC、Datasheet、现有 SchLib/PcbLib、SchLib 写回、Altium Designer 打开检查、EasyEDA/STEP、Altium smoke 的项目未执行时必须写 SKIP/NOT_RUN。

Stable Release 必须有对应 Antigravity 报告，包含准确 commit、`Overall: PASS`、FAIL/SKIP 清单。
