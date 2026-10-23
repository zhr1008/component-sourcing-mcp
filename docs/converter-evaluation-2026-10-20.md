# Altium 转换器选型与 rc.3 修复

日期：2026-10-20。先检查替代项目，再决定本次修复方案。结论是存在替代项目，但目前没有经过验证、可覆盖本项目现有全部转换契约的直接替换项。本次保留独立的 altium-cruncher，通过项目侧后处理补齐关联、验证实际输出并修复安装。

| 方案 | 已核对能力 | 本轮验证与缺口 | 结论 |
|---|---|---|---|
| [npnp](https://github.com/yycx2016/npnp) | Rust SchLib/PcbLib、STEP 嵌入、批量与合并，Apache-2.0 | 检查源码 `ba0cd6710497535a08009e63af910d8d7744a6e1`、CLI 与 exporter 回归；当前镜像仓库没有可下载 Release。CLI 不支持离线 EasyEDA JSON 输入、现有引脚可见性/方向选项，也没有同等转换质量/3D placement 报告。本轮未编译或宣称生成文件验证通过 | 有后续适配价值；不能直接替换并保持现有 Tool 契约 |
| [jlc2ad](https://github.com/NatsumiAi/jlc2ad) | Python SchLib/PcbLib/LibPkg、模型关联、3D 下载嵌入、追加，MIT | 在 `7bb2d0f83790430d9aae892ecc2f67755c348b9d` 实际转换 C5291487 与 C2040，CLI 成功、产生 3/57 pin/pad 和两组模型；但本项目使用的 altium-monkey 2026.9.22 读取 SchLib FileHeader 报 `SchLibContainerError: record payload exceeds stream`，会阻断后续扫描/属性写回。主流程还会 catch 后跳过单个器件，不具备相同失败报告契约 | 本轮实际互操作检查未通过；未据此宣称 Altium 应用一定无法打开 |
| [AltiumSharp](https://github.com/issus/AltiumSharp) | .NET 的 Altium 文件读写、渲染基础库 | 是底层库，需要额外实现 EasyEDA 映射、命令行、质量报告及部署；本轮未开发 .NET 适配器或对生成文件做实测 | 属于后续重新实现方案，不能直接接替现有 CLI |
| 独立 altium-cruncher 2026.9.7 | 已有离线输入、显示策略、3D、质量报告、任意库合并 | 使用独立 venv 保留其自己的 altium-monkey 2026.9.7；MCP 侧用已验证的 2026.9.22 读写输出。新增真实库关联及保存后验证 | 本次继续使用，未修改或打包第三方源码 |

## 已修复问题

1. `_pad_row` 先读明确的 mil 属性；只有原始内部尺寸时除以 10000，覆盖真实二进制 PcbLib 的读取测试，避免 SOT23 焊盘高度从 0.532 mm 变成 5320 mm。
2. 刚生成的符号缺少封装关联时，只在实际 PcbLib 候选唯一时建立 implementation。歧义或冲突拒绝处理；临时写出、重读验证后原子替换，并写入 `szlcsc-footprint-links.json`。自动 smoke 必须通过该项。已有用户库的整理/补全流程不调用此后处理。
3. `<$>` 多图字段归一化成有效单个 HTTP(S) URL，下载仍检查域名。批量下载的 `successful` 只计算完整成功，不再将嵌套图片错误算作成功。
4. 属性写回返回的 `library_write_performed`、`ok` 与实际保存、无动作或失败状态一致；图元保持通过真实库写回前后对比验证。
5. 机械核验要求明确 `pass=true`、expected、actual 和页码；缺失/字符串/数值真值不能把器件置为 verified。
6. no_fee 的一个库请求失败时返回可用结果、`complete=false` 及结构化缺失范围；两者均失败时保持 MCP 错误。不更换身份、不破解上游访问控制。
7. Windows 安装器不再依赖 uv 工具环境，固定安装到托管目录并检查每个原生命令的退出码。发现顺序为显式环境变量、托管目录、PATH；显式无效路径不会静默回退。

## 验证方法

`pytest` 包含真实 altium-monkey 库读写、尺寸和幂等关联验证，以及缺失/冲突关联、部分下载、no_fee 部分失败、属性保存失败和机械证据不完整回归。

`scripts/test_installed_functional.py` 启动 Codex 配置中的实际已安装 EXE，检查版本、wheel 文件一致性和 45 个 Tool，生成每次参数/响应/失败断言证据。测试文件、缓存、迁移和属性写回均限制在指定独立根目录。自动通过不替代制造商 Datasheet 和 Altium 完整制造验收。
