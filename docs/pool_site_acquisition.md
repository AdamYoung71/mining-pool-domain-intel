# 矿池官网域名采集与二阶段发现

## 目标

先从公共矿池目录提取矿池官网域名，再以这些官网域名为根，浅层爬取公开页面，发现对应 Stratum 接入域名。

这种方法比手工维护重点矿池文档更广，适合扩展长尾矿池；但官网浅爬结果必须人工复核，尤其是多币种老式矿池页面经常只能发现 `host:port`，无法自动确定币种和算法。

## 第一阶段：抓矿池官网域名

入口源在 `data/sources/pool_site_sources.json`。首期默认使用两个公共目录源：

- minerstat 公共矿池目录：枚举 `/pools/<slug>` profile，再抽取 profile 的 website 链接。
- MiningPoolStats：抓取 sitemap 枚举币种页，再读取币种页引用的 `data.miningpoolstats.stream/data/<coin>.js?t=...`，从 JSON 的 `data[].url` 提取矿池官网 URL。

```powershell
python3 scripts/collect_pool_sites.py
```

输出：

- `data/raw/pool_sites.json`
- `data/pool_sites.csv`
- `data/raw/ip_pool_endpoint_candidates.json`
- `data/ip_pool_endpoint_candidates.csv`
- `data/raw/pool_site_fetch_report.json`
- `data/raw/pool_site_cache/`

minerstat 采集方法：

1. 抓取 `https://minerstat.com/pools`。
2. 提取 `/pools/<slug>` profile 链接。
3. 逐个低频访问 profile 页面。
4. 从 “website” 链接提取官网 URL。
5. 过滤社交媒体、Bitcointalk、GitHub、Discord、Telegram、X 等非官网链接。
6. 按 `website_domain` 去重。

MiningPoolStats 采集方法：

1. 抓取 `https://miningpoolstats.stream/sitemap.xml`。
2. 提取一级路径币种页，例如 `/bitcoin`、`/litecoin`。
3. 抓取币种页，解析 preload 的数据文件 URL。
4. 带 `Referer` 访问 `https://data.miningpoolstats.stream/data/<coin>.js?t=<ts>`。
5. 从 JSON 的 `data[].url` 提取矿池官网 URL。
6. 域名 URL 进入官网目录；裸 IP:port 不进入官网目录，分流保存为标准矿池端点候选。

当前实测：minerstat 全量 + MiningPoolStats 前 25 个币种页，共提取到 265 个唯一矿池官网域名，并额外保存 1 条裸 IP:port 端点候选。

MiningPoolStats 默认最多抓取 80 个币种页，避免日常运行过慢；全量可显式设置：

```powershell
python3 scripts/collect_pool_sites.py --max-miningpoolstats-coins 0 --workers 4
```

只增量跑 MiningPoolStats 并合并现有官网目录：

```powershell
python3 scripts/collect_pool_sites.py --only-source miningpoolstats_sitemap_data --max-miningpoolstats-coins 25 --merge-existing --workers 4
```

## 第二阶段：由官网发现矿池接入域名

```powershell
python3 scripts/discover_from_pool_sites.py --max-sites 60 --max-pages-per-site 4 --workers 4
```

全量运行时可去掉 `--max-sites`：

```powershell
python3 scripts/discover_from_pool_sites.py --max-pages-per-site 4 --workers 4 --delay-between-sites 0.5 --delay-between-pages 1.0
```

输出：

- `data/raw/site_discovered_pool_domains.json`
- `data/site_discovered_pool_domains.csv`
- `data/raw/site_discovery_report.json`
- `data/raw/site_discovery_cache/`

发现方法：

1. 读取 `data/raw/pool_sites.json`。
2. 抓取官网首页。
3. 只跟进同域名内包含 `help`、`mining`、`pool`、`stratum`、`server`、`start`、`docs`、`support` 等关键词的公开链接。
4. 每个官网默认最多抓 6 页，可用 `--max-pages-per-site` 控制。
5. 提取完整 Stratum URL：`stratum+tcp://domain:port`、`stratum+ssl://domain:port`。
6. 对同官网域名下的 `host:port` 形式做保守提取；页面中的裸 IP:port 也会作为 `candidate`/待复核端点保存。
7. 规范化为标准字段，按 `domain + port + scheme + coin` 去重。

## 复核策略

- 完整 Stratum URL 优先级最高。
- 同官网域名内的 `host:port` 可作为候选，但 `coin=UNKNOWN` 的记录必须人工复核。
- 官网目录来自第三方目录，因此最终进入稳定 seed 库前仍需人工确认。
- 不做端口扫描，不尝试连接矿池端口，不绕过 TLS 或登录限制。

## 与重点源采集的关系

`collect_pool_sites.py` 和 `discover_from_pool_sites.py` 用于广覆盖；`collect_intel.py` 用于重点官方文档源的高置信补充。推荐顺序：

1. 官网目录全量采集。
2. 官网浅爬分批发现。
3. 重点官方文档源补充。
4. 人工复核并合并到 seed。
5. 生成稳定 watchlist。
