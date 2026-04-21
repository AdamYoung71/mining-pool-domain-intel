from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from build_intel import build_library as build_endpoint_library
    from build_intel import write_csv as write_endpoint_csv
    from html_utils import extract_links
except ModuleNotFoundError:
    from scripts.build_intel import build_library as build_endpoint_library
    from scripts.build_intel import write_csv as write_endpoint_csv
    from scripts.html_utils import extract_links

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "data" / "sources" / "pool_site_sources.json"
DEFAULT_POOL_SITES_JSON = ROOT / "data" / "raw" / "pool_sites.json"
DEFAULT_POOL_SITES_CSV = ROOT / "data" / "pool_sites.csv"
DEFAULT_IP_ENDPOINTS_JSON = ROOT / "data" / "raw" / "ip_pool_endpoint_candidates.json"
DEFAULT_IP_ENDPOINTS_CSV = ROOT / "data" / "ip_pool_endpoint_candidates.csv"
DEFAULT_REPORT = ROOT / "data" / "raw" / "pool_site_fetch_report.json"
DEFAULT_CACHE_DIR = ROOT / "data" / "raw" / "pool_site_cache"

FIELDS = [
    "pool_name",
    "website_domain",
    "website_url",
    "profile_url",
    "directory_source",
    "source_type",
    "confidence",
    "first_seen",
    "last_seen",
    "notes",
]

SOCIAL_OR_NON_SITE_DOMAINS = {
    "bitcointalk.org",
    "discord.com",
    "facebook.com",
    "github.com",
    "medium.com",
    "t.me",
    "telegram.me",
    "twitter.com",
    "x.com",
    "youtube.com",
}


def load_config(path: Path = DEFAULT_SOURCES) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_url(
    url: str,
    user_agent: str,
    timeout: int,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        return {
            "url": response.geturl(),
            "status": getattr(response, "status", 200),
            "body": body,
            "content_type": response.headers.get("Content-Type", ""),
        }


def decode_body(body: bytes, content_type: str = "") -> str:
    charset = "utf-8"
    match = re.search(r"charset=([\w.-]+)", content_type, flags=re.I)
    if match:
        charset = match.group(1)
    return body.decode(charset, errors="replace")


def canonical_domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or parsed.netloc or parsed.path.split("/", 1)[0]).lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def is_ip_address(domain: str) -> bool:
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", domain):
        return True
    return ":" in domain


def is_ip_endpoint_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    return bool(host and is_ip_address(host) and parsed.port)


def is_non_site_domain(domain: str) -> bool:
    if is_ip_address(domain):
        return True
    return any(domain == blocked or domain.endswith(f".{blocked}") for blocked in SOCIAL_OR_NON_SITE_DOMAINS)


def pool_name_from_slug(slug: str) -> str:
    overrides = {
        "f2pool": "F2Pool",
        "viabtc": "ViaBTC",
        "2miners": "2Miners",
        "k1pool": "K1 Pool",
    }
    return overrides.get(slug, slug.replace("-", " ").title())


def pool_name_from_domain(domain: str) -> str:
    root = domain.split(".")[0]
    return pool_name_from_slug(root)


def extract_minerstat_profiles(html_text: str, source: dict[str, Any]) -> list[dict[str, str]]:
    links = extract_links(html_text, source["url"])
    profiles: dict[str, dict[str, str]] = {}
    for link in links:
        parsed = urllib.parse.urlparse(link["url"])
        match = re.fullmatch(r"/pools/([a-z0-9-]+)", parsed.path)
        if not match:
            continue
        slug = match.group(1)
        if slug in {"pools"}:
            continue
        profiles[slug] = {
            "pool_name": link["text"] or pool_name_from_slug(slug),
            "profile_url": urllib.parse.urljoin(source.get("base_url", source["url"]), f"/pools/{slug}"),
            "slug": slug,
        }
    return sorted(profiles.values(), key=lambda item: item["pool_name"].lower())


