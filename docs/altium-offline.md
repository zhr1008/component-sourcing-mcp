# 离线处理 Altium 库与已保存的工程

在 Codex 里用 `$szlcsc` 发起任务即可。先指定要看的是哪份磁盘文件，库要写入时注明目标副本。

- 「用 szlcsc 核对这份 PcbLib 里指定封装的焊盘，并和另一份库比较。」
- 「用 szlcsc 读这份 PrjPcb，列出工程文档，然后检查指定 SchDoc 的器件和封装引用。」

## 库文件 MCP

按上游说明安装已验证的 **v1.4.0** 二进制。Windows 可下载发布页的 x86_64 ZIP，核对上游 SHA-256，并用 `gh attestation verify <ZIP 文件> --repo embedded-society/altium-designer-mcp` 验证构建证明。解压后在同目录新建配置文件，只填你希望 MCP 可访问的库目录。下面的目录是示例，建议先用拷贝出来的库验证。 [上游发布页](https://github.com/embedded-society/altium-designer-mcp/releases/tag/v1.4.0)

```json
{
  "allowed_paths": ["D:/AltiumLibraryWork"]
}
```

在 Codex 配置中加入独立服务，改成实际路径。

```toml
[mcp_servers.altium-offline-library]
command = 'D:/Tools/altium-designer-mcp/altium-designer-mcp.exe'
args = ['D:/Tools/altium-designer-mcp/library-config.json']
```

重连后用 `list_components` 和 `validate_library` 检查一个库。服务直接处理库文件，可读取符号引脚、封装链接和焊盘，支持库差异比较和离线写库。它也提供会改文件的工具，因此 `allowed_paths` 应只包含用户允许访问和改写的目录。 [上游工具与参数](https://github.com/embedded-society/altium-designer-mcp/blob/v1.4.0/docs/TOOLS.md)

## 工程、原理图和 PCB

使用已安装 `altium-monkey` 的 Python 3.12 环境，例如本项目安装 `.[altium]` 的环境。运行 `skills/szlcsc/scripts/inspect_saved.py`，或运行安装后的 `szlcsc` Skill 目录下的同一脚本。

| 文件 | 离线结果 |
| --- | --- |
| `.PrjPcb` | 已登记的成员文档清单，以此选定要进一步检查的文件。 |
| `.SchDoc` | 位号、Comment、引脚数量、封装引用等。 |
| `.PcbDoc` | 位号、封装、层面、位置以及焊盘、走线和网络数量。 |

如果全局已装 ScottJeong 的 `altium-library` 与 `altium-schematic-review`，前者补充库制作和核对方法，后者的 `audit_footprints.py` 与 `net_erc.py` 可检查封装引用及网络问题候选。本项目复用了这些离线流程的思想和脚本入口，核对的上游提交为 `586ca3db7a9ee3dc5935c38a0a89c7704b6b6b1f`。 [上游 Skills](https://github.com/scottjeong/altium-claude-skills/tree/586ca3db7a9ee3dc5935c38a0a89c7704b6b6b1f)

离线报告的输入是磁盘上的已保存文件。原理图网络初筛依赖解析器覆盖范围，判定缺陷时还需核对分件、层级连接与厂家资料。需要查看未保存变更、运行 AD 原生 ERC/DRC 或做 ECO 时，按现场任务使用 AD。
