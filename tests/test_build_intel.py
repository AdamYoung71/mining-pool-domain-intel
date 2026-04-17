import unittest

from scripts.build_intel import (
    FIELDS,
    build_library,
    build_watchlist,
    count_source_urls,
    parse_endpoint,
)


BASE_RECORD = {
    "domain": "stratum+tcp://Example.Pool.test:3333/path?user=ignored",
    "port": "",
    "scheme": "",
    "pool_name": "ExamplePool",
    "coin": "TST",
    "algorithm": "TestHash",
    "region": "Global",
    "source_type": "official",
    "source_url": "https://example.test/pool-guide",
    "confidence": "confirmed",
    "status": "active",
    "first_seen": "2026-04-17",
    "last_seen": "2026-04-17",
    "notes": "Fixture record.",
}


class BuildIntelTests(unittest.TestCase):
    def test_parse_endpoint_normalizes_scheme_domain_path_and_port(self):
        self.assertEqual(
            parse_endpoint("stratum+ssl://USER@Pool.Example.COM:1443/path"),
            {"domain": "pool.example.com", "port": 1443, "scheme": "stratum+ssl"},
        )

    def test_build_library_emits_fixed_fields_and_normalized_values(self):
        record = build_library([BASE_RECORD])[0]
        self.assertEqual(list(record.keys()), FIELDS)
        self.assertEqual(record["domain"], "example.pool.test")
        self.assertEqual(record["port"], 3333)
        self.assertEqual(record["scheme"], "stratum+tcp")

    def test_build_library_accepts_ip_literal_endpoints(self):
        record = build_library([dict(BASE_RECORD, domain="stratum+tcp://192.0.2.10:3333")])[0]
        self.assertEqual(record["domain"], "192.0.2.10")
        self.assertEqual(record["port"], 3333)

    def test_build_library_rejects_duplicate_domain_port_scheme_and_coin(self):
        with self.assertRaisesRegex(ValueError, "Duplicate record"):
            build_library([BASE_RECORD, dict(BASE_RECORD)])

    def test_confirmed_records_must_come_from_official_sources(self):
        record = dict(BASE_RECORD, source_type="aggregator")
        with self.assertRaisesRegex(ValueError, "confirmed records must use source_type=official"):
            build_library([record])

    def test_probable_records_require_multiple_source_urls(self):
        record = dict(BASE_RECORD, confidence="probable", source_type="aggregator")
        with self.assertRaisesRegex(ValueError, "probable records must include at least two source URLs"):
            build_library([record])
        self.assertEqual(count_source_urls("https://one.example; https://two.example"), 2)

    def test_watchlist_only_contains_active_confirmed_or_probable_records(self):
        records = build_library(
            [
                BASE_RECORD,
                dict(
                    BASE_RECORD,
                    domain="stratum+tcp://candidate.pool.test:3333",
                    confidence="candidate",
                    status="unknown",
                    source_type="open_source",
                    source_url="https://example.test/readme",
                ),
            ]
        )
        self.assertEqual(len(build_watchlist(records)), 1)


if __name__ == "__main__":
    unittest.main()
