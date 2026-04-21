from __future__ import annotations

import csv
import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any

FIELDS = [
    "domain",
    "port",
    "scheme",
    "pool_name",
    "coin",
    "algorithm",
    "region",
    "source_type",
    "source_url",
    "confidence",
    "status",
    "first_seen",
    "last_seen",
    "notes",
]

SCHEMES = {"stratum+tcp", "stratum+ssl"}
SOURCE_TYPES = {"official", "aggregator", "open_source", "ct_log", "dns", "manual_review"}
CONFIDENCE = {"confirmed", "probable", "candidate", "retired"}
STATUS = {"active", "inactive", "unknown", "retired"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$"
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "data" / "raw" / "mining_pool_domains.seed.json"
DEFAULT_DATA_DIR = ROOT / "data"


def parse_endpoint(
    value: str,
    fallback_port: int | str | None = None,
    fallback_scheme: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("endpoint/domain must be a non-empty string")

    raw = value.strip()
    scheme = fallback_scheme
    scheme_match = re.match(r"^([a-z][a-z0-9+.-]*)://", raw, flags=re.I)
    if scheme_match:
        scheme = scheme_match.group(1).lower()
        raw = raw[scheme_match.end() :]

    raw = re.sub(r"^[^@\s/]+@", "", raw)
    raw = re.split(r"[/#?\s]", raw, maxsplit=1)[0]

    domain = raw
    port = fallback_port
    port_match = re.match(r"^(.+):(\d{1,5})$", raw)
    if port_match:
        domain = port_match.group(1)
        port = int(port_match.group(2))

    domain = domain.removeprefix("[").removesuffix("]").removesuffix(".").lower()
    if port not in (None, ""):
        port = int(port)

    return {"domain": domain, "port": port, "scheme": scheme}


def count_source_urls(source_url: str) -> int:
    return len([item for item in str(source_url or "").split(";") if item.strip()])


def is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _ensure_enum(value: str, allowed: set[str], label: str, errors: list[str]) -> None:
    if value not in allowed:
        errors.append(f"{label} must be one of: {', '.join(sorted(allowed))}")


def normalize_record(record: dict[str, Any], index: int = 0) -> dict[str, Any]:
    errors: list[str] = []
    parsed = parse_endpoint(record.get("domain", ""), record.get("port"), record.get("scheme"))
    normalized: dict[str, Any] = {field: record.get(field, "") for field in FIELDS}

    normalized["domain"] = parsed["domain"]
    normalized["port"] = parsed["port"]
    normalized["scheme"] = str(parsed.get("scheme") or "").lower()
    normalized["source_type"] = str(normalized["source_type"]).lower()
    normalized["confidence"] = str(normalized["confidence"]).lower()
    normalized["status"] = str(normalized["status"]).lower()

    for field in FIELDS:
        if field != "port":
            normalized[field] = str(normalized[field]).strip()

    if not DOMAIN_RE.fullmatch(normalized["domain"]) and not is_ip_literal(normalized["domain"]):
        errors.append(f"domain is not a valid normalized FQDN or IP literal: {normalized['domain']}")
    if not isinstance(normalized["port"], int) or not (1 <= normalized["port"] <= 65535):
        errors.append(f"port must be an integer from 1 to 65535: {record.get('port')}")

    _ensure_enum(normalized["scheme"], SCHEMES, "scheme", errors)
    _ensure_enum(normalized["source_type"], SOURCE_TYPES, "source_type", errors)
    _ensure_enum(normalized["confidence"], CONFIDENCE, "confidence", errors)
    _ensure_enum(normalized["status"], STATUS, "status", errors)

    for field in FIELDS:
        if field != "notes" and normalized.get(field) in ("", None):
            errors.append(f"{field} is required")

    for field in ("first_seen", "last_seen"):
        if not DATE_RE.fullmatch(normalized[field]):
            errors.append(f"{field} must use YYYY-MM-DD")

    if any(mark in normalized["domain"] for mark in ("/", "@")) or re.search(r"\s", normalized["domain"]):
        errors.append("domain must not contain scheme, path, userinfo, or whitespace")

    if normalized["confidence"] == "confirmed" and normalized["source_type"] != "official":
        errors.append("confirmed records must use source_type=official")
    if normalized["confidence"] == "confirmed" and not normalized["source_url"].startswith("http"):
        errors.append("confirmed records must include an official source_url")
    if normalized["confidence"] == "probable" and count_source_urls(normalized["source_url"]) < 2:
        errors.append("probable records must include at least two source URLs separated by semicolons")
    if normalized["confidence"] == "retired" and normalized["status"] != "retired":
        errors.append("retired confidence records must use status=retired")

    if errors:
        key = f"{record.get('domain', 'unknown')}:{record.get('port', 'unknown')}"
        raise ValueError(f"Record {index + 1} ({key}) failed validation:\n- " + "\n- ".join(errors))

    return normalized


def build_library(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    normalized_records: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        item = normalize_record(record, index)
        key = "|".join([item["domain"], str(item["port"]), item["scheme"], item["coin"].upper()])
        if key in seen:
            raise ValueError(f"Duplicate record for domain+port+scheme+coin: {key}")
        seen.add(key)
        normalized_records.append(item)

    return sorted(
        normalized_records,
        key=lambda item: (item["pool_name"], item["coin"], item["domain"], item["port"], item["scheme"]),
    )


def build_watchlist(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if record["status"] == "active" and record["confidence"] in {"confirmed", "probable"}
    ]


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    seed_path = Path(args[0]).resolve() if args else DEFAULT_SEED
    records = json.loads(seed_path.read_text(encoding="utf-8"))
    library = build_library(records)
    watchlist = build_watchlist(library)

    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DEFAULT_DATA_DIR / "mining_pool_domains.json").write_text(
        json.dumps(library, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(library, DEFAULT_DATA_DIR / "mining_pool_domains.csv")
    (DEFAULT_DATA_DIR / "watchlist.json").write_text(
        json.dumps(watchlist, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(watchlist, DEFAULT_DATA_DIR / "watchlist.csv")

    print(f"Built {len(library)} mining pool domain records.")
    print(f"Built {len(watchlist)} active alert-candidate records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
