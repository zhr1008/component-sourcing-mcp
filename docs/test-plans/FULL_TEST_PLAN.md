# SZLCSC MCP v0.5.0 完整测试规范

## 测试目标

> 本文件是 Release 验收的**规范性测试要求**，不是历史测试结果。Agent 不得仅凭 `pytest passed` 宣称完整 Release PASS。
> 所有实机项必须记录 PASS / FAIL / SKIP / NOT RUN；SKIP 必须写明阻塞依赖。网络、Altium、人工检查项不能用单元测试替代。
> 结果报告必须逐项保留失败器件、C 编号、URL/路径、错误阶段和错误消息，不允许只给汇总数字。


验证 SZLCSC MCP v0.5.0 及后续兼容版本以下能力是否可以在真实 Windows + 国内网络环境正常工作：

- 国内立创商城器件检索
- C 编号 / MPN 查询
- 国内库存与人民币阶梯价格获取
- 参数化器件筛选
- 替代料搜索与评分
- 器件参数比较
- Datasheet / 商品图片下载
- 批量 Datasheet 同步
- 包装袋二维码解析
- EasyEDA Symbol / Footprint / 3D 资产获取
- Altium SchLib / PcbLib 生成
- STEP 3D Body 附着
- Altium 转换质量检查
- 器件库批量管理
- CAD 去重
- 国内价格库存增量刷新
- 分类合并 SchLib / PcbLib
- 网络错误、字段缺失、重复器件等异常处理

---

# 0. 测试环境记录

- [ ] 记录 Windows 版本
- [ ] 记录 Python 版本
- [ ] 记录 SZLCSC MCP 版本，应与待测 Release/`pyproject.toml` 一致，当前为 `0.5.0`
- [ ] 记录 MCP 客户端：Antigravity / Claude Code / Cursor / 其他
- [ ] 记录 `altium-cruncher` 是否安装
- [ ] 记录 `altium-cruncher version`（失败时可补测 `--version` 兼容回退）
- [ ] 创建独立测试目录，例如：

```text
D:\SZLCSC_MCP_TEST\
```

建议目录：

```text
D:\SZLCSC_MCP_TEST\
├─ downloads\
├─ datasheets\
├─ eda\
├─ altium\
└─ library\
```

---

# 1. MCP 基础启动测试

## 1.1 MCP Server 启动

- [ ] MCP Server 能正常启动
- [ ] MCP 客户端能发现 SZLCSC MCP
- [ ] Agent 可以看到全部 MCP tools
- [ ] 确认工具数量至少为 25 个（v0.5.0 当前为 25 个）
- [ ] 启动过程中无 Python traceback
- [ ] 连续重启 3 次均能正常工作

记录：

```text
PASS / FAIL:
发现的 tools 数量：
错误日志：
```

---

# 2. `szlcsc_healthcheck`

执行：

```text
调用 szlcsc_healthcheck
```

检查：

- [ ] 能访问 SZLCSC
- [ ] 搜索接口正常
- [ ] 返回明确健康状态
- [ ] 不应出现验证码绕过、登录模拟等行为
- [ ] 网络失败时应返回错误信息，而不是伪造成功

记录：

```text
PASS / FAIL:
返回内容：
网络耗时：
异常：
```

---

# 3. 精确 C 编号查询

建议测试：

```text
C2040
C5291487
C6119790
```

执行：

```text
szlcsc_get_part(identifier="C2040")
```

检查：

- [ ] 正确识别 C 编号
- [ ] 返回 MPN
- [ ] 返回品牌
- [ ] 返回中文商品名称/描述
- [ ] 返回封装
- [ ] 返回分类
- [ ] 返回商品 URL
- [ ] 返回国内库存
- [ ] 返回人民币价格
- [ ] 返回阶梯价格
- [ ] 返回 Datasheet URL
- [ ] 返回参数字典
- [ ] `data_quality` 能说明哪些字段成功获得
- [ ] 缺少字段时返回 `null/unknown`
- [ ] 缺少字段绝不能自动写成 `0`

