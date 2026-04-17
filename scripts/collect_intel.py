from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from build_intel import build_library, parse_endpoint, write_csv
except ModuleNotFoundError:
    from scripts.build_intel import build_library, parse_endpoint, write_csv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "data" / "sources" / "mining_pool_sources.json"
DEFAULT_DISCOVERED_JSON = ROOT / "data" / "raw" / "discovered_pool_domains.json"
DEFAULT_DISCOVERED_CSV = ROOT / "data" / "discovered_pool_domains.csv"
DEFAULT_REPORT = ROOT / "data" / "raw" / "fetch_report.json"
DEFAULT_CACHE_DIR = ROOT / "data" / "raw" / "source_cache"

STRATUM_RE = re.compile(
    r"\b(stratum\+(?:tcp|ssl)):/+(?:[^\s/@<>]+@)?([a-z0-9][a-z0-9.-]*[a-z0-9])\s*:\s*(\d{1,5})\b",
    flags=re.I,
)
HOST_PORT_RE = re.compile(
    r"(?<![@\w.-])([a-z0-9][a-z0-9.-]*\.[a-z0-9-]{2,63})\s*:\s*(\d{2,5})\b",
    flags=re.I,
)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", flags=re.I | re.S)

COIN_RULES = [
    ("btc", "BTC", "SHA-256"),
    ("bitcoin", "BTC", "SHA-256"),
    ("bch", "BCH", "SHA-256"),
    ("b4c", "BCH", "SHA-256"),
    ("ltc", "LTC", "Scrypt"),
    ("doge", "DOGE", "Scrypt"),
    ("etc", "ETC", "Etchash"),
    ("ethw", "ETHW", "Ethash"),
    ("erg", "ERG", "Autolykos v2"),
    ("rvn", "RVN", "KawPoW"),
    ("raven", "RVN", "KawPoW"),
    ("kas", "KAS", "kHeavyHash"),
    ("zec", "ZEC", "Equihash"),
    ("sc", "SC", "Blake2b"),
    ("ckb", "CKB", "Eaglesong"),
    ("cfx", "CFX", "Octopus"),
    ("nexa", "NEXA", "NexaPoW"),
    ("aleo", "ALEO", "Aleo"),
    ("alph", "ALPH", "Blake3"),
    ("dash", "DASH", "X11"),
    ("xec", "XEC", "SHA-256"),
    ("hns", "HNS", "Blake2B+SHA3"),
]

VIABTC_PORT_RULES = {
    3014: ("XEC", "SHA-256"),
    314: ("XEC", "SHA-256"),
    3010: ("ETC", "Etchash"),
    310: ("ETC", "Etchash"),
    3002: ("ZEC", "Equihash"),
    302: ("ZEC", "Equihash"),
    3004: ("DASH", "X11"),
    304: ("DASH", "X11"),
    3008: ("HNS", "Blake2B+SHA3"),
    308: ("HNS", "Blake2B+SHA3"),
    3001: ("CKB", "Eaglesong"),
    301: ("CKB", "Eaglesong"),
    315: ("KAS", "kHeavyHash"),
    3015: ("KAS", "kHeavyHash"),
}


def load_source_config(path: Path = DEFAULT_SOURCES) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def html_to_text(content: bytes, content_type: str = "") -> str:
    charset = "utf-8"
    charset_match = re.search(r"charset=([\w.-]+)", content_type, flags=re.I)
    if charset_match:
        charset = charset_match.group(1)
    text = content.decode(charset, errors="replace")
    text = SCRIPT_STYLE_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text)


