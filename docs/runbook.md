# 公共矿池情报工具运行说明

## 1. 准备环境

本工具只依赖 Python 标准库，要求 Python 3.10 或更高版本。

```powershell
python --version
python -m unittest discover -s tests
```

如果当前 PowerShell 能运行 `python`，后续命令都使用 `python` 即可；如果你的环境只支持 `python3`，把命令中的 `python` 替换为 `python3`。

GitHub 公开代码采集需要设置 `GITHUB_TOKEN`。不设置也可以运行主流程，只是 GitHub 采集会输出空结果和说明报告。

```powershell
$env:GITHUB_TOKEN = "github_pat_or_classic_token"
```

## 2. 推荐完整流程

按下面顺序运行，能覆盖“公共目录官网域名 -> 官网浅爬接入端点 -> 重点官方文档源 -> GitHub 公开代码候选 -> 稳定库”的完整链路。

```powershell
# 1. 从公共目录抓矿池官网域名；0 表示全量 MiningPoolStats sitemap
python scripts/collect_pool_sites.py --max-miningpoolstats-coins 0

# 2. 从官网浅层发现矿池接入端点；默认建议加请求间隔，降低被限速概率
python scripts/discover_from_pool_sites.py --max-pages-per-site 4 --delay-between-sites 2.0 --delay-between-pages 1.0

# 3. 从重点官方文档和聚合帮助页补充端点
python scripts/collect_intel.py

# 4. 从 GitHub 公开代码和配置样例补充候选端点
python scripts/collect_github_intel.py

# 5. 按自动晋升规则把 discovered 结果合并进 seed
python scripts/promote_discovered.py

# 6. 从 seed 生成稳定情报库和 watchlist
python scripts/build_intel.py

# 7. 运行质量检查
python -m unittest discover -s tests
```

## 3. 日常增量流程

日常建议先跑受控批次，复核结果后再决定是否全量。

```powershell
# 官网目录：只增量跑 MiningPoolStats 前 25 个币种页，并合并已有官网目录
python scripts/collect_pool_sites.py --only-source miningpoolstats_sitemap_data --max-miningpoolstats-coins 25 --merge-existing

# 官网浅爬：先跑前 60 个官网，每站最多 4 页
python scripts/discover_from_pool_sites.py --max-sites 60 --max-pages-per-site 4

# GitHub：只跑一个查询做验证
python scripts/collect_github_intel.py --only-query stratum_tcp_json
```

全量 MiningPoolStats 官网目录采集：

```powershell
python scripts/collect_pool_sites.py --max-miningpoolstats-coins 0
```

全量官网浅爬：

```powershell
python scripts/discover_from_pool_sites.py --max-pages-per-site 4 --delay-between-sites 2.0 --delay-between-pages 1.0
```

GitHub Actions 定时任务默认使用全量参数：`miningpoolstats_coins=0`、`site_limit=0`、`pages_per_site=4`，并在官网浅爬时使用 `site_delay_seconds=2.0`、`page_delay_seconds=1.0` 做节流。手动验证脚本是否正常时，可以继续使用前 25 个币种页或前 60 个官网的受控批次。

## 4. 输出文件

官网目录阶段：

- `data/raw/pool_sites.json`
- `data/pool_sites.csv`
- `data/raw/ip_pool_endpoint_candidates.json`
- `data/ip_pool_endpoint_candidates.csv`
- `data/raw/pool_site_fetch_report.json`

官网浅爬阶段：

- `data/raw/site_discovered_pool_domains.json`
- `data/site_discovered_pool_domains.csv`
- `data/raw/blockchain_node_candidates.json`
- `data/blockchain_node_candidates.csv`
- `data/raw/site_discovery_report.json`

重点源采集阶段：

- `data/raw/discovered_pool_domains.json`
- `data/discovered_pool_domains.csv`
- `data/raw/fetch_report.json`

GitHub 采集阶段：