重点检查：

```text
price = unknown
```

不能被解释成：

```text
price = ¥0
```

库存同理。

---

# 4. MPN 查询

选择几个明确型号，例如：

```text
OPA333AIDBVR
INA181A2IDBVR
STM32F407VET6
ADS1115IDGSR
```

执行：

```text
szlcsc_get_part(identifier="OPA333AIDBVR")
```

检查：

- [ ] 能通过 MPN 搜索
- [ ] 能找到对应 C 编号
- [ ] 多个结果时不会随意选择完全无关器件
- [ ] 返回结果中 MPN 与目标一致或明确说明非完全匹配
- [ ] 多个 C 编号对应同一 MPN 时可以识别出来

---

# 5. 普通关键词搜索

测试：

```text
3.3V 电压基准
INA181
10k 0603
100nF 50V 0603 X7R
STM32F407
RS485 收发器 3.3V
```

执行示例：

```text
szlcsc_search(
    query="100nF 50V 0603 X7R"
)
```

检查：

- [ ] 搜索结果与关键词相关
- [ ] 返回多个候选
- [ ] 包含 C 编号
- [ ] 包含型号
- [ ] 包含封装
- [ ] 尽可能返回价格
- [ ] 尽可能返回库存
- [ ] 有货器件应优先于明显缺货器件
- [ ] 不应因为便宜而把完全不匹配器件排第一

---

# 6. 价格过滤测试

执行类似：

```text
搜索 3.3V 电压基准
最高单价 5 元
```

或者对应 MCP 参数。

检查：

- [ ] `max_price=5` 可以过滤明显超过 5 元器件
- [ ] 使用的是最低采购数量对应的阶梯价
- [ ] 不会拿 1000pcs 批量价冒充 1pcs 价格
- [ ] 如果没有 1pcs 阶梯，应明确显示 MOQ
- [ ] 未成功获得价格的器件不能直接认为满足价格限制

特别测试：

```text
max_price=3
```

确认价格未知器件的处理是否合理。

---

# 7. 库存过滤测试

执行：

```text
in_stock=true
```

检查：

- [ ] 有明确库存的器件保留
- [ ] 明确库存为 0 的器件过滤
- [ ] 库存未知不能被误判为库存 0
- [ ] 如果因为接口原因无法获得库存，要在 `data_quality` 中说明

---

# 8. 参数化搜索 `szlcsc_parametric_search`

## 8.1 电压基准

执行：

```text
szlcsc_parametric_search(
    query="3.3V 电压基准",
    package="SOT-23",
    in_stock=true,
    max_price=5,
    constraints={
        "output_voltage": "=3.3V",
        "accuracy": "<=0.2%",
        "tempco": "<=30ppm/°C"
    }
)
```

检查：

- [ ] 能识别 `=3.3V`
- [ ] 能识别 `<=0.2%`
- [ ] 能识别 `<=30ppm/°C`
- [ ] 每项参数有实际值
- [ ] 每项参数有目标值
- [ ] 每项有 PASS/FAIL 或 equivalent 状态
- [ ] 缺参数时不能自动判定为通过
- [ ] 输出应可以解释为什么器件被推荐

---

# 9. 工程单位解析测试

逐个测试：

```text
3.3V
500mA
20uA
10kΩ
0.1%
20ppm/°C
100nF
10uF
2.2uH
50V
2.5V~5.5V
```

检查：

- [ ] mA 转换正确
- [ ] µA/uA 转换正确
- [ ] kΩ 转换正确
- [ ] nF/uF 转换正确
- [ ] ppm/°C 解析正确
- [ ] `20ppm/°C` 绝不能解析成 `2e-11`
- [ ] 范围 `2.5V~5.5V` 能识别上下限

---

# 10. 参数比较 `szlcsc_compare_parts`

