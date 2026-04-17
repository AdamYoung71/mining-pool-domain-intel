from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from build_intel import build_library, parse_endpoint, write_csv
    from collect_intel import infer_coin_algorithm, infer_host_port_scheme, normalize_region
except ModuleNotFoundError:
    from scripts.build_intel import build_library, parse_endpoint, write_csv
    from scripts.collect_intel import infer_coin_algorithm, infer_host_port_scheme, normalize_region

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "data" / "sources" / "github_search_sources.json"
DEFAULT_DISCOVERED_JSON = ROOT / "data" / "raw" / "github_pool_endpoint_candidates.json"
DEFAULT_DISCOVERED_CSV = ROOT / "data" / "github_pool_endpoint_candidates.csv"
DEFAULT_REPORT = ROOT / "data" / "raw" / "github_fetch_report.json"

SEARCH_API = "https://api.github.com/search/code"
STRATUM_RE = re.compile(
    r"\b(stratum\+(?:tcp|ssl)):/+(?:[^\s/@<>\"']+@)?([a-z0-9][a-z0-9.-]*[a-z0-9]|(?:\d{1,3}\.){3}\d{1,3})\s*:\s*(\d{1,5})\b",
    flags=re.I,
)
HOST_PORT_RE = re.compile(
    r"(?<![@\w.-])([a-z0-9][a-z0-9.-]*\.[a-z0-9-]{2,63}|(?:\d{1,3}\.){3}\d{1,3})\s*:\s*(\d{2,5})\b",
    flags=re.I,
)
MINING_KEYWORDS = (
    "ccminer",
    "cpuminer",
    "ethminer",
    "lolminer",
    "miner",
    "mining",
    "mining.subscribe",
    "nbminer",
    "phoenixminer",
    "pool",
    "pools",
    "stratum",
    "trex",
    "xmrig",
    "xmr-stak",
)
BLOCKED_HOSTS = {
    "example.com",
    "example.net",
    "example.org",
    "localhost",
}


