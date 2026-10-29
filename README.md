# Component Sourcing MCP

用于电子元器件选型、库存/阶梯价查询、资料下载和 Altium 器件库管理的 MCP Server，可接入 Codex 等 MCP 客户端。

当前版本为 **v0.8.0-rc.2（预发布）**，Python 包版本为 `0.8.0rc2`。当前实现 **49 个 Tool、两项 Resource 和两项 Prompt**；正式 Altium/Datasheet 制造验收尚未完成。

| 平台或工作流 | 当前状态 |
|---|---|
| 国内立创 SZLCSC | 已实现搜索、精确 C 编号/MPN、参数筛选、库存、CNY 数量报价、资料下载 |
| JLCPCB 装配库 | 已实现基础/优选/扩展库检索和分类；上游可能返回访问限制 |
| EasyEDA / Altium | 已实现资料获取；Altium 扫描/写回/转换需显式安装可选依赖，见下方安全限制 |
| 长期资料库 | 已实现分类归档、离线检索、同步预览、审计、修复、导出和迁移 |
| 贸泽 Mouser | 已实现官方 Search API 关键词/精确料号、库存与原币阶梯价；需自行申请 Search API Key |
| DigiKey | 已实现官方 Product Information V4 搜索/详情、包装库存、阶梯价与 OAuth 自动令牌；需自行申请应用凭据 |
| 华秋 Huaqiu | 提供官方申请渠道和贡献说明；尚无可执行适配器，填写 Key 不会代替正式协议接入 |

Python 模块 `szlcsc_mcp`、原 CLI 别名 `szlcsc-mcp` 和既有 45 个 `szlcsc_*` 工具保留兼容。新增 `component_providers`、`component_search`、`component_get`、`component_offers` 提供统一入口，不用立创 C 编号或 CNY 字段承载其他平台的数据。

**多平台申请和配置见 [供应商接入指南](docs/supplier-setup.md)**：每位使用者自行申请 API，填写私有配置文件或环境变量。仓库不提供真实 Key；未配置的平台返回结构化状态，其他平台仍可查询。Mouser/DigiKey 已通过官方契约 Mock 验证，当前维护环境未提供真实账户凭据，尚未完成其授权联网联调。

## 安装

需要 Python 3.11+，主要验证环境为 Windows + Python 3.12：

```powershell
git clone https://github.com/zhr1008/component-sourcing-mcp.git
Set-Location component-sourcing-mcp
py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -X utf8 -m pip install --index-url https://pypi.org/simple --upgrade pip
& '.\.venv\Scripts\python.exe' -X utf8 -m pip install --index-url https://pypi.org/simple -e .
& '.\.venv\Scripts\python.exe' -X utf8 -m pip check
& '.\.venv\Scripts\python.exe' -X utf8 -c "from importlib.metadata import version; print(version('component-sourcing-mcp'))"
```

