from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "data" / "status" / "baseline.json"
DEFAULT_STATUS_JSON = ROOT / "data" / "status" / "latest_run.json"
DEFAULT_README = ROOT / "README.md"

START_MARKER = "<!-- intel-status:start -->"
END_MARKER = "<!-- intel-status:end -->"

POOL_SITE_FILES = [
    "data/raw/pool_sites.json",
]
ENDPOINT_FILES = [
    "data/raw/discovered_pool_domains.json",
    "data/raw/site_discovered_pool_domains.json",
    "data/raw/github_pool_endpoint_candidates.json",
    "data/raw/ip_pool_endpoint_candidates.json",
]
IP_ENDPOINT_FILES = [
    "data/raw/ip_pool_endpoint_candidates.json",
]
BLOCKCHAIN_NODE_FILES = [
    "data/raw/blockchain_node_candidates.json",
]
WATCHLIST_FILES = [
    "data/watchlist.json",
]
STABLE_LIBRARY_FILES = [
    "data/mining_pool_domains.json",
]
REPORT_FILES = [
    "data/raw/pool_site_fetch_report.json",
    "data/raw/site_discovery_report.json",
    "data/raw/fetch_report.json",
    "data/raw/github_fetch_report.json",
]


def load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return [item for item in payload["records"] if isinstance(item, dict)]
    return []


def data_file_path(root: Path, file_name: str) -> Path:
    path = root / file_name
    if path.exists():
        return path
    if file_name.startswith("data/"):
        artifact_path = root / file_name.removeprefix("data/")
        if artifact_path.exists():
            return artifact_path
    return path


def normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def endpoint_record_key(record: dict[str, Any]) -> str | None:
    domain = normalize_text(record.get("domain"))
    port = str(record.get("port") or "").strip()
    scheme = normalize_text(record.get("scheme"))
    coin = normalize_text(record.get("coin") or "UNKNOWN")
    if not domain or not port or not scheme:
        return None
    return "|".join([domain, port, scheme, coin])


def endpoint_domain_key(record: dict[str, Any]) -> str | None:
    domain = normalize_text(record.get("domain"))
    return domain or None


def pool_site_key(record: dict[str, Any]) -> str | None:
    domain = normalize_text(record.get("website_domain"))
    return domain or None


def blockchain_node_key(record: dict[str, Any]) -> str | None:
    node_host = normalize_text(record.get("node_host"))
    port = str(record.get("port") or "").strip()
    node_type = normalize_text(record.get("node_type"))
    if not node_host or not port:
        return None
    return "|".join([node_host, port, node_type or "node"])


def keys_from_files(root: Path, file_names: list[str], key_func) -> set[str]:
    keys: set[str] = set()
    for file_name in file_names:
        for record in load_json_list(data_file_path(root, file_name)):
            key = key_func(record)
            if key:
                keys.add(key)
    return keys


def report_summary(root: Path) -> dict[str, int]:
    reports: list[dict[str, Any]] = []
    for file_name in REPORT_FILES:
        reports.extend(load_json_list(data_file_path(root, file_name)))
    return {
        "total": len(reports),
        "ok": sum(1 for report in reports if report.get("ok") is True),
        "failed": sum(1 for report in reports if report.get("ok") is False),
        "used_cache": sum(1 for report in reports if report.get("used_cache") is True),
    }