选 2~4 个相同用途器件。

例如：

```text
Cxxxx
Cyyyy
Czzzz
```

执行：

```text
szlcsc_compare_parts(...)
```

检查：

- [ ] MPN 并排显示
- [ ] 品牌并排显示
- [ ] 封装并排显示
- [ ] 库存并排显示
- [ ] 价格并排显示
- [ ] 关键参数并排显示
- [ ] 不同参数有明显标识
- [ ] 缺失参数显示 unknown/null
- [ ] 不自动把缺失参数当成相同

---

# 11. 替代料 `szlcsc_find_alternatives`

选择一个常用 IC。

执行：

```text
szlcsc_find_alternatives(...)
```

检查：

- [ ] 能返回候选替代件
- [ ] 显示原器件
- [ ] 显示候选 MPN
- [ ] 显示 C 编号
- [ ] 显示封装
- [ ] 显示库存
- [ ] 显示价格
- [ ] 有替代评分
- [ ] 给出评分原因
- [ ] 相同封装优先
- [ ] 有货优先
- [ ] 参数匹配度影响评分
- [ ] 不宣称“pin-to-pin 兼容”除非确有证据
- [ ] 应提示最终仍需核 Datasheet / 引脚

---

# 12. 单器件下载 `szlcsc_download_part`

执行：

```text
szlcsc_download_part(
    identifier="C2040",
    output_root="D:/SZLCSC_MCP_TEST/downloads"
)
```

检查是否生成：

```text
C2040/
├─ part.json
├─ README.md
├─ datasheet.pdf
└─ image.*
```

检查：

- [ ] `part.json` 可正常解析
- [ ] README 内容正确
- [ ] Datasheet 可以正常打开
- [ ] PDF 文件开头为 `%PDF-`
- [ ] 不会把 HTML 错误页保存成 PDF
- [ ] 图片可以正常打开
- [ ] 再次下载不会产生大量无意义重复文件

---

# 13. 批量器件下载

执行：

```text
szlcsc_download_parts(
    identifiers=[
        "C2040",
        "C5291487"
    ]
)
```

检查：

- [ ] 两个器件分别建立目录
- [ ] 单个器件失败不会导致整个批次丢失
- [ ] 返回每个器件的成功/失败状态
- [ ] 重复 C 编号不会重复处理

---

# 14. Datasheet 批量同步

准备：

```text
C2040
C5291487
OPA333AIDBVR
ADS1115IDGSR
```

执行：

```text
szlcsc_sync_datasheets(...)
```

检查：

- [ ] 生成 `manifest.json`
- [ ] 每个器件有状态记录
- [ ] 已下载文件有路径
- [ ] 失败项有错误原因
- [ ] 再执行一次时已有成功文件不会全部重下
- [ ] 中断后重新执行可以继续
- [ ] PDF 内容有效

---

# 15. 二维码解析 `szlcsc_parse_barcode`

测试输入：

```text
{pc:C6119790,pm:CGA0402X5R224K250GT,qty:100,mc:,cc:1,pdi:200616857,hp:null}
```

检查：

- [ ] `pc = C6119790`
- [ ] `pm = CGA0402X5R224K250GT`
- [ ] `qty = 100`
- [ ] 可继续查询 C6119790 的商品详情
- [ ] 非法二维码字符串不会导致 MCP 崩溃

---

# 16. EasyEDA EDA 资产 `szlcsc_eda_assets`

执行：

```text
szlcsc_eda_assets(
    identifier="C2040",
    output_root="D:/SZLCSC_MCP_TEST/eda",
    include_step=true
)
```

检查是否能获得：

- [ ] EasyEDA 原始 JSON
- [ ] Symbol 数据
- [ ] Footprint 数据
- [ ] Symbol SVG
- [ ] Footprint SVG
- [ ] 3D UUID
- [ ] OBJ，如该器件提供
- [ ] STEP，如该器件提供
- [ ] `eda_manifest.json`