def fetch_url(url: str, user_agent: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        return {
            "url": response.geturl(),
            "status": getattr(response, "status", 200),
            "content_type": response.headers.get("Content-Type", ""),
            "body": body,
        }


def cache_path_for(source: dict[str, Any]) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", source["id"])
    return DEFAULT_CACHE_DIR / f"{safe_id}.json"


def load_cached_records(source: dict[str, Any]) -> list[dict[str, Any]]:
    cache_path = cache_path_for(source)
    if not cache_path.exists():
        return []
    return json.loads(cache_path.read_text(encoding="utf-8"))


def save_cached_records(source: dict[str, Any], records: list[dict[str, Any]]) -> None:
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path_for(source).write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def suffix_allowed(domain: str, suffixes: list[str]) -> bool:
    domain = domain.lower().strip(".")
    return any(domain == suffix or domain.endswith(f".{suffix}") for suffix in suffixes)


def normalize_region(domain: str) -> str:
    labels = domain.lower().split(".")
    text = "-".join(labels)
    if any(token in text for token in ("asia", "-ap-", "apac")):
        return "Asia"
    if any(token in text for token in ("euro", "eu-", "-eu", "europe")):
        return "Europe"
    if any(token in text for token in ("usa", "us-", "-us")):
        return "USA"
    if any(token in text for token in ("na", "north-america")):
        return "North America"
    if "africa" in text:
        return "Africa"
    if "latin" in text:
        return "Latin America"
    return "Global"


def infer_coin_algorithm(domain: str, port: int, source: dict[str, Any]) -> tuple[str, str]:
    lowered = domain.lower()
    coin_text = lowered.replace("ssl", "")

    if source.get("pool_name") == "ViaBTC" and port in VIABTC_PORT_RULES:
        return VIABTC_PORT_RULES[port]

    for token, coin, algorithm in COIN_RULES:
        if re.search(rf"(^|[-.]){re.escape(token)}($|[-.])", coin_text):
            return coin, algorithm

    return source.get("default_coin", "UNKNOWN"), source.get("default_algorithm", "UNKNOWN")


def infer_host_port_scheme(domain: str, port: int, source: dict[str, Any]) -> str:
    lowered = domain.lower()
    pool_name = source.get("pool_name", "")
    if "ssl" in lowered:
        return "stratum+ssl"
    if pool_name in {"2Miners", "RavenMiner"} and port >= 10000:
        return "stratum+ssl"
    return "stratum+tcp"


def endpoint_to_record(endpoint: dict[str, Any], source: dict[str, Any], fetched_on: str) -> dict[str, Any]:
    coin, algorithm = infer_coin_algorithm(endpoint["domain"], endpoint["port"], source)
    return {
        "domain": endpoint["domain"],
        "port": endpoint["port"],
        "scheme": endpoint["scheme"],
        "pool_name": source.get("pool_name", source["name"]),
        "coin": coin,
        "algorithm": algorithm,
        "region": source.get("default_region") or normalize_region(endpoint["domain"]),
        "source_type": source["source_type"],
        "source_url": source["url"],
        "confidence": source["confidence"],
        "status": "active" if source["confidence"] in {"confirmed", "probable"} else "unknown",
        "first_seen": fetched_on,
        "last_seen": fetched_on,
        "notes": f"Discovered by {source['id']} using {source['method']}. {source.get('notes', '')}".strip(),
    }


def extract_endpoints(text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    suffixes = source.get("allowed_domain_suffixes", [])
    endpoints: dict[tuple[str, int, str], dict[str, Any]] = {}

    for match in STRATUM_RE.finditer(text):
        scheme, domain, port = match.groups()
        parsed = parse_endpoint(f"{scheme.lower()}://{domain}:{port}")
        if suffixes and not suffix_allowed(parsed["domain"], suffixes):
            continue
        key = (parsed["domain"], parsed["port"], parsed["scheme"])
        endpoints[key] = parsed

    for match in HOST_PORT_RE.finditer(text):
        domain, port = match.groups()
        scheme = infer_host_port_scheme(domain, int(port), source)
        parsed = parse_endpoint(f"{domain}:{port}", fallback_scheme=scheme)
        if suffixes and not suffix_allowed(parsed["domain"], suffixes):
            continue
        key = (parsed["domain"], parsed["port"], parsed["scheme"])
        endpoints.setdefault(key, parsed)

    return sorted(endpoints.values(), key=lambda item: (item["domain"], item["port"], item["scheme"]))


def collect_source(source: dict[str, Any], policy: dict[str, Any], fetched_on: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = datetime.now(timezone.utc).isoformat()
    report = {
        "source_id": source["id"],
        "url": source["url"],
        "started_at": started,
        "ok": False,
        "status": None,
        "sha256": None,
        "bytes": 0,
        "records": 0,
        "used_cache": False,
        "error": None,
    }

    attempts = int(policy.get("max_retries", 0)) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = fetch_url(source["url"], policy["user_agent"], int(policy["timeout_seconds"]))
            text = html_to_text(response["body"], response["content_type"])
            endpoints = extract_endpoints(text, source)
            records = [endpoint_to_record(endpoint, source, fetched_on) for endpoint in endpoints]
            report.update(
                {
                    "ok": True,
                    "status": response["status"],
                    "sha256": hashlib.sha256(response["body"]).hexdigest(),
                    "bytes": len(response["body"]),
                    "records": len(records),
                }
            )
            save_cached_records(source, records)
            return records, report
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(float(policy.get("delay_seconds", 1)))

    report["error"] = str(last_error)
    cached = load_cached_records(source)
    if cached:
        report["used_cache"] = True
        report["records"] = len(cached)
        return cached, report
    return [], report


def collect_minerstat_api(source: dict[str, Any], fetched_on: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    api_key_name = source.get("auth", "MINERSTAT_API_KEY")
    api_key = os.environ.get(api_key_name, "")
    report = {
        "source_id": source["id"],
        "url": source["url"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ok": False,
        "status": None,
        "sha256": None,
        "bytes": 0,
        "records": 0,
        "used_cache": False,
        "error": None,
    }
    if not api_key:
        report["error"] = f"Skipped: {api_key_name} is not set."
        return [], report

    request = urllib.request.Request(source["url"], headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read()
            payload = json.loads(body.decode("utf-8", errors="replace"))
        records: list[dict[str, Any]] = []
        for pool in payload:
            for coin, details in pool.get("coins", {}).items():
                records.append(
                    {
                        "domain": pool.get("website", "").replace("https://", "").replace("http://", "").split("/", 1)[0],
                        "port": 443,
                        "scheme": "stratum+tcp",
                        "pool_name": pool.get("name", "UNKNOWN"),
                        "coin": coin,
                        "algorithm": details.get("algorithm", "UNKNOWN"),
                        "region": "UNKNOWN",
                        "source_type": source["source_type"],
                        "source_url": source["url"],
                        "confidence": source["confidence"],
                        "status": "unknown",
                        "first_seen": fetched_on,
                        "last_seen": fetched_on,
                        "notes": "minerstat metadata only; port is placeholder and must be manually reviewed.",
                    }
                )
        report.update(
            {
                "ok": True,
                "status": getattr(response, "status", 200),
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "records": len(records),
            }
        )
        return records, report
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        report["error"] = str(exc)
        return [], report


def run_collection(config: dict[str, Any], include_optional: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    policy = config.get("fetch_policy", {})
    sources = [source for source in config.get("sources", []) if source.get("enabled", True)]
    if include_optional:
        sources.extend([source for source in config.get("optional_sources", []) if source.get("enabled", False)])

    fetched_on = date.today().isoformat()
    records: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []

    for index, source in enumerate(sources):
        if source.get("method") == "authenticated_json_api":
            source_records, report = collect_minerstat_api(source, fetched_on)
        else:
            source_records, report = collect_source(source, policy, fetched_on)
        records.extend(source_records)
        reports.append(report)
        if index < len(sources) - 1:
            time.sleep(float(policy.get("delay_seconds", 1)))

    return records, reports


def unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    confidence_rank = {"confirmed": 3, "probable": 2, "candidate": 1, "retired": 0}

    for record in records:
        key = (record["domain"], int(record["port"]), record["scheme"], record["coin"].upper())
        current = best.get(key)
        if current is None:
            best[key] = record
            continue
        if confidence_rank[record["confidence"]] > confidence_rank[current["confidence"]]:
            best[key] = record
        elif record["source_url"] not in current["source_url"]:
            current["source_url"] = f"{current['source_url']}; {record['source_url']}"

    return list(best.values())


def write_outputs(records: list[dict[str, Any]], reports: list[dict[str, Any]]) -> None:
    DEFAULT_DISCOVERED_JSON.parent.mkdir(parents=True, exist_ok=True)
    normalized = build_library(unique_records(records))
    DEFAULT_DISCOVERED_JSON.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(normalized, DEFAULT_DISCOVERED_CSV)
    DEFAULT_REPORT.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch public mining pool intelligence sources.")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES, help="Path to source registry JSON.")
    parser.add_argument("--include-optional", action="store_true", help="Include optional authenticated sources.")
    parser.add_argument("--stdout", action="store_true", help="Print discovered records instead of writing files.")
    args = parser.parse_args(argv)

    config = load_source_config(args.sources)
    records, reports = run_collection(config, include_optional=args.include_optional)
    normalized = build_library(unique_records(records)) if records else []

    if args.stdout:
        print(json.dumps({"records": normalized, "report": reports}, ensure_ascii=False, indent=2))
    else:
        write_outputs(normalized, reports)
        ok_count = sum(1 for report in reports if report["ok"])
        print(f"Fetched {ok_count}/{len(reports)} sources.")
        print(f"Discovered {len(normalized)} normalized mining pool endpoint records.")
        print(f"Wrote {DEFAULT_DISCOVERED_JSON.relative_to(ROOT)} and {DEFAULT_DISCOVERED_CSV.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
