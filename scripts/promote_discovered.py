from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from build_intel import FIELDS, build_library, is_ip_literal, normalize_record
except ModuleNotFoundError:
    from scripts.build_intel import FIELDS, build_library, is_ip_literal, normalize_record

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "data" / "raw" / "mining_pool_domains.seed.json"
DEFAULT_OUTPUT = DEFAULT_SEED
DEFAULT_REPORT = ROOT / "data" / "raw" / "promotion_report.json"

DISCOVERED_SOURCES = [
    {
        "label": "priority_sources",
        "path": ROOT / "data" / "raw" / "discovered_pool_domains.json",
        "kind": "trusted",
    },
    {
        "label": "official_site_crawl",
        "path": ROOT / "data" / "raw" / "site_discovered_pool_domains.json",
        "kind": "trusted",
    },
    {
        "label": "github_code_search",
        "path": ROOT / "data" / "raw" / "github_pool_endpoint_candidates.json",
        "kind": "candidate",
    },
    {
        "label": "ip_endpoint_candidates",
        "path": ROOT / "data" / "raw" / "ip_pool_endpoint_candidates.json",
        "kind": "candidate",
    },
]

CONFIDENCE_RANK = {"retired": 0, "candidate": 1, "probable": 2, "confirmed": 3}
SOURCE_TYPE_RANK = {
    "ct_log": 0,
    "dns": 0,
    "open_source": 1,
    "aggregator": 2,
    "manual_review": 2,
    "official": 3,
}


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return [item for item in payload["records"] if isinstance(item, dict)]
    return []


def split_source_urls(value: str) -> list[str]:
    urls = []
    seen = set()
    for item in str(value or "").split(";"):
        url = item.strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def join_source_urls(urls: list[str]) -> str:
    return "; ".join(dict.fromkeys(urls))


def record_key(record: dict[str, Any]) -> str:
    return "|".join(
        [
            record["domain"],
            str(record["port"]),
            record["scheme"],
            record["coin"].upper(),
        ]
    )


def has_unknown_identity(record: dict[str, Any]) -> bool:
    return record["coin"].upper() == "UNKNOWN" or record["algorithm"].upper() == "UNKNOWN"


def should_stay_candidate(record: dict[str, Any]) -> bool:
    return has_unknown_identity(record) or is_ip_literal(record["domain"])


def demote_to_candidate(record: dict[str, Any]) -> None:
    record["confidence"] = "candidate"
    record["status"] = "unknown"


def prepare_record(raw_record: dict[str, Any], source_label: str, source_kind: str) -> tuple[dict[str, Any] | None, str | None]:
    record = {field: raw_record.get(field, "") for field in FIELDS}
    record["notes"] = str(record.get("notes") or "").strip()

    if source_kind == "candidate":
        record["confidence"] = "candidate"
        record["status"] = "unknown"

    if record.get("confidence") == "probable" and len(split_source_urls(record.get("source_url", ""))) < 2:
        demote_to_candidate(record)
    if record.get("confidence") == "confirmed" and record.get("source_type") != "official":
        demote_to_candidate(record)

    try:
        normalized = normalize_record(record)
    except ValueError as exc:
        return None, str(exc)

    if should_stay_candidate(normalized):
        demote_to_candidate(normalized)
        normalized = normalize_record(normalized)

    note = f"auto_source={source_label}"
    normalized["notes"] = f"{normalized['notes']} {note}".strip()
    return normalized, None


def better_source_type(left: str, right: str) -> str:
    return left if SOURCE_TYPE_RANK.get(left, -1) >= SOURCE_TYPE_RANK.get(right, -1) else right


def better_confidence(left: str, right: str) -> str:
    return left if CONFIDENCE_RANK.get(left, -1) >= CONFIDENCE_RANK.get(right, -1) else right


def choose_field(current: str, incoming: str, confidence_changed: bool) -> str:
    if confidence_changed and incoming:
        return incoming
    return current or incoming