STEP 检查：

- [ ] STEP 文件非 HTML
- [ ] STEP 文件大小合理
- [ ] STEP 可以被 CAD 软件打开
- [ ] 3D 原点信息被记录
- [ ] Z offset 被记录
- [ ] rotation 被记录

---

# 17. 无 3D 模型器件测试

找一个没有 EasyEDA 3D 模型的器件。

检查：

- [ ] Symbol/Footprint 仍可获取
- [ ] 不因为没有 STEP 导致整个任务失败
- [ ] 明确报告 `3D unavailable`
- [ ] 不生成假 STEP

---

# 18. `szlcsc_altium_status`

执行：

```text
szlcsc_altium_status()
```

检查：

- [ ] 检测到 `altium-cruncher`
- [ ] 返回版本
- [ ] 检测 `easyeda-import`
- [ ] 检测 `merge`
- [ ] 如果没安装，应返回明确安装提示
- [ ] 不应因为没安装而导致整个 MCP 崩溃

---

# 19. 单器件 Altium 导入

执行：

```text
szlcsc_altium_import(
    identifier="C2040",
    output_root="D:/SZLCSC_MCP_TEST/altium",
    preview=true
)
```

检查是否生成：

- [ ] `.SchLib`
- [ ] `.PcbLib`
- [ ] STEP
- [ ] 转换报告
- [ ] preview SVG，如启用 preview

---

# 20. Altium SchLib 检查

在 Altium Designer 中打开生成的 `.SchLib`。

检查：

- [ ] 库可以正常打开
- [ ] Symbol 可显示
- [ ] 引脚数量正确
- [ ] 引脚编号正确
- [ ] 引脚名称正确
- [ ] 引脚位置基本合理
- [ ] Symbol 图形完整
- [ ] Designator 存在
- [ ] 没有明显乱码
- [ ] 没有严重图形偏移

记录截图。

---

# 21. Altium PcbLib 检查

打开 `.PcbLib`。

检查：

- [ ] PcbLib 可正常打开
- [ ] Pad 数量正确
- [ ] Pad 编号正确
- [ ] Pad 形状合理
- [ ] Pad 间距与 Datasheet/EasyEDA 一致
- [ ] 丝印基本合理
- [ ] Assembly/机械层没有明显异常
- [ ] 原点合理
- [ ] Pin 1 标识存在或合理

---

# 22. STEP 3D Body 检查

检查生成 PcbLib 中：

- [ ] 有 3D Body
- [ ] STEP 已嵌入或正确关联
- [ ] X/Y 位置正确
- [ ] Z 高度正确
- [ ] 旋转角正确
- [ ] 模型不是上下颠倒
- [ ] 模型不是偏离封装很远

如果报告为：

```text
needs_checking
```

确认 Agent 是否明确提醒人工检查。

---

# 23. `szlcsc_altium_review`

执行：

```text
szlcsc_altium_review(
    identifier="C2040",
    output_root="D:/SZLCSC_MCP_TEST/altium"
)
```

检查：

- [ ] pin_count
- [ ] unsupported_count
- [ ] source_pad_count
- [ ] generated_pad_count
- [ ] pad_count_match
- [ ] model_3d_attached
- [ ] model_3d_centered_fallback
- [ ] model_3d_placement_verdict
- [ ] concerns

要求：

- [ ] pad 数不一致时必须报警
- [ ] unsupported graphics > 0 时必须说明
- [ ] 3D fallback 时必须提示
- [ ] 不能把 `needs_checking` 说成完全正确

---

# 24. 批量 Altium 导入

执行 3~5 个不同类型器件：

```text
MCU
运放
电阻
电容
连接器
```

检查：

- [ ] 每个器件有独立结果
- [ ] 一个失败不会终止整个批次
- [ ] 每个器件有质量报告
- [ ] 输出目录结构清晰
- [ ] 无文件互相覆盖

