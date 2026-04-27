import unittest
from unittest.mock import patch

from scripts.collect_github_intel import (
    blocked_host,
    endpoint_to_record,
    extract_endpoints_from_text,
    main,
    reports_have_bad_credentials,
    unique_records,
)


class CollectGitHubIntelTests(unittest.TestCase):
    def test_extracts_explicit_stratum_urls(self):
        text = 'pool = "stratum+ssl://xmr.realpool.net:443"'
        self.assertEqual(
            extract_endpoints_from_text(text),
            [{"domain": "xmr.realpool.net", "port": 443, "scheme": "stratum+ssl"}],
        )

    def test_extracts_host_port_only_in_mining_context(self):
        text = "api.example.com:443\nxmrig pool: pool.realpool.net:3333"
        self.assertEqual(
            extract_endpoints_from_text(text),
            [{"domain": "pool.realpool.net", "port": 3333, "scheme": "stratum+tcp"}],
        )

    def test_filters_private_and_placeholder_hosts(self):
        self.assertTrue(blocked_host("127.0.0.1"))
        self.assertTrue(blocked_host("192.168.1.10"))
        self.assertTrue(blocked_host("example.com"))
        self.assertTrue(blocked_host("example.net"))
        self.assertFalse(blocked_host("pool.realpool.net"))

    def test_endpoint_records_are_candidate_open_source(self):
        item = {
            "html_url": "https://github.com/example/repo/blob/main/config.json",
            "path": "config.json",
            "repository": {"full_name": "example/repo"},
        }
        record = endpoint_to_record(
            {"domain": "btc.pool.test", "port": 3333, "scheme": "stratum+tcp"},
            item,
            "fixture",
            "2026-04-17",
        )
        self.assertEqual(record["source_type"], "open_source")
        self.assertEqual(record["confidence"], "candidate")
        self.assertIn("repo=example/repo", record["notes"])

    def test_unique_records_merges_github_source_urls(self):
        first = {"domain": "pool.test", "port": 3333, "scheme": "stratum+tcp", "coin": "UNKNOWN", "source_url": "https://one"}
        second = dict(first, source_url="https://two")
        merged = unique_records([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_url"], "https://one; https://two")

    def test_reports_have_bad_credentials_matches_401(self):
        reports = [{"ok": False, "error": 'HTTP 401: {"message":"Bad credentials"}'}]
        self.assertTrue(reports_have_bad_credentials(reports))

    @patch("scripts.collect_github_intel.write_outputs")
    @patch("scripts.collect_github_intel.load_config", return_value={"auth": {"env": "GITHUB_TOKEN"}})
    def test_main_returns_error_when_token_missing(self, _load_config, _write_outputs):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(main([]), 2)

    @patch("scripts.collect_github_intel.write_outputs")
    @patch("scripts.collect_github_intel.collect_github", return_value=([], [{"ok": False, "error": 'HTTP 401: {"message":"Bad credentials"}'}]))
    @patch("scripts.collect_github_intel.load_config", return_value={"auth": {"env": "GITHUB_TOKEN"}})
    def test_main_returns_error_when_token_rejected(self, _load_config, _collect_github, _write_outputs):
        with patch.dict("os.environ", {"GITHUB_TOKEN": "bad-token"}, clear=True):
            self.assertEqual(main([]), 2)


if __name__ == "__main__":
    unittest.main()