版本检查应显示 `0.8.0rc2`。发布的 wheel、源码 ZIP 和校验文件见 [Releases](https://github.com/zhr1008/component-sourcing-mcp/releases)。

## 接入 Codex

在 `~/.codex/config.toml` 中配置，替换实际路径；已有同名表时修改原表：

```toml
[mcp_servers.components]
command = 'C:\src\component-sourcing-mcp\.venv\Scripts\python.exe'
args = ['-m', 'szlcsc_mcp', '--transport', 'stdio']
cwd = 'C:\src\component-sourcing-mcp'
startup_timeout_sec = 30
tool_timeout_sec = 120

[mcp_servers.components.env]
PYTHONUTF8 = '1'
SZLCSC_REQUEST_DELAY = '0.5'
SZLCSC_EDA_REQUEST_DELAY = '0.5'
```

重连 MCP 后，先调用 `szlcsc_cache_status` 确认连接，再查料。长批量转换应拆分任务或调整 `tool_timeout_sec`。其他客户端配置见 [mcp.example.json](mcp.example.json)，详细接入与验证见 [Codex 指南](docs/codex-setup-and-validation.md)。

可选的本地 HTTP：

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m szlcsc_mcp --transport http --port 8000
```

客户端地址为 `http://127.0.0.1:8000/mcp`。CLI 只允许回环地址，启用 Host/Origin 防护；HTTP/SSE 没有多用户鉴权，不是公网服务。旧 SSE 客户端可使用 `--transport sse` 的 `/sse` 入口。

## 工具与数据

| 任务 | 主要 Tool |
|---|---|
| 搜索/参数选型 | `szlcsc_search`、`szlcsc_parametric_search`、`szlcsc_get_part` |
| 库存/数量报价 | `szlcsc_get_stock`、`szlcsc_get_pricing` |
| 比较与替代 | `szlcsc_compare_parts`、`szlcsc_find_alternatives` |
| 装配库 | `szlcsc_jlcpcb_search`、`szlcsc_jlcpcb_categories` |
| 资料/CAD | `szlcsc_download_part`、`szlcsc_download_parts`、`szlcsc_eda_assets` |
| 已有 Altium 库 | `szlcsc_ad_library_scan`、`szlcsc_ad_library_search`、`szlcsc_ad_library_enrich`、`szlcsc_ad_library_property_apply` |
| 转换与长期归档 | `szlcsc_altium_import`、`szlcsc_altium_smoke_test`、`szlcsc_library_add`、`szlcsc_library_audit`、`szlcsc_library_migrate` |
| 诊断/缓存 | `szlcsc_healthcheck`、`szlcsc_cache_status` |

原有 45 个工具及调用边界见 [skills/szlcsc/SKILL.md](skills/szlcsc/SKILL.md)，新增 4 个统一工具见 [供应商指南](docs/supplier-setup.md)。Resource 包含 `szlcsc://sourcing-guide`、`components://supplier-guide`；Prompt 包含 `select_domestic_component`、`compare_supplier_offers`。

```text
szlcsc_get_part(identifier="C2040", refresh=true)
szlcsc_get_stock(identifier="C2040", refresh=true)
szlcsc_get_pricing(identifier="C2040", quantity=10, refresh=true)
```

国内报价使用 CNY，明确 MOQ、订购倍数、数量对应的价格阶梯；JLCPCB 装配库存与国内零售库存分别标识。缺失数据保持 null，`data_freshness` 记录缓存/时间信息。库存与报价默认显式刷新；上游失败不静默替换为过期采购数据。

错误使用 MCP `isError=true` 和结构化 `error`，包含 `code`、`message`、`retryable` 等字段。部分成功的批量结果还需检查 `complete` 和子项状态。服务不绕过验证码、登录、签名或访问控制。

`szlcsc_library_sync` 和 `szlcsc_library_sync_preview` 会刷新采购数据，每次最多处理 500 项。
读取 `has_more`、`remaining_count` 和 `next_offset`，把 `next_offset` 作为下次的 `offset` 继续；
分页期间保持库成员或显式目标列表不变。CSV 导出会把公式形式的文本转成字面文本，JSON 保留原值。

## Altium 与工程核验

默认安装提供查料、报价、资料和归档功能，不安装 Altium 解析器。Altium 集成要求 Python 3.12+，`altium-monkey` 用于读取/保存 SchLib/PcbLib，可显式安装：

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m pip install --index-url https://pypi.org/simple -e '.[altium]'
```

**Altium 扩展存在尚未解决的第三方依赖漏洞**：上游当前固定的 lxml/Pillow 版本命中安全公告；详见[依赖安全记录](docs/dependency-security.md)。只处理可信库/图像，在独立、低权限环境中使用，暂不视为安全验收通过。基础安装不引入这组依赖。需要 EasyEDA → Altium 或 merge 时，再安装独立转换器：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-altium-cruncher.ps1
```

安装器固定 `altium-cruncher==2026.9.7` 到托管目录，检查原生命令退出码。MCP 依次查找显式 `SZLCSC_ALTIUM_CRUNCHER`、托管目录和 PATH；显式无效路径不会静默回退。

生成库会检查符号/封装关联、真实焊盘尺寸和保存后的可读取性。已有用户库的参数写回默认 dry-run，冲突保持可见，真实写回前备份。服务内文件操作串行处理，但不同进程应使用不同归档/CAD 根目录。

Datasheet 是 pin/package/footprint/electrical 最终判断依据。自动 smoke 和第三方 CAD 都不能代替投板前对 Pin 1、引脚编号、焊盘尺寸、land pattern 和 3D 方位的工程复核。

## 验证与贡献

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m pip install --index-url https://pypi.org/simple -e '.[dev,altium]'
& '.\.venv\Scripts\python.exe' -X utf8 -m pytest -q
& '.\.venv\Scripts\python.exe' -X utf8 scripts/check_publication_privacy.py
& '.\.venv\Scripts\python.exe' -X utf8 scripts/verify_mcp.py --all-transports
```

单元/Mock 与协议验证不访问上游数据。可选实时探测命令为 `scripts/verify_live.py --output live-results.json`，需要按报告区分 PASS、BLOCKED、FAIL 和 NOT_RUN。本地 Altium 正式验收见 [验收指南](docs/local-validation.md)；未完成验收前只发布预发布版本。

公共 CI 使用 GitHub-hosted runner，并检查完整可达历史的隐私规则。不要提交真实凭据、私人目录、原始日志或用户库。贡献与漏洞报告见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)。

本版修复范围见 [全面检查记录](docs/audits/comprehensive-2026-10-29.md)，验证见 [rc.2 记录](docs/test-reports/v0.8.0-rc.2-automated.md)。多供应商初版和原始公开快照的验证分别保留在 [rc.1 记录](docs/test-reports/v0.8.0-rc.1-automated.md)和 [rc.4 记录](docs/test-reports/v0.7.0-rc.4-automated.md)。未提供凭据或上游受限的调用不计为授权联网成功。

## 许可与数据来源

本仓库自有代码使用 [MIT](LICENSE)。可选 Altium 集成的 `altium-monkey` 和 `altium-cruncher` 均声明 **AGPL-3.0-or-later**；包含这些功能的整体分发不能解释为全部仅受 MIT 约束。具体依赖、集成边界和分发前需确认的事项见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。下载的 Datasheet/CAD 资产保留其上游权利。

本项目是独立社区实现，与 SZLCSC、JLCPCB、EasyEDA、Altium、Mouser、华秋和 DigiKey 无官方隶属关系。
