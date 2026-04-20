import unittest

from scripts.promote_discovered import merge_discovered


BASE_RECORD = {
    "domain": "btc.seed.example",
    "port": 3333,
    "scheme": "stratum+tcp",
    "pool_name": "SeedPool",
    "coin": "BTC",
    "algorithm": "SHA-256",
    "region": "Global",
    "source_type": "official",
    "source_url": "https://seed.example/docs",
    "confidence": "confirmed",
    "status": "active",
    "first_seen": "2026-04-17",
    "last_seen": "2026-04-17",
    "notes": "Seed record.",
}


def source(label, kind, records):
    return {"label": label, "kind": kind, "records": records}


class PromoteDiscoveredTests(unittest.TestCase):
    def test_official_discovered_record_is_inserted_as_confirmed(self):
        discovered = dict(
            BASE_RECORD,
            domain="stratum+tcp://btc.official.example:3333/path",
            pool_name="OfficialPool",
            source_url="https://official.example/help",
            notes="Official page.",
        )

        records, report = merge_discovered([], [source("priority_sources", "trusted", [discovered])])
        record = records[0]

        self.assertEqual(report["inserted"], 1)
        self.assertEqual(record["domain"], "btc.official.example")
        self.assertEqual(record["confidence"], "confirmed")
        self.assertEqual(record["status"], "active")

    def test_candidate_sources_do_not_enter_watchlist_shape(self):
        github_record = dict(
            BASE_RECORD,
            domain="github.pool.example",
            pool_name="UNKNOWN",
            source_type="open_source",
            source_url="https://github.com/example/repo/blob/main/config.json",
            confidence="confirmed",
            status="active",
        )
        ip_record = dict(
            BASE_RECORD,
            domain="192.0.2.10",
            port=4444,
            pool_name="IPPool",
            coin="UNKNOWN",
            algorithm="UNKNOWN",
            source_type="aggregator",
            source_url="https://miningpoolstats.stream/example",
            confidence="confirmed",
            status="active",
        )

        records, _ = merge_discovered(
            [],
            [
                source("github_code_search", "candidate", [github_record]),
                source("ip_endpoint_candidates", "candidate", [ip_record]),
            ],
        )

        by_domain = {record["domain"]: record for record in records}
        self.assertEqual(by_domain["github.pool.example"]["confidence"], "candidate")
        self.assertEqual(by_domain["github.pool.example"]["status"], "unknown")
        self.assertEqual(by_domain["192.0.2.10"]["confidence"], "candidate")
        self.assertEqual(by_domain["192.0.2.10"]["status"], "unknown")

    def test_two_candidate_sources_upgrade_to_probable(self):
        first = dict(
            BASE_RECORD,
            domain="multi.pool.example",
            source_type="aggregator",
            source_url="https://aggregator.example/pool",
            confidence="candidate",
            status="unknown",
        )
        second = dict(
            BASE_RECORD,
            domain="multi.pool.example",
            source_type="open_source",
            source_url="https://github.com/example/repo/blob/main/config.json",
            confidence="candidate",
            status="unknown",
        )

        records, _ = merge_discovered(
            [],
            [
                source("priority_sources", "trusted", [first]),
                source("github_code_search", "candidate", [second]),
            ],
        )

        self.assertEqual(records[0]["confidence"], "probable")
        self.assertEqual(records[0]["status"], "active")
        self.assertIn("https://aggregator.example/pool", records[0]["source_url"])
        self.assertIn("https://github.com/example/repo/blob/main/config.json", records[0]["source_url"])

    def test_unknown_coin_or_algorithm_stays_candidate(self):
        unknown = dict(
            BASE_RECORD,
            domain="unknown.pool.example",
            coin="UNKNOWN",
            algorithm="UNKNOWN",
            source_url="https://official.example/unknown",
            confidence="confirmed",
            status="active",
        )

        records, _ = merge_discovered([], [source("official_site_crawl", "trusted", [unknown])])

        self.assertEqual(records[0]["confidence"], "candidate")
        self.assertEqual(records[0]["status"], "unknown")

    def test_existing_confirmed_record_is_not_downgraded_by_candidate(self):
        incoming = dict(
            BASE_RECORD,
            source_type="open_source",
            source_url="https://github.com/example/repo/blob/main/config.json",
            confidence="candidate",
            status="unknown",
            last_seen="2026-04-20",
        )

        records, report = merge_discovered(
            [BASE_RECORD],
            [source("github_code_search", "candidate", [incoming])],
        )
        record = records[0]

        self.assertEqual(report["updated"], 1)
        self.assertEqual(record["confidence"], "confirmed")
        self.assertEqual(record["status"], "active")
        self.assertIn("https://seed.example/docs", record["source_url"])
        self.assertIn("https://github.com/example/repo/blob/main/config.json", record["source_url"])
        self.assertEqual(record["last_seen"], "2026-04-20")


if __name__ == "__main__":
    unittest.main()
