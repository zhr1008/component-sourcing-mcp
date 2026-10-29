# 多供应商接入核查与实施边界

**v0.8.0-rc.1 更新：** 通用模型、注册层、4 个统一 Tool、立创/JLCPCB 兼容适配、Mouser 和 DigiKey 客户端现已实现；API 申请及配置见 [supplier-setup.md](supplier-setup.md)。以下正文保留 2026-10-23 立项时的调研与设计背景，其中“尚未实施”描述属于该历史时点。华秋仍需获批协议后实现，Mouser/DigiKey 的真实授权联调尚未在当前维护环境执行。

核查起始日期：2026-10-23。以下是官方接口与当前代码的可行性结论，
不表示所有供应商已经在 MCP 中完成接入或通过真实账户测试。

## 核查结果

| 平台 | 官方依据与能力 | 接入条件 | 当前状态 |
|---|---|---|---|
| 国内立创 SZLCSC | 现有精确编号、搜索、库存、CNY 数量报价、参数及资料工具 | 遵守上游访问限制 | 已实现，保留既有工具兼容 |
| JLCPCB 装配库 | 现有基础/优选/扩展库检索与分类 | 部分接口可能受访问限制 | 已实现，单独标识装配库存池 |
| 贸泽 Mouser | [Search API](https://www.mouser.com/en/api-search/) 与 [V1 OpenAPI](https://api.mouser.com/api/docs/V1)：关键词、料号、库存、阶梯价格、MOQ、倍数、交期、生命周期和 Datasheet | My Mouser 账户、Search API Key、相应条款 | **可按官方协议实施**；本轮未持有授权 Key，未做真实授权查询 |
| 华秋 Huaqiu | [官网](https://www.hqchip.com/)“终端 API 申请”指向 [用户中心 OpenAPI](https://www.hqchip.com/mycenter/account/openapi) | 从官方渠道取得权限、接口协议和鉴权资料 | **确认存在官方申请入口**；公开页面未提供可完整核对的端点、签名、返回字段及配额，尚不能宣称库存/价格接口已可用 |
| 得捷 DigiKey | [Product Information V4](https://developer.digikey.com/products/product-information-v4/productsearch)：搜索、详情、价格、分类与按数量/包装报价 | 开发者账号、注册应用、认证及所需产品权限 | 可作为下一批官方适配器；尚未注册应用或测试账户 |

贸泽官方页面当前公布：每次最多 50 条、每分钟最多 30 次、每日最多 1,000 次。
实施时仍以实际账户配额和响应为准。带制造商的搜索使用 V2，不沿用 OpenAPI
中已经标为 deprecated 的 V1 同名接口。

确认的基础查询契约：

| 调用 | 请求结构 | 必须处理的事项 |
|---|---|---|
| `POST https://api.mouser.com/api/v1/search/keyword` | `SearchByKeywordRequest`：`keyword`、`records`、`startingRecord`、`searchOptions` | 每次不超过 50 条，验证分页偏移与边界；官方 `apiKey` 查询参数必须从日志和缓存标识中排除 |
| `POST https://api.mouser.com/api/v1/search/partnumber` | `SearchByPartRequest`：`mouserPartNumber`、`partSearchOptions` | 使用官方 Exact 选项后仍校验返回身份，不把模糊结果第一项自动绑定为确定器件 |

库存优先读取 `AvailabilityInStock`，同时保留描述文本；无法可靠解析时保持 null。
`PriceBreaks.Price` 为字符串，每档有 `Currency`，不能将其他币种写入现有的
`unit_price_cny`。还需处理 HTTP 200 + `Errors` 数组的业务错误。

DigiKey 的 KeywordSearch 不返回 MyPricing；详情/报价在适用时才返回账户价格。
不能把普通搜索价格描述为用户专属价格。

## 工程改造边界

当前 `Part.lcsc_code`、`PriceTier.unit_price_cny`、`small_batch_price_cny` 与
`szlcsc_*` 工具围绕立创建立。建议增加通用层，保留当前 45 个工具及归档/Altium
流程的兼容，不逐个复制后再混合库存与币种：

```text
component_search / component_get / component_offers / component_providers
  -> provider registry
     -> SZLCSC compatibility adapter
     -> Mouser official Search API adapter
     -> Huaqiu adapter after approved API contract is available
     -> DigiKey adapter after application authentication is configured
  -> component identity + separate supplier offers
```

通用模型至少需要：

- `ComponentIdentity`：manufacturer、MPN、package、电气参数及匹配依据；供应商编号不充当跨平台身份。
- `SupplierOffer`：provider、supplier_sku、库存池、stock、MOQ、order_multiple、包装、交期、地区、税费口径、product_url、fetched_at 和 warnings。
- `PriceBreak`：min_quantity、十进制价格字符串、currency；如需换汇，额外记录汇率来源和时间，保留原币种。
- provider 状态：configured、authentication_required、ready、rate_limited、unsupported；缺少凭据只影响该供应商，不使整个 MCP 无法启动。

第一批建议实现通用只读模型/注册层、SZLCSC 兼容适配、Mouser 关键词/精确料号/
报价适配及 Mock 验证。华秋在取得协议前只登记待配置状态，不编造 API 或复用
网页登录 Cookie/签名；DigiKey 排下一批。当前文档没有把这些计划标为已实现。

## 缓存、调用与密钥

- 采购记录键使用 provider + supplier_sku；跨平台比较以明确的 manufacturer + MPN + package 为依据，C 编号仅属于立创。
- 每个平台分别执行限流、日配额和冷却。贸泽公开配额要求约两秒的最小间隔，并预留边界余量。
- 缓存隔离 provider、地区、币种及账户报价上下文；库存/价格使用短 TTL，静态参数较长 TTL；失败不伪装成 0 库存。
- 复用 `SafeSession` 时，只有官方只读 Search POST 才可显式使用 `retry_safe=True`。不得将此重试行为应用到订单或购物车写入。
- API Key、签名、Cookie、OAuth Token 不进入 Tool 返回、异常文本、SQLite URL、缓存键原文、fixture 或日志。尤其不能原样把 Mouser 的带 Key URL 交给现有缓存层。
- 未提供凭据时只做 Mock 和配置诊断，不能将 provider 标为已联通。真实账户响应先过滤后留证，不提交原始数据。
- 各供应商的缓存、展示和再分发条款分别适用；查询可用不等于允许建立公开镜像数据库。

## 验收范围

离线验证覆盖身份歧义、分页边界、币种/地区/账户隔离、MOQ/倍数、缺失或本地化
价格、HTTP 200 业务错误、401/403/429、超时、只读重试和密钥脱敏。随后再用用户
自行配置的授权凭据验证搜索、精确查料、库存和阶梯价，分别记录每个平台的结果。