---

# 25. 器件库 `szlcsc_library_add`

执行：

```text
szlcsc_library_add(
    identifiers=[
        "C2040",
        "C5291487"
    ],
    library_root="D:/SZLCSC_MCP_TEST/library",
    altium_output_root="D:/SZLCSC_MCP_TEST/altium",
    import_missing=true,
    dedupe_cad=true
)
```

检查：

- [ ] 生成 `library-index.json`
- [ ] `schema_version` 存在
- [ ] `revision` 存在
- [ ] 每个 C 编号都有采购记录
- [ ] MPN 被记录
- [ ] 封装被记录
- [ ] 分类被记录
- [ ] 价格被记录
- [ ] 库存被记录
- [ ] CAD 状态被记录

---

# 26. CAD 去重测试

找两个：

```text
相同 MPN
相同封装
不同 C 编号
```

加入 library。

检查：

- [ ] 两个 C 编号都存在
- [ ] 只有一份 CAD 主副本
- [ ] 一个为 `cad_owner`
- [ ] 另一个为 `cad_shared=true`
- [ ] 两者动态采购数据仍独立

---

# 27. 同 MPN 不同封装测试

加入：

```text
同系列/同 MPN family
但一个 SOT-23
一个 SOIC-8
```

检查：

- [ ] 不共享 CAD
- [ ] 分别生成 SchLib/PcbLib 或分别建立 CAD 键
- [ ] 不能因为 MPN 相同而错误合并封装

---

# 28. CAD 文件被人工删除后的恢复测试

步骤：

- [ ] 添加一个器件
- [ ] 确认索引认为 CAD 存在
- [ ] 手动删除对应 `.SchLib/.PcbLib`
- [ ] 再运行 library status/add

检查：

- [ ] MCP 能发现真实文件已经不存在
- [ ] 不继续把它作为合法 CAD owner
- [ ] 可以重新生成或重新指派 owner
- [ ] 索引最终保持一致

---

# 29. `szlcsc_library_status`

执行：

```text
szlcsc_library_status(
    library_root="D:/SZLCSC_MCP_TEST/library"
)
```

检查：

- [ ] 总器件数量
- [ ] 分类统计
- [ ] CAD owner 数
- [ ] shared CAD 数
- [ ] duplicate MPN
- [ ] duplicate CAD key
- [ ] cad_review_required
- [ ] revision

---

# 30. `szlcsc_library_sync`

先记录某器件：

```text
价格
库存
Datasheet
```

执行：

```text
szlcsc_library_sync(
    library_root="D:/SZLCSC_MCP_TEST/library"
)
```

检查：

- [ ] 国内价格刷新
- [ ] 国内库存刷新
- [ ] 商品 URL 刷新
- [ ] Datasheet 信息刷新
- [ ] MOQ/包装刷新
- [ ] revision 更新
- [ ] 没有重新生成 SchLib
- [ ] 没有重新生成 PcbLib

---

# 31. MPN / 封装变化保护

人为修改测试 fixture 或模拟数据，使：

```text
原 MPN != 新 MPN
```

或：

```text
原封装 != 新封装
```

同步。

检查：

- [ ] 标记 `cad_review_required=true`
- [ ] 不自动继续把旧 CAD 当作完全可靠
- [ ] 不因为价格库存同步而偷偷覆盖 CAD

---

# 32. 分类库合并 `szlcsc_library_merge`

加入多个分类器件后执行：

```text
szlcsc_library_merge(
    library_root="D:/SZLCSC_MCP_TEST/library"
)
```

检查：

- [ ] 按分类建立目录
- [ ] 中文分类目录正常
- [ ] `.SchLib` 成功合并
- [ ] `.PcbLib` 成功合并
- [ ] 同一共享 CAD 不重复合并
- [ ] merge provenance/report 存在
- [ ] 合并库可以在 Altium 中打开

---

# 33. 中文分类测试

测试例如：

