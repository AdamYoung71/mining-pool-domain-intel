import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.update_readme_status import (
    END_MARKER,
    START_MARKER,
    build_status_payload,
    collect_snapshot,
    snapshot_command,
    update_readme,
)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class UpdateReadmeStatusTests(unittest.TestCase):
    def test_collect_snapshot_counts_unique_domains_and_endpoint_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "data/raw/pool_sites.json",
                [
                    {"website_domain": "ExamplePool.com"},
                    {"website_domain": "examplepool.com"},
                    {"website_domain": "other.example"},
                ],
            )
            write_json(
                root / "data/raw/site_discovered_pool_domains.json",
                [
                    {"domain": "btc.examplepool.com", "port": 3333, "scheme": "stratum+tcp", "coin": "BTC"},
                    {"domain": "BTC.ExamplePool.com", "port": 3333, "scheme": "stratum+tcp", "coin": "btc"},
                    {"domain": "192.0.2.10", "port": 4444, "scheme": "stratum+tcp", "coin": "UNKNOWN"},
                ],
            )
            write_json(
                root / "data/raw/ip_pool_endpoint_candidates.json",
                [
                    {"domain": "192.0.2.10", "port": 4444, "scheme": "stratum+tcp", "coin": "UNKNOWN"},
                ],
            )
            write_json(
                root / "data/raw/blockchain_node_candidates.json",
                [
                    {"node_host": "192.0.2.20", "port": 8333, "node_type": "addnode"},
                ],
            )
            write_json(
                root / "data/mining_pool_domains.json",
                [
                    {"domain": "btc.examplepool.com", "port": 3333, "scheme": "stratum+tcp", "coin": "BTC"},
                ],
            )

            snapshot = collect_snapshot(root)

            self.assertEqual(snapshot["pool_site_domains"], ["examplepool.com", "other.example"])
            self.assertEqual(snapshot["endpoint_domains"], ["192.0.2.10", "btc.examplepool.com"])
            self.assertEqual(len(snapshot["endpoint_records"]), 2)
            self.assertEqual(len(snapshot["ip_endpoint_records"]), 1)
            self.assertEqual(len(snapshot["blockchain_nodes"]), 1)
            self.assertEqual(len(snapshot["stable_records"]), 1)

    def test_collect_snapshot_accepts_artifact_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "raw/pool_sites.json", [{"website_domain": "artifact.example"}])
            write_json(
                root / "raw/discovered_pool_domains.json",
                [{"domain": "btc.artifact.example", "port": 3333, "scheme": "stratum+tcp", "coin": "BTC"}],
            )
            write_json(root / "watchlist.json", [])

            snapshot = collect_snapshot(root)

            self.assertEqual(snapshot["pool_site_domains"], ["artifact.example"])
            self.assertEqual(snapshot["endpoint_domains"], ["btc.artifact.example"])

    def test_status_payload_reports_new_counts_against_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "data/raw/pool_sites.json",
                [{"website_domain": "old.example"}, {"website_domain": "new.example"}],
            )
            write_json(
                root / "data/raw/discovered_pool_domains.json",
                [
                    {"domain": "old.example", "port": 3333, "scheme": "stratum+tcp", "coin": "BTC"},
                    {"domain": "new.example", "port": 4444, "scheme": "stratum+tcp", "coin": "BTC"},
                ],
            )
            write_json(
                root / "data/mining_pool_domains.json",
                [
                    {"domain": "old.example", "port": 3333, "scheme": "stratum+tcp", "coin": "BTC"},
                    {"domain": "new.example", "port": 4444, "scheme": "stratum+tcp", "coin": "BTC"},
                ],
            )
            write_json(
                root / "data/raw/blockchain_node_candidates.json",
                [
                    {"node_host": "192.0.2.20", "port": 8333, "node_type": "addnode"},
                    {"node_host": "192.0.2.21", "port": 9333, "node_type": "seednode"},
                ],
            )
            write_json(root / "data/raw/fetch_report.json", [{"ok": True}, {"ok": False, "used_cache": True}])
            args = argparse.Namespace(
                started_at="2026-04-20T01:00:00Z",
                ended_at="2026-04-20T01:02:05Z",
                conclusion="success",
                event="workflow_dispatch",
                branch="main",
                commit="abc123",
                run_id="42",
                run_url="https://github.example/run/42",
                miningpoolstats_coins="80",
                site_limit="60",
                pages_per_site="4",
                run_github="false",
            )
            baseline = {
                "pool_site_domains": ["old.example"],
                "endpoint_domains": ["old.example"],
                "endpoint_records": ["old.example|3333|stratum+tcp|btc"],
                "ip_endpoint_records": [],
                "blockchain_nodes": ["192.0.2.20|8333|addnode"],
                "stable_records": ["old.example|3333|stratum+tcp|btc"],
                "watchlist_records": [],
            }

            status = build_status_payload(root, baseline, args)

            self.assertEqual(status["run"]["duration_seconds"], 125)
            self.assertEqual(status["metrics"]["pool_site_domains"], {"total": 2, "new": 1})
            self.assertEqual(status["metrics"]["endpoint_domains"], {"total": 2, "new": 1})
            self.assertEqual(status["metrics"]["endpoint_records"], {"total": 2, "new": 1})
            self.assertEqual(status["metrics"]["blockchain_nodes"], {"total": 2, "new": 1})
            self.assertEqual(status["metrics"]["stable_records"], {"total": 2, "new": 1})
            self.assertEqual(status["metrics"]["source_reports"]["ok"], 1)
            self.assertEqual(status["metrics"]["source_reports"]["failed"], 1)
            self.assertEqual(status["metrics"]["source_reports"]["used_cache"], 1)
            self.assertIn("snapshot", status)

    def test_snapshot_command_prefers_previous_run_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous_status = root / "data/status/latest_run.json"
            output = root / "baseline.json"
            write_json(
                previous_status,
                {
                    "snapshot": {
                        "pool_site_domains": ["previous.example"],
                        "endpoint_domains": [],
                        "endpoint_records": [],
                        "ip_endpoint_records": [],
                        "blockchain_nodes": [],
                        "stable_records": [],
                        "watchlist_records": [],
                    }
                },
            )
            write_json(root / "data/raw/pool_sites.json", [{"website_domain": "current.example"}])
            args = argparse.Namespace(root=root, output=output, previous_status=previous_status)

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(snapshot_command(args), 0)
            baseline = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(baseline["pool_site_domains"], ["previous.example"])

    def test_update_readme_replaces_status_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                "# Title\n\n## 运行状态\n\n"
                f"{START_MARKER}\nold\n{END_MARKER}\n\n## 快速开始\nbody\n",
                encoding="utf-8",
            )
            status = {
                "run": {
                    "conclusion": "success",
                    "started_at": "2026-04-20T01:00:00Z",
                    "duration": "1秒",
                    "event": "workflow_dispatch",
                    "run_url": "https://github.example/run/42",
                },
                "parameters": {
                    "miningpoolstats_coins": "80",
                    "site_limit": "60",
                    "pages_per_site": "4",
                    "run_github": "false",
                },
                "metrics": {
                    "pool_site_domains": {"total": 2, "new": 1},
                    "endpoint_domains": {"total": 3, "new": 2},
                    "endpoint_records": {"total": 4, "new": 3},
                    "ip_endpoint_records": {"total": 1, "new": 1},
                    "blockchain_nodes": {"total": 2, "new": 1},
                    "stable_records": {"total": 2, "new": 1},
                    "watchlist_records": {"total": 1, "new": 0},
                    "source_reports": {"ok": 1, "failed": 0, "used_cache": 0},
                },
            }

            update_readme(readme, status)
            content = readme.read_text(encoding="utf-8")

            self.assertNotIn("old", content)
            self.assertIn("| Stratum 域名/IP | 总数 3，新增 2 |", content)
            self.assertIn("| 区块链接入节点候选 | 总数 2，新增 1 |", content)
            self.assertIn("| 最终情报库 | 总数 2，新增 1 |", content)
            self.assertEqual(content.count(START_MARKER), 1)
            self.assertIn("## 快速开始", content)


if __name__ == "__main__":
    unittest.main()