def collect_snapshot(root: Path = ROOT) -> dict[str, Any]:
    endpoint_records = keys_from_files(root, ENDPOINT_FILES, endpoint_record_key)
    endpoint_domains = keys_from_files(root, ENDPOINT_FILES, endpoint_domain_key)
    ip_endpoint_records = keys_from_files(root, IP_ENDPOINT_FILES, endpoint_record_key)
    pool_site_domains = keys_from_files(root, POOL_SITE_FILES, pool_site_key)
    watchlist_records = keys_from_files(root, WATCHLIST_FILES, endpoint_record_key)
    stable_records = keys_from_files(root, STABLE_LIBRARY_FILES, endpoint_record_key)
    blockchain_nodes = keys_from_files(root, BLOCKCHAIN_NODE_FILES, blockchain_node_key)
    return {
        "pool_site_domains": sorted(pool_site_domains),
        "endpoint_domains": sorted(endpoint_domains),
        "endpoint_records": sorted(endpoint_records),
        "ip_endpoint_records": sorted(ip_endpoint_records),
        "watchlist_records": sorted(watchlist_records),
        "stable_records": sorted(stable_records),
        "blockchain_nodes": sorted(blockchain_nodes),
    }


def load_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_previous_run_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    snapshot = payload.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def count_delta(current: dict[str, Any], baseline: dict[str, Any], key: str) -> dict[str, int]:
    current_set = set(current.get(key, []))
    baseline_set = set(baseline.get(key, []))
    return {
        "total": len(current_set),
        "new": len(current_set - baseline_set),
    }


def parse_timestamp(value: str) -> datetime:
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_cst(value: str) -> str:
    cst = timezone(timedelta(hours=8))
    return parse_timestamp(value).astimezone(cst).strftime("%Y-%m-%d %H:%M:%S 北京时间")


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{secs:02d}秒"
    if minutes:
        return f"{minutes}分{secs:02d}秒"
    return f"{secs}秒"


def build_status_payload(
    root: Path,
    baseline: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    current = collect_snapshot(root)
    started_at = parse_timestamp(args.started_at)
    ended_at = parse_timestamp(args.ended_at)
    duration_seconds = max(0, int((ended_at - started_at).total_seconds()))
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": {
            "conclusion": args.conclusion,
            "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at": ended_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_seconds": duration_seconds,
            "duration": format_duration(duration_seconds),
            "event": args.event,
            "branch": args.branch,
            "commit": args.commit,
            "run_id": args.run_id,
            "run_url": args.run_url,
        },
        "parameters": {
            "miningpoolstats_coins": args.miningpoolstats_coins,
            "site_limit": args.site_limit,
            "pages_per_site": args.pages_per_site,
            "run_github": args.run_github,
        },
        "metrics": {
            "pool_site_domains": count_delta(current, baseline, "pool_site_domains"),
            "endpoint_domains": count_delta(current, baseline, "endpoint_domains"),
            "endpoint_records": count_delta(current, baseline, "endpoint_records"),
            "ip_endpoint_records": count_delta(current, baseline, "ip_endpoint_records"),
            "blockchain_nodes": count_delta(current, baseline, "blockchain_nodes"),
            "stable_records": count_delta(current, baseline, "stable_records"),
            "watchlist_records": count_delta(current, baseline, "watchlist_records"),
            "source_reports": report_summary(root),
        },
        "snapshot": current,
    }