```text
电源管理 / DC-DC
接口芯片
模拟芯片
连接器
```

检查：

- [ ] Windows 路径合法
- [ ] 中文不被全部删掉
- [ ] `/` 等非法字符被替换
- [ ] 不会全部变成 `Uncategorized`

---

# 34. 重复执行幂等性测试

连续运行 3 次：

```text
library_add
library_sync
library_merge
```

检查：

- [ ] 不产生大量重复条目
- [ ] 不无限复制 CAD
- [ ] 不无限复制 Datasheet
- [ ] index 不损坏
- [ ] revision 合理增长
- [ ] merge 输出稳定

---

# 35. SQLite Cache 测试

同一个器件连续查两次。

检查：

- [ ] 第二次请求明显减少或使用缓存
- [ ] cache status 可以查看
- [ ] 缓存失效后能重新请求
- [ ] 网络失败时近期 stale cache 可以作为降级
- [ ] 明确告诉 Agent 数据来自缓存

---

# 36. 网络断开测试

步骤：

- [ ] 正常查询器件一次
- [ ] 断网
- [ ] 再查询同一器件
- [ ] 查询一个从未缓存的新器件

检查：

已缓存：

- [ ] 可以使用缓存或明确说明缓存状态

未缓存：

- [ ] 返回网络错误
- [ ] 不伪造数据
- [ ] MCP Server 不崩溃

---

# 37. 错误 C 编号测试

测试：

```text
C0
C999999999999999
ABC123
空字符串
```

检查：

- [ ] 返回明确错误
- [ ] 不产生 traceback 泄漏给普通用户
- [ ] 不生成空目录垃圾
- [ ] 不进入无限重试

---

# 38. 非法输出路径测试

测试：

```text
不存在盘符
只读目录
无权限目录
```

检查：

- [ ] 返回明确文件系统错误
- [ ] 不损坏 index
- [ ] 不生成半截 JSON
- [ ] 临时文件尽可能被清理

---

# 39. 下载中断测试

下载 Datasheet / STEP 时人工断网。

检查：

- [ ] 不留下被误认为完整文件的损坏 PDF
- [ ] 不留下被误认为有效的损坏 STEP
- [ ] manifest 标记失败
- [ ] 恢复网络后可以重试

---

# 40. 并发 / Agent 重复调用测试

让 Agent 快速连续查询同一个器件 5~10 次。

检查：

- [ ] 不大量重复请求 SZLCSC
- [ ] 无 SQLite locked
- [ ] 无 index JSON 损坏
- [ ] 无文件互相覆盖
- [ ] 无 MCP Server crash

---

# 41. Agent 自然语言选型测试

直接对 Agent 说：

```text
给我找一个 3.3V 电压基准：
SOT-23；
初始精度不差于 ±0.2%；
温漂不大于 30ppm/℃；
国内立创现货；
1片价格不超过 5 元。
优先推荐便宜器件。
```

检查 Agent 是否：

- [ ] 主动调用 SZLCSC MCP
- [ ] 使用参数化检索而不是纯关键词搜索
- [ ] 核对价格
- [ ] 核对库存
- [ ] 核对参数
- [ ] 明确指出参数缺失项
- [ ] 不把缺数据器件当作完全满足
- [ ] 给出推荐理由
- [ ] 给出 C 编号

---

# 42. Agent 替代料测试

自然语言：

```text
给 Cxxxx 找能替代的器件。
优先 pin-to-pin、相同封装、国内有货、便宜。
```

检查：

- [ ] Agent 调用替代料工具
- [ ] 比较参数
- [ ] 比较封装
- [ ] 比较库存
- [ ] 比较价格
- [ ] 不未经验证声称 pin-to-pin
- [ ] 提醒核 Datasheet 引脚定义

---

# 43. Agent 自动入库测试

自然语言：