def load_config(path: Path = DEFAULT_SOURCES) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def github_headers(token: str, user_agent: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": user_agent,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def fetch_json(url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def search_code(query: str, headers: dict[str, str], timeout: int, per_page: int, max_results: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    page = 1
    while len(items) < max_results:
        params = urllib.parse.urlencode({"q": query, "per_page": min(per_page, 100), "page": page})
        url = f"{SEARCH_API}?{params}"
        try:
            payload = fetch_json(url, headers, timeout)
            page_items = payload.get("items", [])
            items.extend(page_items)
            reports.append({
                "url": url,
                "ok": True,
                "page": page,
                "records": len(page_items),
                "error": None,
            })
            if not page_items or len(page_items) < per_page:
                break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            reports.append({
                "url": url,
                "ok": False,
                "page": page,
                "records": 0,
                "error": f"HTTP {exc.code}: {body[:300]}",
            })
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            reports.append({
                "url": url,
                "ok": False,
                "page": page,
                "records": 0,
                "error": str(exc),
            })
            break
        page += 1
        time.sleep(1)

    return items[:max_results], reports


def fetch_file_text(item: dict[str, Any], headers: dict[str, str], timeout: int) -> tuple[str | None, str | None]:
    try:
        payload = fetch_json(item["url"], headers, timeout)
        content = payload.get("content", "")
        encoding = payload.get("encoding", "")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace"), None
        if isinstance(content, str):
            return content, None
        return None, "Unsupported GitHub content encoding."
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return None, f"HTTP {exc.code}: {body[:300]}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return None, str(exc)


def blocked_host(host: str) -> bool:
    lowered = host.lower()
    if lowered.startswith("127.") or lowered.startswith("10.") or lowered.startswith("192.168."):
        return True
    if lowered in BLOCKED_HOSTS:
        return True
    return False


def extract_endpoints_from_text(text: str) -> list[dict[str, Any]]:
    endpoints: dict[tuple[str, int, str], dict[str, Any]] = {}
    for match in STRATUM_RE.finditer(text):
        scheme, host, port_text = match.groups()
        parsed = parse_endpoint(f"{scheme.lower()}://{host}:{port_text}")
        if not blocked_host(parsed["domain"]):
            endpoints[(parsed["domain"], parsed["port"], parsed["scheme"])] = parsed

    for line in text.splitlines():
        lowered = line.lower()
        if "stratum+" in lowered:
            continue
        if not any(keyword in lowered for keyword in MINING_KEYWORDS):
            continue
        for match in HOST_PORT_RE.finditer(line):
            host, port_text = match.groups()
            scheme = infer_host_port_scheme(host, int(port_text), {"pool_name": "GitHub"})
            parsed = parse_endpoint(f"{host}:{port_text}", fallback_scheme=scheme)
            if not blocked_host(parsed["domain"]):
                endpoints[(parsed["domain"], parsed["port"], parsed["scheme"])] = parsed

    return sorted(endpoints.values(), key=lambda item: (item["domain"], item["port"], item["scheme"]))


def endpoint_to_record(endpoint: dict[str, Any], item: dict[str, Any], query_id: str, fetched_on: str) -> dict[str, Any]:
    repo = item.get("repository", {}).get("full_name", "UNKNOWN")
    source_url = item.get("html_url") or item.get("url")
    coin, algorithm = infer_coin_algorithm(endpoint["domain"], int(endpoint["port"]), {"pool_name": "GitHub"})
    return {
        "domain": endpoint["domain"],
        "port": int(endpoint["port"]),
        "scheme": endpoint["scheme"],
        "pool_name": "UNKNOWN",
        "coin": coin,
        "algorithm": algorithm,
        "region": normalize_region(endpoint["domain"]),
        "source_type": "open_source",
        "source_url": source_url,
        "confidence": "candidate",
        "status": "unknown",
        "first_seen": fetched_on,
        "last_seen": fetched_on,
        "notes": f"Extracted from public GitHub code search query {query_id}; repo={repo}; path={item.get('path', 'UNKNOWN')}; review before promotion.",
    }


def unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    for record in records:
        key = (record["domain"], int(record["port"]), record["scheme"], record["coin"].upper())
        current = unique.get(key)
        if current is None:
            unique[key] = record
        elif record["source_url"] not in current["source_url"]:
            current["source_url"] = f"{current['source_url']}; {record['source_url']}"
    return list(unique.values())


def collect_github(config: dict[str, Any], token: str, only_query: set[str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    policy = config.get("fetch_policy", {})
    headers = github_headers(token, policy.get("user_agent", "mining-pool-domain-intel/0.1"))
    timeout = int(policy.get("timeout_seconds", 25))
    per_page = int(policy.get("per_page", 30))
    default_max_results = int(policy.get("max_results_per_query", 50))
    fetched_on = date.today().isoformat()
    records: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []

    for query in config.get("queries", []):
        if not query.get("enabled", True):
            continue
        if only_query and query["id"] not in only_query:
            continue

        started_at = datetime.now(timezone.utc).isoformat()
        items, search_reports = search_code(
            query["query"],
            headers,
            timeout,
            per_page,
            int(query.get("max_results", default_max_results)),
        )
        reports.append({
            "query_id": query["id"],
            "query": query["query"],
            "started_at": started_at,
            "ok": any(report["ok"] for report in search_reports),
            "search_pages": search_reports,
            "search_items": len(items),
            "files_scanned": 0,
            "records": 0,
            "error": None if search_reports and search_reports[0]["ok"] else (search_reports[0]["error"] if search_reports else "No search response."),
        })

        for item in items:
            text, error = fetch_file_text(item, headers, timeout)
            if error or text is None:
                reports.append({
                    "query_id": query["id"],
                    "file_url": item.get("html_url") or item.get("url"),
                    "ok": False,
                    "records": 0,
                    "error": error,
                })
                continue

            endpoints = extract_endpoints_from_text(text)
            file_records = [endpoint_to_record(endpoint, item, query["id"], fetched_on) for endpoint in endpoints]
            records.extend(file_records)
            reports.append({
                "query_id": query["id"],
                "file_url": item.get("html_url") or item.get("url"),
                "ok": True,
                "records": len(file_records),
                "error": None,
            })
            time.sleep(float(policy.get("delay_seconds", 1)))

    return build_library(unique_records(records)) if records else [], reports


def write_outputs(records: list[dict[str, Any]], reports: list[dict[str, Any]]) -> None:
    DEFAULT_DISCOVERED_JSON.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_DISCOVERED_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(records, DEFAULT_DISCOVERED_CSV)
    DEFAULT_REPORT.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect public mining pool endpoint candidates from GitHub Code Search.")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--only-query", action="append", default=[], help="Run only the selected query id; may be repeated.")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.sources)
    token_env = config.get("auth", {}).get("env", "GITHUB_TOKEN")
    token = os.environ.get(token_env, "")
    if not token:
        records: list[dict[str, Any]] = []
        reports = [{
            "ok": False,
            "records": 0,
            "error": f"{token_env} is not set. GitHub Code Search API requires an authenticated token.",
        }]
    else:
        records, reports = collect_github(config, token, set(args.only_query) if args.only_query else None)

    if args.stdout:
        print(json.dumps({"records": records, "report": reports}, ensure_ascii=False, indent=2))
    else:
        write_outputs(records, reports)
        ok_files = sum(1 for report in reports if report.get("ok") and "file_url" in report)
        print(f"Scanned {ok_files} GitHub files.")
        print(f"Discovered {len(records)} normalized GitHub endpoint candidates.")
        print(f"Wrote {DEFAULT_DISCOVERED_JSON.relative_to(ROOT)} and {DEFAULT_DISCOVERED_CSV.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