def status_block(status: dict[str, Any]) -> str:
    run = status["run"]
    params = status["parameters"]
    metrics = status["metrics"]
    source_reports = metrics["source_reports"]
    run_url = str(run.get("run_url") or "")
    run_label = run_url or "GitHub Actions"
    if run_url.startswith(("https://", "http://")):
        run_label = f"[GitHub Actions]({run_url})"
    param_text = (
        f"MiningPoolStats={params['miningpoolstats_coins']}, "
        f"官网={params['site_limit']}, "
        f"每站页面={params['pages_per_site']}, "
        f"GitHub={params['run_github']}"
    )
    lines = [
        START_MARKER,
        "> 本区块由采集工作流自动更新，用于快速查看最近一次公共情报采集结果。",
        "",
        "| 指标 | 上次结果 |",
        "| --- | --- |",
        f"| 运行状态 | `{run['conclusion']}` |",
        f"| 上次运行时间 | {format_cst(run['started_at'])} |",
        f"| 耗时 | {run['duration']} |",
        f"| 触发方式 | `{run['event']}` |",
        f"| 参数 | {param_text} |",
        f"| 官网域名 | 总数 {metrics['pool_site_domains']['total']}，新增 {metrics['pool_site_domains']['new']} |",
        f"| Stratum 域名/IP | 总数 {metrics['endpoint_domains']['total']}，新增 {metrics['endpoint_domains']['new']} |",
        f"| Stratum 记录 | 总数 {metrics['endpoint_records']['total']}，新增 {metrics['endpoint_records']['new']} |",
        f"| 裸 IP:port 候选 | 总数 {metrics['ip_endpoint_records']['total']}，新增 {metrics['ip_endpoint_records']['new']} |",
        f"| 区块链接入节点候选 | 总数 {metrics['blockchain_nodes']['total']}，新增 {metrics['blockchain_nodes']['new']} |",
        f"| 最终情报库 | 总数 {metrics['stable_records']['total']}，新增 {metrics['stable_records']['new']} |",
        f"| 告警建议集 | 总数 {metrics['watchlist_records']['total']} |",
        f"| 源抓取状态 | 成功 {source_reports['ok']}，失败 {source_reports['failed']}，使用缓存 {source_reports['used_cache']} |",
        f"| 运行链接 | {run_label} |",
        END_MARKER,
    ]
    return "\n".join(lines)


def update_readme(readme_path: Path, status: dict[str, Any]) -> None:
    readme = readme_path.read_text(encoding="utf-8")
    block = status_block(status)
    if START_MARKER in readme and END_MARKER in readme:
        before, rest = readme.split(START_MARKER, 1)
        _, after = rest.split(END_MARKER, 1)
        updated = before.rstrip() + "\n\n" + block + "\n\n" + after.lstrip("\n")
    else:
        heading = "## 快速开始"
        if heading in readme:
            before, after = readme.split(heading, 1)
            updated = before.rstrip() + "\n\n## 运行状态\n\n" + block + "\n\n" + heading + after
        else:
            updated = readme.rstrip() + "\n\n## 运行状态\n\n" + block + "\n"
    readme_path.write_text(updated, encoding="utf-8", newline="\n")


def snapshot_command(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    snapshot = load_previous_run_snapshot(args.previous_status)
    if not snapshot:
        snapshot = collect_snapshot(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote status baseline snapshot to {args.output}.")
    return 0


def update_command(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    baseline = load_snapshot(args.baseline)
    status = build_status_payload(root, baseline, args)
    args.status_json.parent.mkdir(parents=True, exist_ok=True)
    args.status_json.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_readme(args.readme, status)
    print(f"Updated {args.readme} and {args.status_json}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update README collection status metrics.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="Write a pre-run metric snapshot.")
    snapshot.add_argument("--root", type=Path, default=ROOT)
    snapshot.add_argument("--output", type=Path, default=DEFAULT_BASELINE)
    snapshot.add_argument("--previous-status", type=Path, default=DEFAULT_STATUS_JSON)
    snapshot.set_defaults(func=snapshot_command)

    update = subparsers.add_parser("update", help="Update README and latest status JSON.")
    update.add_argument("--root", type=Path, default=ROOT)
    update.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    update.add_argument("--status-json", type=Path, default=DEFAULT_STATUS_JSON)
    update.add_argument("--readme", type=Path, default=DEFAULT_README)
    update.add_argument("--started-at", required=True)
    update.add_argument("--ended-at", required=True)
    update.add_argument("--conclusion", required=True)
    update.add_argument("--event", default="manual")
    update.add_argument("--branch", default="")
    update.add_argument("--commit", default="")
    update.add_argument("--run-id", default="")
    update.add_argument("--run-url", default="")
    update.add_argument("--miningpoolstats-coins", default="")
    update.add_argument("--site-limit", default="")
    update.add_argument("--pages-per-site", default="")
    update.add_argument("--run-github", default="")
    update.set_defaults(func=update_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