```text
把 C2040 加进我的 Altium 器件库。
如果已有相同 MPN+封装就复用。
没有的话自动生成 SchLib、PcbLib 和 3D。
最后检查转换质量。
```

检查：

- [ ] 查询器件
- [ ] 检查 library index
- [ ] 检查 CAD 去重
- [ ] 必要时调用 Altium import
- [ ] 调用 review
- [ ] 更新 library index
- [ ] 返回最终文件位置
- [ ] 返回质量警告

---

# 44. Agent 批量入库测试

自然语言：

```text
把下面器件全部加入我的库：
Cxxxx
Cyyyy
Czzzz

相同 CAD 不要重复生成。
```

检查：

- [ ] 批量执行
- [ ] 去重有效
- [ ] 单个失败不影响其他器件
- [ ] 最后总结成功/失败数量

---

# 45. 最终人工 Altium 抽查

至少抽查：

- [ ] 1 个两脚无源器件
- [ ] 1 个 SOT-23
- [ ] 1 个 SOIC
- [ ] 1 个 QFN
- [ ] 1 个 MCU/QFP
- [ ] 1 个连接器

每个器件检查：

- [ ] Symbol 正确
- [ ] Pin 编号正确
- [ ] Footprint pad 数正确
- [ ] Footprint 尺寸正确
- [ ] Pin 1 正确
- [ ] STEP 位置正确
- [ ] SchLib/PcbLib 可正常保存
- [ ] 放入原理图/PCB 后无明显异常

---

# 46. 建议重点测试器件类型

优先覆盖：

- [ ] 0603 电阻
- [ ] 0603 MLCC
- [ ] SOT-23-5 运放
- [ ] SOIC-8
- [ ] TSSOP
- [ ] QFN
- [ ] LQFP100 MCU
- [ ] JST / 国产连接器
- [ ] 电感
- [ ] MOSFET
- [ ] TVS
- [ ] RS485/CAN 收发器
- [ ] ADC
- [ ] DAC
- [ ] 电压基准

---

# 47. Agent 最终应输出的测试报告格式

要求 Agent 最终整理：

```text
SZLCSC MCP v0.4.0 测试结果

环境：
- Windows:
- Python:
- MCP Client:
- altium-cruncher:

总测试：
PASS:
FAIL:
SKIP:

严重问题：
1.
2.

一般问题：
1.
2.

接口字段异常：
1.
2.

SZLCSC 在线接口异常：
1.
2.

EasyEDA 资产异常：
1.
2.

Altium 转换异常：
1.
2.

器件库管理异常：
1.
2.

建议修改：
1.
2.
3.

测试器件：
- Cxxxx
- Cyyyy
- Czzzz
```

---

# 48. BUG 报告要求

每个 FAIL 请保留：

- [ ] 测试编号
- [ ] MCP tool 名称
- [ ] 完整输入参数
- [ ] 实际返回 JSON / 文本
- [ ] Python traceback（如果有）
- [ ] 对应 C 编号
- [ ] SZLCSC 商品链接
- [ ] 输出文件路径
- [ ] 失败文件
- [ ] Altium 截图，如涉及 CAD
- [ ] 预期结果
- [ ] 实际结果

不要只写：

```text
不能用
```

应该写成：

```text
测试 22：STEP 3D Body

C2040
工具：szlcsc_altium_import

预期：
STEP 位于 footprint 中心且高度正确。

实际：
STEP 成功嵌入，但绕 Z 轴旋转 90°，
模型中心相比 footprint 偏移约 1.2mm。

Report:
model_3d_attached=true
model_3d_placement_verdict=needs_checking
```

这样后续可以直接定位并修代码。


# 49. v0.4.5 双视图归档结构

入库至少 2 个不同类目的器件，检查：

