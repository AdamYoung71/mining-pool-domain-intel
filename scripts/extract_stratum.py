from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

from build_intel import parse_endpoint

STRATUM_RE = re.compile(
    r"\b(stratum\+(?:tcp|ssl))://(?:[^\s/@]+@)?([a-z0-9.-]+):(\d{1,5})\b",
    flags=re.I,
)


def candidate_from_match(match: re.Match[str], source_file: Path) -> dict[str, object]:
    endpoint = f"{match.group(1)}://{match.group(2)}:{match.group(3)}"
    parsed = parse_endpoint(endpoint)
    today = date.today().isoformat()
    return {
        "domain": parsed["domain"],
        "port": parsed["port"],
        "scheme": parsed["scheme"],
        "pool_name": "UNKNOWN",
        "coin": "UNKNOWN",
        "algorithm": "UNKNOWN",
        "region": "UNKNOWN",
        "source_type": "open_source",
        "source_url": f"file:{source_file.resolve()}",
        "confidence": "candidate",
        "status": "unknown",
        "first_seen": today,
        "last_seen": today,
        "notes": "Extracted from local text; requires manual review before merging.",
    }


def extract_from_file(source_file: Path) -> list[dict[str, object]]:
    text = source_file.read_text(encoding="utf-8")
    records: list[dict[str, object]] = []
    seen: set[tuple[object, object, object]] = set()

    for match in STRATUM_RE.finditer(text):
        candidate = candidate_from_match(match, source_file)
        key = (candidate["domain"], candidate["port"], candidate["scheme"])
        if key not in seen:
            seen.add(key)
            records.append(candidate)

    return records


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python3 scripts/extract_stratum.py <text-file> [text-file...]", file=sys.stderr)
        return 2

    records: list[dict[str, object]] = []
    for arg in args:
        records.extend(extract_from_file(Path(arg)))

    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
