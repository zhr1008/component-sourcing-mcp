# 多供应商 API 申请与配置

适用于 v0.8 系列预发布版本。每位使用者自行申请 API 权限与凭据，仓库、安装包和测试不包含可用密钥。

| 数据源 | 官方申请渠道与当前实现 | 配置 |
|---|---|---|
| 国内立创 | 已实现公开查询，遵守上游访问限制 | 不需要本项目保存登录 Cookie |
| JLCPCB 装配库 | 已实现，库存池与国内零售分开 | 不需要本项目保存登录 Cookie |
| 贸泽 Mouser | 从 [Search API 页面](https://www.mouser.com/en/api-search/)登录自己的账户，申请 **Search API** Key；已实现关键词、精确料号、库存和原币阶梯价 | `MOUSER_API_KEY` 或 JSON 的 `mouser.api_key` |
| DigiKey | 从 [开发者门户](https://developer.digikey.com/)注册应用、订阅 Product Information V4；生产应用还需符合组织要求；已实现搜索、详情、包装报价及两方 OAuth | `DIGIKEY_CLIENT_ID` 和 `DIGIKEY_CLIENT_SECRET` 或 JSON 对应字段 |
| 华秋 | [用户中心 OpenAPI 申请入口](https://www.hqchip.com/mycenter/account/openapi)；当前提供申请/协议接入渠道，**尚无可执行适配器** | 先取得获批的接口、签名、字段及配额文档；仅填写 Key 不会启用该数据源 |

## 推荐：使用自己的私有 JSON

将根目录 `suppliers.example.json` 复制到自己的私有配置目录，命名为 `suppliers.json`，
在对应空字符串中填写申请到的凭据。未开通的平台保留空值。文件不要提交 Git 或上传公开附件。

```json
{
  "mouser": {"api_key": ""},
  "digikey": {
    "client_id": "",
    "client_secret": "",
    "sandbox": false,
    "site": "US",
    "language": "en",
    "currency": "USD",
    "account_id": ""
  }
}
```

`sandbox: true` 使用官方 Sandbox，其数据不能视为真实库存。`account_id` 只在确实具有
相应授权和报价需要时填写。币种/站点需符合账户支持范围；最终金额采用响应确认的实际币种。

在 MCP 环境中设置 `COMPONENT_SUPPLIERS_CONFIG` 指向私有文件，重连后调用
`component_providers` 查看状态。Codex 示例（替换自己的实际路径）：

```toml
[mcp_servers.components]
command = 'C:\src\component-sourcing-mcp\.venv\Scripts\python.exe'
args = ['-m', 'szlcsc_mcp', '--transport', 'stdio']
startup_timeout_sec = 30
tool_timeout_sec = 120

[mcp_servers.components.env]
PYTHONUTF8 = '1'
COMPONENT_SUPPLIERS_CONFIG = 'C:\ComponentPrivate\suppliers.json'
```

JSON 是本地明文凭据文件，应限制到自己的账户可读。Key、Secret、Token 不应发送到聊天、
Issues、PR 或公开日志。文件无效时只影响需要它的供应商，立创/JLCPCB 继续可用。

## 环境变量及覆盖规则

环境变量优先于文件，同名变量即使为空也会覆盖文件。配置在启动时读取，修改后需重连。

| 变量 | 含义 |
|---|---|
| `COMPONENT_SUPPLIERS_CONFIG` | 私有 JSON 的绝对路径；不设置时只读取环境变量 |
| `MOUSER_API_KEY` | Mouser Search API Key，不是其他 API 产品的 Key |
| `DIGIKEY_CLIENT_ID` / `DIGIKEY_CLIENT_SECRET` | 官方两方 `client_credentials` OAuth 的应用凭据 |
| `DIGIKEY_SITE` / `DIGIKEY_LANGUAGE` / `DIGIKEY_CURRENCY` | 默认 US / en / USD；最终使用响应确认值 |
| `DIGIKEY_SANDBOX` | `true` 或 `false`，默认 false |
| `DIGIKEY_ACCOUNT_ID` | 可选的已授权账户报价上下文 |
| `DIGIKEY_ACCESS_TOKEN` | 高级可选：与 Client ID 配合使用已有合法 Bearer Token，此模式不自动续期；推荐 Client Secret 模式 |

适配器不自动导入 `.netrc` 认证或系统代理，网络需能够访问官方 API。没有凭据时不发送对应
HTTP 请求；配置成功不代表已通过真实账户联调，查看 `last_request_status`。

## 调用和结果判断

```text
component_providers()
component_search(query="OPA333", providers=["szlcsc", "mouser", "digikey"], limit=5)
component_get(provider="mouser", identifier="制造商料号或贸泽 SKU", refresh=true)
component_offers(mpn="OPA333AIDBVR", quantity=10, providers=["szlcsc", "mouser", "digikey"], refresh=true)
```

- 搜索 `limit` 是每供应商的页大小（1–50），合并最多 4 倍该值。制造商、封装和电气条件
  在当前抓取窗口中再次筛选；空结果不证明后续页面也无匹配，检查每源 `has_more` 和未知总数。
- `complete=false` 必须查看 `provider_errors`。缺 Key 不等于无库存，失败不静默替换过期库存或价格。
- 精确查询发生身份歧义时，补充 `manufacturer`、`package`，或使用更明确的供应商 SKU。
- 报价工具输入制造商 MPN；供应商 SKU 先通过 `component_get` 解析。每包装变体独立记录
  库存、MOQ 和报价，整包装数量不会误当作 MOQ/倍数。
- 比价只对制造商/MPN/封装、币种及零售/装配市场相同的条目分组，不自动换汇或累加库存。
  运费、税费、关税及绕带费未统一核实时，器件行价不能视为到货总成本。
- DigiKey KeywordSearch 仅用 StandardPricing；详情存在 MyPricing 时才注明账户价。
- 新 Resource `components://supplier-guide`、Prompt `compare_supplier_offers` 提供简要指导；
  原 45 个 `szlcsc_*` 工具继续可用。最终电气、封装和替代兼容性仍需制造商 Datasheet。

## 缓存、限流和秘密处理

Mouser 本地间隔至少 2.1 秒，DigiKey 至少 0.6 秒；每账户每天默认最多 1,000 次实际发送
尝试，重试/OAuth 也计数，通过本地数据库事务协调。此上限不包括其他程序或机器用量；
账户配额更低时以上游 429/Retry-After 为准，401/403 不更换身份或绕过限制。

查询默认缓存 120 秒，库存/报价默认刷新。缓存仅保存清洗后的模型，按供应商、账户摘要、请求
及 DigiKey 地区/币种隔离。Mouser 的 Key 按官方协议发送到查询参数，不进入缓存 URL、返回值
或正常诊断日志。自动 OAuth Token 只在进程内保存，按有效期重新申请。

## 华秋适配贡献

经官方申请获批后，获取基础地址、只读搜索/详情端点、认证/签名、参数边界、返回字段、库存池、
币种、MOQ/倍数、配额及错误码。私有文档和凭据不要直接提交公开 Issue，可先请求私下沟通渠道。
沿用供应商接口和 `ComponentRecord`，通过 `build_registry` 注册，并补充无凭据、身份歧义、
分页、金额、超时、限流及脱敏 Mock。取得协议前返回 `UNSUPPORTED_PROVIDER`，不猜签名或借用浏览器 Cookie。

官方契约核对日期：2026-10-29。依据：[Mouser OpenAPI](https://api.mouser.com/api/docs/V1)、
[DigiKey V4](https://developer.digikey.com/products/product-information-v4/productsearch)、
[DigiKey OAuth 两方流程](https://developer.digikey.com/tutorials-and-resources/oauth-20-2-legged-flow)。