def extract_official_site(profile_html: str, profile_url: str, pool_name: str) -> tuple[str, str] | None:
    links = extract_links(profile_html, profile_url)
    preferred: list[dict[str, str]] = []
    fallback: list[dict[str, str]] = []
    for link in links:
        parsed = urllib.parse.urlparse(link["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        domain = canonical_domain(link["url"])
        if domain == "minerstat.com" or domain.endswith(".minerstat.com") or is_non_site_domain(domain):
            continue
        text = link["text"].lower()
        if "website" in text:
            preferred.append(link)
        else:
            fallback.append(link)

    candidates = preferred or fallback
    if not candidates:
        return None
    url = candidates[0]["url"]
    parsed = urllib.parse.urlparse(url)
    website_url = f"{parsed.scheme}://{parsed.hostname}/"
    return website_url, canonical_domain(website_url)


def cache_path_for(source_id: str, key: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", key)
    return DEFAULT_CACHE_DIR / source_id / f"{safe}.json"


def load_cache(source_id: str, key: str) -> dict[str, Any] | None:
    path = cache_path_for(source_id, key)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(source_id: str, key: str, payload: dict[str, Any]) -> None:
    path = cache_path_for(source_id, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_with_retries(url: str, policy: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    attempts = int(policy.get("max_retries", 0)) + 1
    last_error: str | None = None
    for attempt in range(attempts):
        try:
            return fetch_url(url, policy["user_agent"], int(policy["timeout_seconds"])), None
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = str(exc)
            if attempt < attempts - 1:
                time.sleep(float(policy.get("delay_seconds", 0.8)))
    return None, last_error


def fetch_with_retries_headers(
    url: str,
    policy: dict[str, Any],
    extra_headers: dict[str, str],
) -> tuple[dict[str, Any] | None, str | None]:
    attempts = int(policy.get("max_retries", 0)) + 1
    last_error: str | None = None
    for attempt in range(attempts):
        try:
            return fetch_url(url, policy["user_agent"], int(policy["timeout_seconds"]), extra_headers), None
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = str(exc)
            if attempt < attempts - 1:
                time.sleep(float(policy.get("delay_seconds", 0.8)))
    return None, last_error


def worker_count(requested_workers: int, item_count: int) -> int:
    if item_count <= 0:
        return 1
    return max(1, min(requested_workers, item_count))


def collect_minerstat_profile(
    source: dict[str, Any],
    policy: dict[str, Any],
    profile: dict[str, str],
    fetched_on: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    response, error = fetch_with_retries(profile["profile_url"], policy)
    used_cache = False
    site = None
    if response:
        profile_html = decode_body(response["body"], response["content_type"])
        site = extract_official_site(profile_html, profile["profile_url"], profile["pool_name"])
        save_cache(source["id"], profile["slug"], {
            "profile": profile,
            "site": site,
            "sha256": hashlib.sha256(response["body"]).hexdigest(),
        })
    else:
        cached = load_cache(source["id"], profile["slug"])
        if cached:
            used_cache = True
            site = cached.get("site")

    report = {
        "source_id": source["id"],
        "profile": profile["slug"],
        "url": profile["profile_url"],
        "ok": bool(response),
        "used_cache": used_cache,
        "records": 1 if site else 0,
        "error": error,
    }

    if not site:
        return None, report

    website_url, website_domain = site
    return {
        "pool_name": profile["pool_name"],
        "website_domain": website_domain,
        "website_url": website_url,
        "profile_url": profile["profile_url"],
        "directory_source": source["url"],
        "source_type": source["source_type"],
        "confidence": source["confidence"],
        "first_seen": fetched_on,
        "last_seen": fetched_on,
        "notes": f"Official website link extracted from {source['name']} profile.",
    }, report


def collect_from_minerstat(
    source: dict[str, Any],
    policy: dict[str, Any],
    max_profiles: int = 0,
    workers: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fetched_on = date.today().isoformat()
    reports: list[dict[str, Any]] = []
    directory_response, directory_error = fetch_with_retries(source["url"], policy)
    if not directory_response:
        cached = load_cache(source["id"], "directory_profiles")
        profiles = cached.get("profiles", []) if cached else []
        reports.append({
            "source_id": source["id"],
            "url": source["url"],
            "ok": False,
            "used_cache": bool(cached),
            "records": len(profiles),
            "error": directory_error,
        })
    else:
        html_text = decode_body(directory_response["body"], directory_response["content_type"])
        profiles = extract_minerstat_profiles(html_text, source)
        save_cache(source["id"], "directory_profiles", {"profiles": profiles})
        reports.append({
            "source_id": source["id"],
            "url": source["url"],
            "ok": True,
            "used_cache": False,
            "status": directory_response["status"],
            "sha256": hashlib.sha256(directory_response["body"]).hexdigest(),
            "bytes": len(directory_response["body"]),
            "records": len(profiles),
            "error": None,
        })

    if max_profiles > 0:
        profiles = profiles[:max_profiles]

    records: list[dict[str, Any]] = []
    result_slots: list[tuple[dict[str, Any] | None, dict[str, Any]] | None] = [None] * len(profiles)
    resolved_workers = worker_count(workers, len(profiles))
    delay_seconds = float(policy.get("delay_seconds", 0.8))
    if resolved_workers == 1:
        for index, profile in enumerate(profiles):
            try:
                result_slots[index] = collect_minerstat_profile(source, policy, profile, fetched_on)
            except Exception as exc:  # pragma: no cover - defensive guard for live crawls
                result_slots[index] = (None, {
                    "source_id": source["id"],
                    "profile": profile["slug"],
                    "url": profile["profile_url"],
                    "ok": False,
                    "used_cache": False,
                    "records": 0,
                    "error": f"Unhandled crawler error: {exc}",
                })
            if index < len(profiles) - 1:
                time.sleep(delay_seconds)
    else:
        with ThreadPoolExecutor(max_workers=resolved_workers) as executor:
            future_to_index = {}
            for index, profile in enumerate(profiles):
                future = executor.submit(collect_minerstat_profile, source, policy, profile, fetched_on)
                future_to_index[future] = index
                if index < len(profiles) - 1:
                    time.sleep(delay_seconds)
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                profile = profiles[index]
                try:
                    result_slots[index] = future.result()
                except Exception as exc:  # pragma: no cover - defensive guard for live crawls
                    result_slots[index] = (None, {
                        "source_id": source["id"],
                        "profile": profile["slug"],
                        "url": profile["profile_url"],
                        "ok": False,
                        "used_cache": False,
                        "records": 0,
                        "error": f"Unhandled crawler error: {exc}",
                    })

    for result in result_slots:
        if result is None:
            continue
        record, report = result
        reports.append(report)
        if record:
            records.append(record)

    return records, reports


def extract_miningpoolstats_coin_urls(sitemap_xml: str, source: dict[str, Any]) -> list[str]:
    base = source.get("base_url", "https://miningpoolstats.stream").rstrip("/")
    urls: list[str] = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap_xml):
        if not loc.startswith(base + "/"):
            continue
        path = urllib.parse.urlparse(loc).path.strip("/")
        if not path or "/" in path:
            continue
        urls.append(loc)
    return sorted(dict.fromkeys(urls))


def miningpoolstats_data_url(coin_html: str) -> str | None:
    match = re.search(
        r"https://data\.miningpoolstats\.stream/data/[a-z0-9_.-]+\.js\?t=\d+",
        coin_html,
        flags=re.I,
    )
    return match.group(0) if match else None


def collect_miningpoolstats_coin(
    source: dict[str, Any],
    policy: dict[str, Any],
    coin_url: str,
    fetched_on: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    coin_slug = urllib.parse.urlparse(coin_url).path.strip("/")
    records: list[dict[str, Any]] = []
    ip_endpoint_records: list[dict[str, Any]] = []
    coin_response, coin_error = fetch_with_retries(coin_url, policy)
    coin_records = 0
    if coin_response:
        coin_html = decode_body(coin_response["body"], coin_response["content_type"])
        data_url = miningpoolstats_data_url(coin_html)
        if data_url:
            data_response, data_error = fetch_with_retries_headers(
                data_url,
                policy,
                {
                    "Accept": "application/json,text/plain,*/*",
                    "Origin": source.get("data_origin", source.get("base_url", "")),
                    "Referer": coin_url,
                },
            )
            if data_response:
                payload = json.loads(decode_body(data_response["body"], data_response["content_type"]))
                for item in payload.get("data", []):
                    url = str(item.get("url", "")).strip()
                    if not url.startswith(("http://", "https://")):
                        continue
                    website_domain = canonical_domain(url)
                    parsed = urllib.parse.urlparse(url)
                    if is_ip_endpoint_url(url):
                        port = parsed.port
                        if isinstance(port, int) and 1 <= port <= 65535:
                            ip_endpoint_records.append({
                                "domain": website_domain,
                                "port": port,
                                "scheme": "stratum+tcp",
                                "pool_name": str(item.get("pool_id") or website_domain),
                                "coin": coin_slug.upper(),
                                "algorithm": "UNKNOWN",
                                "region": "UNKNOWN",
                                "source_type": "aggregator",
                                "source_url": coin_url,
                                "confidence": "candidate",
                                "status": "unknown",
                                "first_seen": fetched_on,
                                "last_seen": fetched_on,
                                "notes": f"IP:port pool URL extracted from MiningPoolStats data file for {coin_slug}; verify protocol before promotion.",
                            })
                        coin_records += 1
                        continue
                    if not website_domain or is_non_site_domain(website_domain):
                        continue
                    website_url = f"{parsed.scheme}://{parsed.hostname}/"
                    pool_name = item.get("pool_id") or pool_name_from_domain(website_domain)
                    records.append({
                        "pool_name": str(pool_name),
                        "website_domain": website_domain,
                        "website_url": website_url,
                        "profile_url": coin_url,
                        "directory_source": source["url"],
                        "source_type": source["source_type"],
                        "confidence": source["confidence"],
                        "first_seen": fetched_on,
                        "last_seen": fetched_on,
                        "notes": f"Pool website URL extracted from MiningPoolStats data file for {coin_slug}.",
                    })
                    coin_records += 1
                coin_error = None
            else:
                coin_error = data_error
        else:
            coin_error = "No MiningPoolStats data file preload URL found."

    return records, ip_endpoint_records, {
        "source_id": source["id"],
        "coin": coin_slug,
        "url": coin_url,
        "ok": coin_records > 0,
        "used_cache": False,
        "records": coin_records,
        "error": coin_error,
    }


def miningpoolstats_error_report(source: dict[str, Any], coin_url: str, error: Exception) -> dict[str, Any]:
    return {
        "source_id": source["id"],
        "coin": urllib.parse.urlparse(coin_url).path.strip("/"),
        "url": coin_url,
        "ok": False,
        "used_cache": False,
        "records": 0,
        "error": f"Unhandled crawler error: {error}",
    }


def collect_from_miningpoolstats(
    source: dict[str, Any],
    policy: dict[str, Any],
    max_coin_pages: int | None = None,
    workers: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    fetched_on = date.today().isoformat()
    records: list[dict[str, Any]] = []
    ip_endpoint_records: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    sitemap_response, sitemap_error = fetch_with_retries(source["url"], policy)
    if not sitemap_response:
        reports.append({
            "source_id": source["id"],
            "url": source["url"],
            "ok": False,
            "used_cache": False,
            "records": 0,
            "error": sitemap_error,
        })
        return records, reports, ip_endpoint_records

    sitemap_text = decode_body(sitemap_response["body"], sitemap_response["content_type"])
    coin_urls = extract_miningpoolstats_coin_urls(sitemap_text, source)
    configured_limit = int(source.get("max_coin_pages", 0))
    effective_limit = configured_limit if max_coin_pages is None else max_coin_pages
    if effective_limit > 0:
        coin_urls = coin_urls[:effective_limit]

    reports.append({
        "source_id": source["id"],
        "url": source["url"],
        "ok": True,
        "used_cache": False,
        "status": sitemap_response["status"],
        "sha256": hashlib.sha256(sitemap_response["body"]).hexdigest(),
        "bytes": len(sitemap_response["body"]),
        "records": len(coin_urls),
        "error": None,
    })

    result_slots: list[tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]] | None] = [
        None
    ] * len(coin_urls)
    resolved_workers = worker_count(workers, len(coin_urls))
    delay_seconds = float(policy.get("delay_seconds", 0.8))
    if resolved_workers == 1:
        for index, coin_url in enumerate(coin_urls):
            try:
                result_slots[index] = collect_miningpoolstats_coin(source, policy, coin_url, fetched_on)
            except Exception as exc:  # pragma: no cover - defensive guard for live crawls
                result_slots[index] = ([], [], miningpoolstats_error_report(source, coin_url, exc))
            if index < len(coin_urls) - 1:
                time.sleep(delay_seconds)
    else:
        with ThreadPoolExecutor(max_workers=resolved_workers) as executor:
            future_to_index = {}
            for index, coin_url in enumerate(coin_urls):
                future = executor.submit(collect_miningpoolstats_coin, source, policy, coin_url, fetched_on)
                future_to_index[future] = index
                if index < len(coin_urls) - 1:
                    time.sleep(delay_seconds)
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                coin_url = coin_urls[index]
                try:
                    result_slots[index] = future.result()
                except Exception as exc:  # pragma: no cover - defensive guard for live crawls
                    result_slots[index] = ([], [], miningpoolstats_error_report(source, coin_url, exc))

    for result in result_slots:
        if result is None:
            continue
        coin_records, coin_ip_endpoint_records, report = result
        records.extend(coin_records)
        ip_endpoint_records.extend(coin_ip_endpoint_records)
        reports.append(report)

    return records, reports, ip_endpoint_records


def unique_sites(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record["website_domain"]
        if not key or is_non_site_domain(key):
            continue
        current = unique.get(key)
        if current is None:
            unique[key] = record
        elif record["profile_url"] not in current["profile_url"]:
            current["profile_url"] = f"{current['profile_url']}; {record['profile_url']}"
    return sorted(unique.values(), key=lambda item: (item["pool_name"].lower(), item["website_domain"]))


def write_outputs(
    records: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    ip_endpoint_records: list[dict[str, Any]] | None = None,
) -> None:
    def writable_csv_path(path: Path) -> Path:
        try:
            with path.open("a", newline="", encoding="utf-8"):
                pass
            return path
        except PermissionError:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            return path.with_name(f"{path.stem}.{stamp}{path.suffix}")

    DEFAULT_POOL_SITES_JSON.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_POOL_SITES_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pool_sites_csv = writable_csv_path(DEFAULT_POOL_SITES_CSV)
    with pool_sites_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    DEFAULT_REPORT.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if ip_endpoint_records is not None:
        normalized_ip_endpoints = build_endpoint_library(ip_endpoint_records) if ip_endpoint_records else []
        DEFAULT_IP_ENDPOINTS_JSON.write_text(
            json.dumps(normalized_ip_endpoints, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_endpoint_csv(normalized_ip_endpoints, writable_csv_path(DEFAULT_IP_ENDPOINTS_CSV))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect public mining pool official website domains.")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--max-profiles", type=int, default=0, help="Limit minerstat profiles for smoke tests; 0 means all.")
    parser.add_argument("--max-miningpoolstats-coins", type=int, default=None, help="Override MiningPoolStats coin-page limit; 0 means all sitemap coin pages.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent directory pages to fetch.")
    parser.add_argument("--only-source", action="append", default=[], help="Run only the selected source id; may be repeated.")
    parser.add_argument("--merge-existing", action="store_true", help="Merge collected records with existing pool_sites.json before writing.")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.sources)
    policy = config.get("fetch_policy", {})
    records: list[dict[str, Any]] = []
    ip_endpoint_records: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for source in config.get("sources", []):
        if not source.get("enabled", True):
            continue
        if args.only_source and source["id"] not in set(args.only_source):
            continue
        if source["method"] == "minerstat_directory_profiles":
            source_records, source_reports = collect_from_minerstat(source, policy, args.max_profiles, args.workers)
            records.extend(source_records)
            reports.extend(source_reports)
        elif source["method"] == "miningpoolstats_sitemap_data":
            source_records, source_reports, source_ip_endpoints = collect_from_miningpoolstats(source, policy, args.max_miningpoolstats_coins, args.workers)
            records.extend(source_records)
            reports.extend(source_reports)
            ip_endpoint_records.extend(source_ip_endpoints)

    if args.merge_existing and DEFAULT_POOL_SITES_JSON.exists():
        records = json.loads(DEFAULT_POOL_SITES_JSON.read_text(encoding="utf-8")) + records
    records = unique_sites(records)
    if args.stdout:
        print(json.dumps({"records": records, "report": reports}, ensure_ascii=False, indent=2))
    else:
        write_outputs(records, reports, ip_endpoint_records)
        print(f"Collected {len(records)} unique mining pool website domains with {max(1, args.workers)} worker(s).")
        if ip_endpoint_records:
            print(f"Collected {len(build_endpoint_library(ip_endpoint_records))} IP:port pool endpoint candidates.")
        print(f"Wrote {DEFAULT_POOL_SITES_JSON.relative_to(ROOT)} and {DEFAULT_POOL_SITES_CSV.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
