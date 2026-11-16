# 离线处理 Altium 库与已保存的工程

在 Codex 里用 `$component-sourcing` 发起任务即可。先指定要看的是哪份磁盘文件，库要写入时注明目标副本。

- 「用 $component-sourcing 核对这份 PcbLib 里指定封装的焊盘，并和另一份库比较。」
- 「用 $component-sourcing 读这份 PrjPcb，列出工程文档，然后检查指定 SchDoc 的器件和封装引用。」

## 一键下载、转换并核对

明确给出国内立创 C 号并要求「一键生成 AD 库」或「下载封装」时，Skill 调用 `szlcsc_altium_build`。Python 3.12+、`[altium]` 扩展和 `altium-cruncher` 就绪后，即可下载 EasyEDA 源数据和可用的 Datasheet、转换两种库、嵌入 STEP 并离线读回。它为每个 C 号生成一个独立目录，库文件、独立 STEP 和 PDF 使用 `完整型号_C号` 命名。文件名中的斜杠等非法字符转为下划线，报告保留原型号。同名目录已有产物时，核对现有产物或指定新的输出目录。

有目标工程时，从 `.PrjPcb` 确认对应的已保存 `.PcbDoc`，把其路径传给 `target_board`。流程只读取该板文件，报告列出解析到的物理层叠和已保存规则。若工程有多块板，先确定板文件。

核验使用产物里的 `verification.json`。Agent 实际阅读原厂 PDF，对照同一订购型号和封装的引脚表、机械尺寸及推荐焊盘图。再将带页码的独立期望值提交给 `szlcsc_altium_verify_bundle`，工具与读回值比较并更新报告。必要时按库规范、板厂规范或工程规范核对缺少原厂规定的项目。

| 核验项 | 依据与边界 |
| --- | --- |
| 引脚、焊盘 | 核对编号、名称、电气类型、尺寸、位置、所在层和孔径。坐标比较须统一毫米、原点和朝向。 |
| 阻焊、锡膏 | 记录跟随规则或手动设置，并核对读到的手动扩展。跟随规则时，实际开窗属于具体器件实例的有效规则，缺证据保持待核实。 |
| 机械层、3D | 对照库规范核对层用途，从保存的库中验证 3D Body 引用及嵌入模型，记录旋转与悬高。Pin 1、方向和贴板位置需要根据实际 3D 几何与原厂图取证。 |
| 工程层叠和规则 | 可比较保存的层序、厚度、材料、规则定义与规范。原生 DRC、项目级规则解析及实际间隙验证保留待核实。 |

查型号或做选型时无需调用该流程。该流程的下载需要网络，后续转换与读回无需启动 AD。结果的 `generated` 表示产物生成，`review_status` 表示证据核验进度，两者分别报告。

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

使用已安装 `altium-monkey` 的 Python 3.12 环境，例如本项目安装 `.[altium]` 的环境。运行 [inspect_saved.py](../skills/component-sourcing/scripts/inspect_saved.py)，或运行安装后的 `component-sourcing` Skill 目录下的同一脚本。

| 文件 | 离线结果 |
| --- | --- |
| `.PrjPcb` | 已登记的成员文档清单，以此选定要进一步检查的文件。 |
| `.SchDoc` | 位号、Comment、引脚数量、封装引用等。 |
| `.PcbDoc` | 位号、封装、层面、位置以及焊盘、走线和网络数量。 |

如果全局已装 ScottJeong 的 `altium-library` 与 `altium-schematic-review`，前者补充库制作和核对方法，后者的 `audit_footprints.py` 与 `net_erc.py` 可检查封装引用及网络问题候选。本项目复用了这些离线流程的思想和脚本入口，核对的上游提交为 `586ca3db7a9ee3dc5935c38a0a89c7704b6b6b1f`。 [上游 Skills](https://github.com/scottjeong/altium-claude-skills/tree/586ca3db7a9ee3dc5935c38a0a89c7704b6b6b1f)

离线报告的输入是磁盘上的已保存文件。原理图网络初筛依赖解析器覆盖范围，判定缺陷时还需核对分件、层级连接与厂家资料。需要查看未保存变更、运行 AD 原生 ERC/DRC 或做 ECO 时，按现场任务使用 AD。
