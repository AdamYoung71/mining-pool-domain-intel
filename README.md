# 公共矿池域名情报库

这是一个零依赖的公共矿池域名情报库项目，用于持续收集、校验、分级和导出公共虚拟货币矿池 Stratum 接入域名。

## 运行状态

<!-- intel-status:start -->
> 本区块由采集工作流自动更新，用于快速查看最近一次公共情报采集结果。

| 指标 | 上次结果 |
| --- | --- |
| 运行状态 | `success` |
| 上次运行时间 | 2026-04-20 13:26:34 北京时间 |
| 耗时 | 9分46秒 |
| 触发方式 | `schedule` |
| 参数 | MiningPoolStats=80, 官网=60, 每站页面=4, GitHub=false |
| 官网域名 | 总数 448，新增 448 |
| Stratum 域名/IP | 总数 602，新增 602 |
| Stratum 记录 | 总数 949，新增 949 |
| 裸 IP:port 候选 | 总数 1，新增 1 |
| 区块链接入节点候选 | 总数 0，新增 0 |
| 最终情报库 | 总数 18，新增 18 |
| 告警建议集 | 总数 17 |
| 源抓取状态 | 成功 410，失败 36，使用缓存 0 |
| 运行链接 | [GitHub Actions](https://github.com/AdamYoung71/mining-pool-domain-intel/actions/runs/24649968475) |
<!-- intel-status:end -->

## 快速开始

完整运行顺序和复核规则见 `docs/runbook.md`。

```powershell
python3 scripts/collect_pool_sites.py --max-miningpoolstats-coins 0
python3 scripts/discover_from_pool_sites.py --max-pages-per-site 4 --delay-between-sites 2.0 --delay-between-pages 1.0
python3 scripts/collect_intel.py
python3 scripts/collect_github_intel.py
python3 scripts/promote_discovered.py
python3 scripts/build_intel.py
python3 -m unittest discover -s tests
```

生成结果：

- `data/raw/discovered_pool_domains.json`：从公开源实时采集到的规范化候选情报。
- `data/discovered_pool_domains.csv`：实时采集结果的 CSV 版本。
- `data/raw/pool_sites.json`：从公共目录提取的矿池官网域名。
- `data/pool_sites.csv`：矿池官网域名 CSV。
- `data/raw/ip_pool_endpoint_candidates.json`：公共目录中出现的裸 IP:port 矿池端点候选。
- `data/ip_pool_endpoint_candidates.csv`：裸 IP:port 端点候选 CSV。
- `data/raw/site_discovered_pool_domains.json`：从矿池官网浅层爬取发现的 Stratum 接入域名。
- `data/site_discovered_pool_domains.csv`：官网浅爬发现结果 CSV。
- `data/raw/blockchain_node_candidates.json`：官网浅爬中识别出的 `addnode` / `seednode` 区块链接入节点候选，不作为矿池地址。
- `data/blockchain_node_candidates.csv`：区块链接入节点候选 CSV。
- `data/raw/github_pool_endpoint_candidates.json`：从 GitHub 公开代码搜索发现的端点候选。
- `data/github_pool_endpoint_candidates.csv`：GitHub 端点候选 CSV。
- `data/raw/fetch_report.json`：每个源的抓取状态、hash、记录数和失败原因。
- `data/raw/promotion_report.json`：自动晋升合并报告，记录插入、更新、跳过和置信度分布。
- `data/mining_pool_domains.csv`：人工审计和表格处理。
- `data/mining_pool_domains.json`：后续接入 SIEM、DNS 检测或规则生成。
- `data/watchlist.json`：仅包含 `confirmed` / `probable` 且 `active` 的告警候选记录。

## 数据字段

标准表 `mining_pool_domains` 固定字段如下：

`domain`、`port`、`scheme`、`pool_name`、`coin`、`algorithm`、`region`、`source_type`、`source_url`、`confidence`、`status`、`first_seen`、`last_seen`、`notes`

置信度：

- `confirmed`：矿池官方文档确认。
- `probable`：聚合站或多个公开来源交叉确认。
- `candidate`：GitHub、教程、CT 日志、DNS 扩展等发现但未交叉验证。
- `retired`：历史存在但当前不可确认。

状态：

- `active`
- `inactive`
- `unknown`
- `retired`

## 工作流

1. 运行 `python3 scripts/collect_pool_sites.py`，先从公共矿池目录抓取矿池官网域名。
2. 运行 `python3 scripts/discover_from_pool_sites.py`，从官网域名浅层爬取帮助页、挖矿说明和服务器页面，发现 Stratum 接入域名。
3. 在 `data/sources/mining_pool_sources.json` 维护重点官方文档源，运行 `python3 scripts/collect_intel.py` 做补充采集。
4. 设置 `GITHUB_TOKEN` 后运行 `python3 scripts/collect_github_intel.py`，从公开代码样例补充候选端点。
5. 运行 `python3 scripts/promote_discovered.py`，按来源、置信度、UNKNOWN 字段和多源交叉规则合并到 `data/raw/mining_pool_domains.seed.json`。
6. 运行 `python3 scripts/build_intel.py` 生成标准 CSV、JSON 和告警候选清单。
7. 运行 `python3 -m unittest discover -s tests` 做字段、置信度、去重和误报保护检查。
8. 每周增量更新一次，每月全量复核一次。

## 情报源采集

首期默认采集源包括 minerstat 公共矿池目录、MiningPoolStats sitemap/data 文件、F2Pool、ViaBTC、AntPool、Luxor、2Miners、RavenMiner 的官方帮助页，以及 minerstat 的公开帮助页。详见：

- `data/sources/pool_site_sources.json`
- `data/sources/mining_pool_sources.json`
- `data/sources/github_search_sources.json`
- `docs/pool_site_acquisition.md`
- `docs/source_acquisition.md`
- `docs/github_acquisition.md`

采集器只访问公开网页/API，不做公网端口扫描。官方源抽取到的 Stratum 地址标记为 `confirmed`，聚合站或帮助页补充源默认标记为 `candidate`。每个源成功抓取后会写入 `data/raw/source_cache/`，临时 403 或 TLS 失败时不会立刻清空历史成功结果。

官网目录发现是第一层，重点产物是 `data/raw/pool_sites.json`。如果公共目录里出现裸 IP:port，脚本会分流到 `data/raw/ip_pool_endpoint_candidates.json`，不混入官网域名目录。官网浅层爬取是第二层，重点产物是 `data/raw/site_discovered_pool_domains.json`。官网浅爬中出现的 `addnode=IP:port` / `seednode=IP:port` 会分流到 `data/raw/blockchain_node_candidates.json`，不进入矿池端点库。全量官网浅爬可不加限制运行；日常验证建议先用 `--max-sites` 控制批次。

## 候选提取

从公开 README、教程、配置样例中提取 Stratum 候选地址时，可以先保存为本地文本文件，再运行：

```powershell
python3 scripts/extract_stratum.py path\to\sample.txt
```

该脚本只读取本地文件并输出候选 JSON 到标准输出，不会写入情报库。人工复核后再合并到 `data/raw/mining_pool_domains.seed.json`。
