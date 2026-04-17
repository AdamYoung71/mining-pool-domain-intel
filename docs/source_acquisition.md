# 公共矿池情报源与采集步骤

## 首期情报源

| 类别 | 源 | 方法 | 默认置信度 | 说明 |
| --- | --- | --- | --- | --- |
| 官方文档 | F2Pool Help Center | 抓取 HTML，抽取 `stratum+tcp://` / `stratum+ssl://` | `confirmed` | 多币种 URL 表和 BTC 指南。 |
| 官方文档 | ViaBTC Help Center | 抓取 HTML，抽取 Stratum URL | `confirmed` | 多币种 URL 表和 BTC 指南；站点偶发 403/TLS 中断，依赖重试和缓存。 |
| 官方文档 | AntPool Help Center | 抓取 HTML，抽取 Stratum URL | `confirmed` | BTC/BCH 与 ETC 指南；部分页面可能 403。 |
| 官方文档 | Luxor Docs | 抓取 HTML，抽取官方 global stratum 表 | `confirmed` | BTC、LTC/DOGE、SC、ZEC 等。 |
| 官方文档 | 2Miners pool help | 抓取 HTML，抽取 URL 和服务器表 `host:port` | `confirmed` | 端口大于等于 10000 的 2Miners 记录按 SSL 推断。 |
| 官方文档 | RavenMiner RVN howto | 抓取 HTML，抽取 URL、区域节点和 TLS 端口 | `confirmed` | 端口大于等于 10000 的 RavenMiner 记录按 SSL 推断。 |
| 聚合/帮助页 | minerstat NiceHash help | 抓取 HTML，抽取 NiceHash Stratum 示例 | `candidate` | 非官方矿池来源，不直接进入 watchlist。 |
| 认证 API | minerstat Pools API | `X-API-Key` JSON API | `candidate` | 默认关闭，设置 `MINERSTAT_API_KEY` 后可作为元数据补充。 |
| 开源代码 | GitHub Code Search | 认证 API 搜索公开配置和 README，抽取 Stratum URL / host:port | `candidate` | 需要 `GITHUB_TOKEN`，详见 `docs/github_acquisition.md`。 |

完整源清单在 `data/sources/mining_pool_sources.json`。新增源时优先选择矿池官方文档，其次选择聚合站/API，最后才使用开源配置或教程样例。

## 爬取方法

1. 读取 `data/sources/mining_pool_sources.json`，只处理 `enabled=true` 的源。
2. 使用浏览器 User-Agent 低频访问公开页面；默认超时 25 秒、源间隔 1 秒、失败重试 1 次。
3. 将 HTML 转为纯文本，去除脚本、样式和标签，归一化空白字符。
4. 用两类模式提取端点：
   - 完整 Stratum URL：`stratum+tcp://domain:port`、`stratum+ssl://domain:port`
   - 服务器表地址：`domain:port`，仅在域名后缀命中源配置的 `allowed_domain_suffixes` 时接受
5. 根据域名、端口和源配置推断 `coin`、`algorithm`、`region`、`scheme`。
6. 标准化为固定字段记录，按 `domain + port + scheme + coin` 去重。
7. 写出 `data/raw/discovered_pool_domains.json`、`data/discovered_pool_domains.csv` 和 `data/raw/fetch_report.json`。
8. 每个成功源会写入 `data/raw/source_cache/<source_id>.json`；后续源临时失败时使用最近成功缓存，并在报告里标记 `used_cache=true`。

## 操作步骤

```powershell
# 1. 获取公开情报
python3 scripts/collect_intel.py

# 2. 查看每个源的抓取状态
Get-Content data\raw\fetch_report.json

# 3. 人工复核采集结果
Get-Content data\discovered_pool_domains.csv

# 4. 把确认后的记录合并到 seed 库
#    保留 first_seen，更新 last_seen，source_url 保留原始来源。

# 5. 生成稳定情报库和告警候选
python3 scripts/build_intel.py

# 6. 跑质量检查
python3 -m unittest discover -s tests
```

## 失败处理

- `403 Forbidden`：记录在 `fetch_report.json`，下次维护周期重试；如果有缓存，继续使用缓存结果。
- TLS EOF/超时：不放宽证书校验，不改用扫描；等待下次重试或人工下载页面后用 `scripts/extract_stratum.py` 处理。
- 抽取到 `UNKNOWN`：保留在 discovered 输出中，但不得合并进 seed，必须补币种/算法推断规则或人工确认。
- 聚合站/API 记录：默认 `candidate`，不进入 `watchlist`，除非获得官方源或多源交叉确认。

## 安全边界

- 只抓公开网页/API。
- 不做端口扫描、爆破、绕过认证或规避封禁。
- 不保存钱包地址、账号名、密码或矿工身份。
- 默认只告警不阻断。
