from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

try:
    from build_intel import FIELDS, build_library, parse_endpoint, write_csv
    from collect_intel import infer_coin_algorithm, infer_host_port_scheme, normalize_region
    from html_utils import extract_links
except ModuleNotFoundError:
    from scripts.build_intel import FIELDS, build_library, parse_endpoint, write_csv
    from scripts.collect_intel import infer_coin_algorithm, infer_host_port_scheme, normalize_region
    from scripts.html_utils import extract_links

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL_SITES = ROOT / "data" / "raw" / "pool_sites.json"
DEFAULT_DISCOVERED_JSON = ROOT / "data" / "raw" / "site_discovered_pool_domains.json"
DEFAULT_DISCOVERED_CSV = ROOT / "data" / "site_discovered_pool_domains.csv"
DEFAULT_BLOCKCHAIN_NODES_JSON = ROOT / "data" / "raw" / "blockchain_node_candidates.json"
DEFAULT_BLOCKCHAIN_NODES_CSV = ROOT / "data" / "blockchain_node_candidates.csv"
DEFAULT_REPORT = ROOT / "data" / "raw" / "site_discovery_report.json"
DEFAULT_CACHE_DIR = ROOT / "data" / "raw" / "site_discovery_cache"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36 mining-pool-domain-intel/0.1"
LINK_KEYWORDS = (
    "connect",
    "doc",
    "faq",
    "getting",
    "guide",
    "help",
    "mine",
    "mining",
    "pool",
    "server",
    "start",
    "stratum",
    "support",
)
STRATUM_RE = re.compile(
    r"\b(stratum\+(?:tcp|ssl)):/+(?:[^\s/@<>]+@)?([a-z0-9][a-z0-9.-]*[a-z0-9])\s*:\s*(\d{1,5})\b",
    flags=re.I,
)
HOST_PORT_RE = re.compile(
    r"(?<![@\w.-])([a-z0-9][a-z0-9.-]*\.[a-z0-9-]{2,63})\s*:\s*(\d{2,5})\b",
    flags=re.I,
)
IP_PORT_RE = re.compile(
    r"(?<![\w.-])((?:\d{1,3}\.){3}\d{1,3})\s*:\s*(\d{2,5})\b",
    flags=re.I,
)
BLOCKCHAIN_NODE_RE = re.compile(
    r"\b(addnode|seednode)\b\s*[=:]?\s*((?:\d{1,3}\.){3}\d{1,3})\s*:\s*(\d{2,5})\b",
    flags=re.I,
)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", flags=re.I | re.S)
NODE_FIELDS = [
    "node_host",
    "port",
    "node_type",
    "pool_name",
    "coin",
    "source_url",
    "first_seen",
    "last_seen",
    "notes",
]


def load_pool_sites(path: Path = DEFAULT_POOL_SITES) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def decode_body(body: bytes, content_type: str = "") -> str:
    charset = "utf-8"
    match = re.search(r"charset=([\w.-]+)", content_type, flags=re.I)
    if match:
        charset = match.group(1)
    return body.decode(charset, errors="replace")