def merge_records(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    current_confidence = current["confidence"]
    best_confidence = better_confidence(current["confidence"], incoming["confidence"])
    confidence_changed = best_confidence != current_confidence

    source_urls = split_source_urls(current.get("source_url", "")) + split_source_urls(incoming.get("source_url", ""))
    source_urls = list(dict.fromkeys(source_urls))

    merged["source_url"] = join_source_urls(source_urls)
    merged["source_type"] = better_source_type(current["source_type"], incoming["source_type"])
    merged["confidence"] = best_confidence
    merged["pool_name"] = choose_field(current["pool_name"], incoming["pool_name"], confidence_changed)
    merged["algorithm"] = choose_field(current["algorithm"], incoming["algorithm"], confidence_changed)
    merged["region"] = choose_field(current["region"], incoming["region"], confidence_changed)
    merged["first_seen"] = min(current["first_seen"], incoming["first_seen"])
    merged["last_seen"] = max(current["last_seen"], incoming["last_seen"])

    notes = [current.get("notes", "").strip(), incoming.get("notes", "").strip()]
    merged["notes"] = " | ".join(dict.fromkeys(note for note in notes if note))[:900]

    if should_stay_candidate(merged):
        demote_to_candidate(merged)
    elif merged["confidence"] == "candidate" and len(source_urls) >= 2:
        merged["confidence"] = "probable"
        merged["status"] = "active"
    elif merged["confidence"] in {"confirmed", "probable"}:
        merged["status"] = "active"
    else:
        merged["status"] = "unknown"

    return normalize_record(merged)


def merge_discovered(
    seed_records: list[dict[str, Any]],
    discovered_sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records_by_key: dict[str, dict[str, Any]] = {}
    original_by_key: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    input_counts: dict[str, int] = {}

    for index, seed_record in enumerate(seed_records):
        try:
            normalized = normalize_record(seed_record, index)
        except ValueError as exc:
            skipped.append({"source": "seed", "reason": str(exc)})
            continue
        key = record_key(normalized)
        records_by_key[key] = normalized
        original_by_key[key] = dict(normalized)

    for source in discovered_sources:
        label = source["label"]
        raw_records = source.get("records")
        if raw_records is None:
            raw_records = load_records(Path(source["path"]))
        input_counts[label] = len(raw_records)
        for raw_record in raw_records:
            normalized, error = prepare_record(raw_record, label, source["kind"])
            if error or normalized is None:
                skipped.append({
                    "source": label,
                    "domain": str(raw_record.get("domain", "")),
                    "reason": error or "unknown error",
                })
                continue
            key = record_key(normalized)
            if key in records_by_key:
                records_by_key[key] = merge_records(records_by_key[key], normalized)
            else:
                records_by_key[key] = normalized

    library = build_library(list(records_by_key.values()))
    inserted = 0
    updated = 0
    unchanged = 0
    for record in library:
        key = record_key(record)
        if key not in original_by_key:
            inserted += 1
        elif record != original_by_key[key]:
            updated += 1
        else:
            unchanged += 1

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed_records": len(seed_records),
        "input_records": input_counts,
        "output_records": len(library),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": len(skipped),
        "skipped_examples": skipped[:25],
        "confidence_counts": dict(Counter(record["confidence"] for record in library)),
        "status_counts": dict(Counter(record["status"] for record in library)),
    }
    return library, report


def write_outputs(records: list[dict[str, Any]], output: Path, report: dict[str, Any], report_path: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Promote discovered mining-pool endpoints into the stable seed file.")
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--exclude-github", action="store_true", help="Do not merge GitHub code-search candidates.")
    parser.add_argument("--exclude-ip-candidates", action="store_true", help="Do not merge naked IP:port candidates.")
    parser.add_argument("--dry-run", action="store_true", help="Print the promotion report without writing files.")
    return parser


def selected_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source in DISCOVERED_SOURCES:
        if args.exclude_github and source["label"] == "github_code_search":
            continue
        if args.exclude_ip_candidates and source["label"] == "ip_endpoint_candidates":
            continue
        sources.append(source)
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    seed_records = load_records(args.seed)
    records, report = merge_discovered(seed_records, selected_sources(args))
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        write_outputs(records, args.output, report, args.report)
        print(
            "Promoted discovered records: "
            f"{report['inserted']} inserted, {report['updated']} updated, {report['skipped']} skipped."
        )
        print(f"Wrote {args.output} and {args.report}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
