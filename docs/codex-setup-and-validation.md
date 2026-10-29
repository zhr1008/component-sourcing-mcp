# Codex 接入与验证

当前 Python distribution 为 `component-sourcing-mcp==0.8.0rc3`，模块仍为
`szlcsc_mcp`，两个 CLI 名 `component-sourcing-mcp` 和 `szlcsc-mcp` 均可用。

按 README 创建虚拟环境并安装。先用 `python -m pip check` 和
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

替换路径，避免重复追加同名 TOML 表。重连后枚举 50 个工具，先调用
`szlcsc_cache_status`；可用性必须来自实际连接，不能只根据配置文件判断。
HTTP 配置为 `url = 'http://127.0.0.1:8000/mcp'`，作为 stdio 配置的替代。

贸泽/DigiKey 的申请和私有 JSON 配置见 [供应商指南](supplier-setup.md)。在本节 env 表增加 `COMPONENT_SUPPLIERS_CONFIG` 指向自己的凭据文件；先调用 `component_providers` 检查状态。使用有工具目录缓存的 MCP 懒加载代理时，更新安装后也需刷新该服务的目录缓存。

离线测试：

```text
python -X utf8 -m pytest -q
python -X utf8 scripts/check_publication_privacy.py
python -X utf8 scripts/verify_mcp.py --all-transports
```

可选的已安装进程功能测试必须指定独立写入目录、实际 wheel 和客户端配置：

```text
python -X utf8 scripts/test_installed_functional.py network --server-name components --output-root C:/ComponentTest/isolated --wheel C:/Downloads/component_sourcing_mcp-0.8.0rc3-py3-none-any.whl
```

该脚本核对解释器、配置中的 server command、发行版版本和 wheel 源码一致性。
它还提供 assets、altium、archive、ad、final、integrity、categories 阶段；这些
阶段需要相应网络、转换器和前序 fixture，不能将没有执行的阶段计为通过。
支持 `--codex-config` 指定其他配置，兼容旧服务名时使用 `--server-name szlcsc`。

实时库存和数量报价默认 refresh；失败保持结构化错误，未知数据保持 null。
需求选型先调用 `szlcsc_discover`，检查查询范围、错误和不同封装/引脚数；`PART_NOT_FOUND` 不能证明市场不存在。`procurement` 记录当前公开商品页与旧接口冲突；未标明数量区间的展示价单独返回，不据此计算数量报价。
`ACCESS_DENIED` 不应无限重试，`RATE_LIMITED` 按 retry_after_seconds 处理。
本地文件工具使用服务账户的实际权限，测试前将用户主库复制到独立 fixture。
