# Codex 接入与验证

当前源码构建的 Python distribution 为 `component-sourcing-mcp==0.8.0rc7`，模块为
`szlcsc_mcp`，两个 CLI 名 `component-sourcing-mcp` 和 `szlcsc-mcp` 均可用。

按 [README 的 Windows 安装步骤](../README.md#windows-安装与接入-codex) 创建虚拟环境并安装。
先用 `python -m pip check` 和
`importlib.metadata.version('component-sourcing-mcp')` 核对环境，再配置 Codex。

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

将配置写入 `~/.codex/config.toml`，Windows 的 `~` 是当前用户目录。把 `command` 和 `cwd` 中的示例路径换成解压后的工程路径。已有 `components` 条目时，在原条目中修改。
重连后，先从 `components` 枚举工具并调用 `szlcsc_cache_status` 核对连接。v0.8.0-rc.7 应列出 53 项工具。一键转换会下载 CAD 和 PDF，慢连接可在 `[mcp_servers.components]` 下设置 `tool_timeout_sec = 360`。

使用本地 HTTP 的客户端可以单独启动服务。

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m szlcsc_mcp --transport http --port 8000
```

客户端地址为 `http://127.0.0.1:8000/mcp`。HTTP 和旧版 SSE 兼容入口只监听回环地址，使用本机进程权限。旧 SSE 客户端可用 `--transport sse` 和 `/sse` 路径。

贸泽/DigiKey 的申请和私有 JSON 配置见 [供应商指南](supplier-setup.md)。在本节 env 表增加 `COMPONENT_SUPPLIERS_CONFIG` 指向自己的凭据文件；先调用 `component_providers` 检查状态。使用有工具目录缓存的 MCP 懒加载代理时，更新安装后也需刷新该服务的目录缓存。

开发者可在源码目录安装测试依赖并运行离线验证。

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m pip install -e '.[dev,altium]'
& '.\.venv\Scripts\python.exe' -X utf8 -m pytest -q
& '.\.venv\Scripts\python.exe' -X utf8 scripts/check_publication_privacy.py
& '.\.venv\Scripts\python.exe' -X utf8 scripts/verify_mcp.py --all-transports
```

联网探测可运行 `scripts/verify_live.py --output live-results.json`。查看报告中的 PASS、BLOCKED、FAIL 和 NOT_RUN。Altium 实际工程验收见 [本地验证指南](local-validation.md)。

可选的已安装进程功能测试必须指定独立写入目录、实际 wheel 和客户端配置：

```text
python -X utf8 scripts/test_installed_functional.py network --server-name components --output-root C:/ComponentTest/isolated --wheel C:/Downloads/component_sourcing_mcp-0.8.0rc7-py3-none-any.whl
```

该脚本核对解释器、配置中的 server command、发行版版本和 wheel 源码一致性。
它还提供 assets、altium、archive、ad、final、integrity、categories 阶段；这些
阶段需要相应网络、转换器和前序 fixture，不能将没有执行的阶段计为通过。
支持 `--codex-config` 指定其他配置，兼容旧服务名时使用 `--server-name szlcsc`。

实时库存和数量报价默认 refresh；失败保持结构化错误，未知数据保持 null。
需求选型先调用 `szlcsc_select`。把返回的 `selection` 随下一轮修改传入 `previous`。提供数量后可看到候选的 `procurement`，其中单列公开展示价和有数量依据的报价。
`ACCESS_DENIED` 不应无限重试，`RATE_LIMITED` 按 retry_after_seconds 处理。
本地文件工具使用服务账户的实际权限，测试前将用户主库复制到独立 fixture。
