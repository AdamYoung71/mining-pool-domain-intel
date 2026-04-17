# GitHub 公开代码情报采集

## 目标

从 GitHub 公开代码和配置样例中发现矿池接入端点候选。GitHub 结果只作为 `candidate`，不得直接进入阻断或高置信 watchlist。

## 准备

GitHub Code Search API 需要认证 token。设置环境变量：

```powershell
$env:GITHUB_TOKEN = "github_pat_or_classic_token"
```

建议使用只读、低权限 token。脚本只调用公开 code search 和 contents API，不写 GitHub，不克隆仓库。

## 运行

```powershell
python3 scripts/collect_github_intel.py
```

只跑某个查询：

```powershell
python3 scripts/collect_github_intel.py --only-query stratum_tcp_json
```

输出：

- `data/raw/github_pool_endpoint_candidates.json`
- `data/github_pool_endpoint_candidates.csv`
- `data/raw/github_fetch_report.json`

## 采集方法

1. 读取 `data/sources/github_search_sources.json`。
2. 使用 GitHub Code Search API 执行预设查询。
3. 通过 contents API 读取命中文件内容。
4. 只抽取 `stratum+tcp://host:port`、`stratum+ssl://host:port`，以及矿工配置上下文中的 `host:port`。
5. 不保存钱包、用户名、密码或完整配置内容。
6. 标准化为固定字段，标记 `source_type=open_source`、`confidence=candidate`、`status=unknown`。

## 复核规则

- 优先复核完整 Stratum URL。
- `host:port` 只有在命中行包含 `pool`、`pools`、`stratum`、`xmrig`、`mining` 等关键词时才抽取。
- GitHub 公开样例可能过期、错误或包含测试地址；必须交叉验证后才能合并到 seed。
- 私有网段、localhost、example 域名会被过滤。