- [ ] `classified/datasheets/<类目>/类型-型号-C编号.pdf`
- [ ] `classified/schlib/<类目>/类型-型号-C编号.SchLib`
- [ ] `classified/pcblib/<类目>/类型-型号-C编号.PcbLib`
- [ ] `classified/3d/<类目>/类型-型号-C编号.step`（有 3D 时）
- [ ] `flat/` 存在同名浏览镜像
- [ ] 中文文件名在 Windows/Altium 中可正常访问
- [ ] 完整多级类目存在时逐级建目录；只有末级类目时不伪造上级类目
- [ ] 删除临时 `altium/Cxxxx` 后，classified CAD 仍可被复用和 merge

# 50. `szlcsc_library_search`（v0.5.0）

至少加入电压基准、电流检测、MCU 三类器件。检查：

- [ ] 完全离线运行，不访问 SZLCSC
- [ ] C 编号精确查询可命中
- [ ] MPN 精确查询优先于仅分类/品牌文本命中
- [ ] `category` / `brand` / `package` 过滤正确
- [ ] `in_stock=true/false` 正确
- [ ] `has_datasheet` / `has_schlib` / `has_pcblib` / `has_step` 正确
- [ ] `cad_review_required` 可筛选
- [ ] `only_cad_owners=true` 不重复返回共享 CAD 成员
- [ ] `sort_by=relevance/price/stock/mpn` 行为稳定
- [ ] 价格 unknown 不被当作 0 元排到最前
- [ ] 返回 `assets`、`cad_owner`、`cad_shared`、`match_score`

# 51. `szlcsc_library_audit`（v0.5.0）

在测试副本中人为制造以下故障：删除 flat 文件、篡改 flat 内容、删除 classified 文件、改错 `cad_owner`。检查：

- [ ] 审计默认只读，不修改任何文件
- [ ] 缺 flat 能报告 `repairable=true`
- [ ] flat 与 classified 内容不同能通过 SHA256 检出
- [ ] classified 主文件缺失必须是 error，不能自动当成可修复
- [ ] `cad_owner` 不存在能报告 error
- [ ] `cad_owner` 与 `cad_key` 不一致能报告 error
- [ ] `cad_shared` 标志错误能报告 warning
- [ ] `altium.has_schlib/has_pcblib=true` 但实际无文件时能报告 error
- [ ] 返回 `severity_counts`、`repairable_count` 和逐条 issue

# 52. `szlcsc_library_repair`（v0.5.0）

先执行 `dry_run=true`：

- [ ] 返回计划动作
- [ ] 不创建/覆盖任何文件
- [ ] 不修改 `library-index.json` revision

再对测试副本执行 `dry_run=false`：

- [ ] 从 classified 恢复缺失 flat 镜像
- [ ] 覆盖内容损坏的 flat 镜像
- [ ] 修复后的 flat SHA256 与 classified 一致
- [ ] 重建 `mpn_index/cad_index`
- [ ] 重新协调 `cad_owner/cad_shared`
- [ ] 不重新下载 Datasheet
- [ ] 不重新运行 EasyEDA/Altium 生成
- [ ] classified 主文件缺失只进入 `unresolved`，绝不伪造文件
- [ ] 修复后自动给出 `post_audit`

# 53. Release 报告格式要求

最终测试报告必须至少包含：

```text
Version:
Commit / tag:
Environment:
pytest: X / X passed
MCP tool count:
Live SZLCSC: PASS/FAIL/SKIP
Datasheet live: PASS/FAIL/SKIP
EasyEDA 3D live: PASS/FAIL/SKIP
Altium toolchain: PASS/FAIL/SKIP
Altium smoke: PASS/FAIL/SKIP
Altium manual inspection: MANUAL_PASS / MANUAL_FAIL / NOT_RUN
Library archive: PASS/FAIL
Library search: PASS/FAIL
Library audit/repair: PASS/FAIL
FAIL items: [...]
SKIP items + blockers: [...]
Known warnings/concerns: [...]
```

**禁止**：把未执行的网络/Altium/人工项目算入 PASS 数量；仅凭 `pytest 67 passed` 就写“全部功能 100% 可用”。