def html_to_text(html_text: str) -> str:
    html_text = SCRIPT_STYLE_RE.sub(" ", html_text)
    html_text = TAG_RE.sub(" ", html_text)
    html_text = html_text.replace("&nbsp;", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", html_text)


def fetch_url(url: str, timeout: int = 25) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        return {
            "url": response.geturl(),
            "status": getattr(response, "status", 200),
            "body": body,
            "content_type": response.headers.get("Content-Type", ""),
        }


def canonical_domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def domain_matches(domain: str, suffix: str) -> bool:
    domain = domain.lower().strip(".")
    suffix = suffix.lower().strip(".")
    return domain == suffix or domain.endswith(f".{suffix}")


def likely_help_link(link: dict[str, str], site_domain: str) -> bool:
    parsed = urllib.parse.urlparse(link["url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if not domain_matches(canonical_domain(link["url"]), site_domain):
        return False
    haystack = f"{parsed.path} {parsed.query} {link.get('text', '')}".lower()
    return any(keyword in haystack for keyword in LINK_KEYWORDS)


def page_urls_for_site(site: dict[str, Any], homepage_html: str, max_pages: int) -> list[str]:
    homepage = site["website_url"]
    links = extract_links(homepage_html, homepage)
    urls = [homepage]
    for link in links:
        if likely_help_link(link, site["website_domain"]) and link["url"] not in urls:
            urls.append(link["url"])
        if len(urls) >= max_pages:
            break
    return urls


def infer_node_coin(port: int, site: dict[str, Any]) -> str:
    port_map = {
        8333: "BTC",
        18333: "BTC-TESTNET",
        9333: "LTC",
        22556: "DOGE",
    }
    return port_map.get(port, site.get("coin", "UNKNOWN") or "UNKNOWN")


def extract_blockchain_node_candidates(
    text: str,
    site: dict[str, Any],
    source_url: str,
    fetched_on: str,
) -> list[dict[str, Any]]:
    nodes: dict[tuple[str, int, str], dict[str, Any]] = {}
    for match in BLOCKCHAIN_NODE_RE.finditer(text):
        node_type, host, port_text = match.groups()
        port = int(port_text)
        key = (host, port, node_type.lower())
        nodes[key] = {
            "node_host": host,
            "port": port,
            "node_type": node_type.lower(),
            "pool_name": site["pool_name"],
            "coin": infer_node_coin(port, site),
            "source_url": source_url,
            "first_seen": fetched_on,
            "last_seen": fetched_on,
            "notes": (
                f"Blockchain peer node candidate extracted from {node_type.lower()} directive while "
                f"crawling {site['website_domain']}; not a mining pool Stratum endpoint."
            ),
        }
    return sorted(nodes.values(), key=lambda item: (item["node_host"], item["port"], item["node_type"]))


def node_endpoint_keys(nodes: list[dict[str, Any]]) -> set[tuple[str, int]]:
    return {(node["node_host"], int(node["port"])) for node in nodes}


def extract_site_endpoints(
    text: str,
    site: dict[str, Any],
    excluded_ip_ports: set[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    excluded_ip_ports = excluded_ip_ports or set()
    endpoints: dict[tuple[str, int, str], dict[str, Any]] = {}
    for match in STRATUM_RE.finditer(text):
        scheme, domain, port = match.groups()
        parsed = parse_endpoint(f"{scheme.lower()}://{domain}:{port}")
        endpoints[(parsed["domain"], parsed["port"], parsed["scheme"])] = parsed

    for match in HOST_PORT_RE.finditer(text):
        domain, port_text = match.groups()
        if not domain_matches(domain, site["website_domain"]):
            continue
        scheme = infer_host_port_scheme(domain, int(port_text), {"pool_name": site["pool_name"]})
        parsed = parse_endpoint(f"{domain}:{port_text}", fallback_scheme=scheme)
        endpoints[(parsed["domain"], parsed["port"], parsed["scheme"])] = parsed

    for match in IP_PORT_RE.finditer(text):
        domain, port_text = match.groups()
        if (domain, int(port_text)) in excluded_ip_ports:
            continue
        parsed = parse_endpoint(f"{domain}:{port_text}", fallback_scheme="stratum+tcp")
        endpoints[(parsed["domain"], parsed["port"], parsed["scheme"])] = parsed

    return sorted(endpoints.values(), key=lambda item: (item["domain"], item["port"], item["scheme"]))


def endpoint_to_record(endpoint: dict[str, Any], site: dict[str, Any], source_url: str, fetched_on: str) -> dict[str, Any]:
    source = {"pool_name": site["pool_name"]}
    coin, algorithm = infer_coin_algorithm(endpoint["domain"], int(endpoint["port"]), source)
    return {
        "domain": endpoint["domain"],
        "port": int(endpoint["port"]),
        "scheme": endpoint["scheme"],
        "pool_name": site["pool_name"],
        "coin": coin,
        "algorithm": algorithm,
        "region": normalize_region(endpoint["domain"]),
        "source_type": "official",
        "source_url": source_url,
        "confidence": "confirmed",
        "status": "active",
        "first_seen": fetched_on,
        "last_seen": fetched_on,
        "notes": f"Discovered by shallow crawling official website domain {site['website_domain']}.",
    }


def cache_path_for(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return DEFAULT_CACHE_DIR / f"{digest}.json"


def load_cache(url: str) -> dict[str, Any] | None:
    path = cache_path_for(url)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(url: str, response: dict[str, Any]) -> None:
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": response["url"],
        "status": response["status"],
        "content_type": response["content_type"],
        "body": response["body"].decode("latin1"),
    }
    cache_path_for(url).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def response_from_cache(url: str) -> dict[str, Any] | None:
    cached = load_cache(url)
    if not cached:
        return None
    return {
        "url": cached["url"],
        "status": cached["status"],
        "content_type": cached["content_type"],
        "body": cached["body"].encode("latin1"),
    }


def fetch_with_cache(url: str, timeout: int) -> tuple[dict[str, Any] | None, bool, str | None]:
    try:
        response = fetch_url(url, timeout)
        save_cache(url, response)
        return response, False, None
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        cached = response_from_cache(url)
        if cached:
            return cached, True, str(exc)
        return None, False, str(exc)


def discover_site(
    site: dict[str, Any],
    max_pages: int,
    timeout: int,
    page_delay_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    reports: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    blockchain_node_records: list[dict[str, Any]] = []
    fetched_on = date.today().isoformat()
    homepage_response, used_cache, error = fetch_with_cache(site["website_url"], timeout)
    if not homepage_response:
        reports.append({
            "pool_name": site["pool_name"],
            "url": site["website_url"],
            "ok": False,
            "used_cache": False,
            "records": 0,
            "error": error,
        })
        return records, blockchain_node_records, reports

    homepage_html = decode_body(homepage_response["body"], homepage_response["content_type"])
    page_urls = page_urls_for_site(site, homepage_html, max_pages)
    for index, page_url in enumerate(page_urls):
        response, page_used_cache, page_error = (homepage_response, used_cache, error) if page_url == site["website_url"] else fetch_with_cache(page_url, timeout)
        page_records: list[dict[str, Any]] = []
        page_blockchain_nodes: list[dict[str, Any]] = []
        if response:
            html_text = decode_body(response["body"], response["content_type"])
            text = html_to_text(html_text)
            page_blockchain_nodes = extract_blockchain_node_candidates(text, site, response["url"], fetched_on)
            blockchain_node_records.extend(page_blockchain_nodes)
            endpoints = extract_site_endpoints(text, site, node_endpoint_keys(page_blockchain_nodes))
            page_records = [endpoint_to_record(endpoint, site, response["url"], fetched_on) for endpoint in endpoints]
            records.extend(page_records)
        reports.append({
            "pool_name": site["pool_name"],
            "url": page_url,
            "ok": bool(response),
            "used_cache": page_used_cache,
            "records": len(page_records),
            "blockchain_nodes": len(page_blockchain_nodes),
            "error": page_error,
        })
        if index < len(page_urls) - 1:
            time.sleep(page_delay_seconds)
    return records, blockchain_node_records, reports


def unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    for record in records:
        key = (record["domain"], int(record["port"]), record["scheme"], record["coin"].upper())
        current = best.get(key)
        if current is None:
            best[key] = record
        elif record["source_url"] not in current["source_url"]:
            current["source_url"] = f"{current['source_url']}; {record['source_url']}"
    return list(best.values())


def unique_blockchain_node_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, int, str], dict[str, Any]] = {}
    for record in records:
        key = (record["node_host"], int(record["port"]), record["node_type"])
        current = best.get(key)
        if current is None:
            best[key] = record
        elif record["source_url"] not in current["source_url"]:
            current["source_url"] = f"{current['source_url']}; {record['source_url']}"
            current["last_seen"] = max(current["last_seen"], record["last_seen"])
    return sorted(best.values(), key=lambda item: (item["node_host"], item["port"], item["node_type"]))


def write_node_csv(records: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=NODE_FIELDS)
        writer.writeheader()
        writer.writerows(records)


def write_outputs(
    records: list[dict[str, Any]],
    blockchain_node_records: list[dict[str, Any]],
    reports: list[dict[str, Any]],
) -> None:
    DEFAULT_DISCOVERED_JSON.parent.mkdir(parents=True, exist_ok=True)
    normalized = build_library(unique_records(records))
    normalized_blockchain_nodes = unique_blockchain_node_records(blockchain_node_records)
    DEFAULT_DISCOVERED_JSON.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(normalized, DEFAULT_DISCOVERED_CSV)
    DEFAULT_BLOCKCHAIN_NODES_JSON.write_text(
        json.dumps(normalized_blockchain_nodes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_node_csv(normalized_blockchain_nodes, DEFAULT_BLOCKCHAIN_NODES_CSV)
    DEFAULT_REPORT.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover mining pool Stratum domains from collected official website domains.")
    parser.add_argument("--pool-sites", type=Path, default=DEFAULT_POOL_SITES)
    parser.add_argument("--max-sites", type=int, default=0, help="Limit sites for smoke tests; 0 means all.")
    parser.add_argument("--max-pages-per-site", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--delay-between-sites", type=float, default=0.8)
    parser.add_argument("--delay-between-pages", type=float, default=0.5)
    args = parser.parse_args(argv)

    sites = load_pool_sites(args.pool_sites)
    if args.max_sites > 0:
        sites = sites[: args.max_sites]

    records: list[dict[str, Any]] = []
    blockchain_node_records: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for index, site in enumerate(sites):
        site_records, site_blockchain_nodes, site_reports = discover_site(
            site,
            args.max_pages_per_site,
            args.timeout,
            args.delay_between_pages,
        )
        records.extend(site_records)
        blockchain_node_records.extend(site_blockchain_nodes)
        reports.extend(site_reports)
        if index < len(sites) - 1:
            time.sleep(args.delay_between_sites)

    write_outputs(records, blockchain_node_records, reports)
    print(f"Crawled {len(sites)} official website domains.")
    print(f"Discovered {len(build_library(unique_records(records)) if records else [])} normalized mining pool endpoint records.")
    print(f"Separated {len(unique_blockchain_node_records(blockchain_node_records))} blockchain node candidates.")
    print(f"Wrote {DEFAULT_DISCOVERED_JSON.relative_to(ROOT)} and {DEFAULT_DISCOVERED_CSV.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