- `data/raw/github_pool_endpoint_candidates.json`
- `data/github_pool_endpoint_candidates.csv`
- `data/raw/github_fetch_report.json`

自动晋升阶段：

- `data/raw/promotion_report.json`
- `data/raw/mining_pool_domains.seed.json`

稳定库阶段：

- `data/raw/mining_pool_domains.seed.json`
- `data/mining_pool_domains.json`
- `data/mining_pool_domains.csv`
- `data/watchlist.json`

## 5. 复核和入库

采集脚本产生的是 discovered/candidate 结果，默认通过 `scripts/promote_discovered.py` 按自动晋升规则合并到：

```text
data/raw/mining_pool_domains.seed.json
```

自动晋升规则：

- 重点官方文档源和官网浅爬结果可按来源配置保留 `confirmed` / `probable`。
- 两个以上独立来源命中同一 `domain + port + scheme + coin` 的候选记录可升为 `probable`。
- GitHub、MiningPoolStats、教程、配置样例、裸 IP:port 默认保留为 `candidate`。
- `coin=UNKNOWN`、`algorithm=UNKNOWN` 或裸 IP 字面量记录保持 `candidate` / `unknown`，不进入 watchlist。
- 官网浅爬中识别到的 `addnode=IP:port` / `seednode=IP:port` 属于区块链接入节点候选，会写入 `data/raw/blockchain_node_candidates.json`，不合并进矿池端点库。
- 同一 `domain + port + scheme + coin` 会合并 `source_url`，并保留更高置信度。

自动晋升不是人工事实确认。高风险变化仍应查看 `data/raw/promotion_report.json` 和对应 `source_url`。

复核后重新生成稳定库：

```powershell
python scripts/build_intel.py
```

## 6. 安全边界

- 只抓公开网页、公开 API 和 GitHub 公开代码搜索结果。
- 不做公网端口扫描。
- 不绕过登录、Cloudflare、TLS 校验或访问控制。
- 不保存钱包地址、用户名、密码或完整矿工配置。
- 默认只告警不阻断；只有 `confirmed` 和 `probable` 的 active 记录进入 `watchlist`。

## 7. 常见问题

`GITHUB_TOKEN is not set`：

GitHub 采集需要 token。设置 `$env:GITHUB_TOKEN` 后重跑；如果不需要 GitHub 数据，可以忽略这个报告。

部分源返回 `403 Forbidden` 或 TLS EOF：

这是公开站点的反爬、限流或 TLS 行为。工具会记录到对应 report 文件；不要绕过访问控制，下一轮维护周期重试即可。

发现大量 `UNKNOWN`：

说明只抽到了端点，但无法可靠推断币种或算法。先保留为候选，必要时补充推断规则或人工确认。

想快速确认工具是否正常：

```powershell
python -m unittest discover -s tests
python scripts/build_intel.py
```

## 8. GitHub Actions

仓库包含两个工作流：

- `.github/workflows/ci.yml`：在 push、pull request、手动触发时运行单元测试，并生成稳定情报库输出 artifact。
- `.github/workflows/collect.yml`：每周一 UTC 02:30 定时运行，也支持手动触发；会抓官网目录、官网浅爬、重点文档源，并把结果作为 artifact 上传。

手动运行采集工作流时可以配置：

- `miningpoolstats_coins`：MiningPoolStats 抓取币种页数量，`0` 表示全量 sitemap。
- `site_limit`：官网浅爬站点数量，`0` 表示全量。
- `pages_per_site`：每个官网最多浅爬页面数。
- `site_delay_seconds`：官网浅爬时两个网站之间的等待秒数。
- `page_delay_seconds`：同一官网内两个页面之间的等待秒数。
- `run_github`：是否运行 GitHub Code Search 候选采集。

GitHub Actions 里运行 GitHub Code Search 时使用仓库自带的 `${{ secrets.GITHUB_TOKEN }}`。如果 API 权限不足，可以额外配置一个只读 token，并把工作流里的 `GITHUB_TOKEN` 环境变量改为该 secret。
