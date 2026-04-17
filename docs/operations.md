# 矿池域名情报库运营流程

## 来源优先级

1. 官方矿池文档：最高优先级，记录为 `source_type=official` 和 `confidence=confirmed`。
2. 聚合站：用于补充币种、矿池活跃度和候选节点；单一聚合站来源不得标记为 `confirmed`。
3. 开源配置和教程：只作为 `candidate`，必须人工复核后才能升级。
4. CT 日志、DNS 解析、子域模式：只作为扩展候选，不直接进入告警建议集。

## 记录维护规则

- 同一 `domain + port + scheme + coin` 只保留一条。
- 同一域名的不同端口、不同协议或不同币种保留为独立记录。
- `source_url` 必须能解释该记录来源；多来源用分号加空格分隔。
- `first_seen` 保持首次收录日期，`last_seen` 更新为最近确认日期。
- 连续两个维护周期无法确认的记录降级为 `retired`，`status` 同步改为 `retired`。

## 分级规则

- `confirmed`：官方文档明确列出域名、端口和连接协议。
- `probable`：至少两个独立公开来源交叉确认，或聚合站来源经过人工复核。
- `candidate`：单一公开来源、代码搜索、教程、CT 日志或 DNS 扩展结果。
- `retired`：历史记录保留，用于回溯和避免重复误加。

## 更新节奏

- 每周增量：新增公开来源、更新 `last_seen`、导出 watchlist。
- 每月全量：复核主流矿池官方文档，审查 `candidate` 和 `retired` 记录。
- 每次更新后检查新增、降级、升级和删除数量；大批量变化必须人工复核。

## 告警落地建议

- 首期只使用 `data/watchlist.json` 做告警候选输入。
- 告警逻辑建议结合 DNS 查询、代理日志、TLS SNI、目的端口和连接时长。
- 不建议仅凭域名或端口阻断；公共云、CDN、教程站点和官网域名容易造成误报。
- 发现内部真实命中时，把命中证据作为新的来源补充到原始数据，但不要记录钱包地址或用户名。

## 参考公开来源

- MiningPoolStats: https://miningpoolstats.app/
- minerstat Pools API: https://api.minerstat.com/docs-pools
- XMRig pool config: https://xmrig.com/docs/miner/config/pool
- Stratum V2 mining protocol: https://stratumprotocol.org/specification/05-mining-protocol/
- f2pool mining pool info: https://f2pool.zendesk.com/hc/en-us/articles/360058887912-Mining-pools-info-payout-schemes-and-thresholds-at-f2pool
