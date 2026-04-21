import unittest
from unittest.mock import patch

from scripts.discover_from_pool_sites import (
    discover_sites,
    extract_blockchain_node_candidates,
    extract_site_endpoints,
    likely_help_link,
    node_endpoint_keys,
    page_urls_for_site,
    worker_count,
)


class DiscoverFromPoolSitesTests(unittest.TestCase):
    def test_page_urls_selects_same_domain_help_links(self):
        site = {"website_url": "https://pool.example/", "website_domain": "pool.example"}
        html = '<a href="/help/mining">Mining help</a><a href="https://external.example/help">External</a>'
        self.assertEqual(
            page_urls_for_site(site, html, 4),
            ["https://pool.example/", "https://pool.example/help/mining"],
        )

    def test_likely_help_link_rejects_cross_domain(self):
        self.assertFalse(
            likely_help_link({"url": "https://external.example/mining", "text": "mining"}, "pool.example")
        )

    def test_extract_site_endpoints_accepts_full_stratum_and_same_site_host_port(self):
        site = {"pool_name": "Example", "website_domain": "pool.example"}
        text = "Use stratum+ssl://stratum.other.example:1443, pool.example:3333, or 192.0.2.10:4444."
        endpoints = extract_site_endpoints(text, site)
        self.assertEqual(
            endpoints,
            [
                {"domain": "192.0.2.10", "port": 4444, "scheme": "stratum+tcp"},
                {"domain": "pool.example", "port": 3333, "scheme": "stratum+tcp"},
                {"domain": "stratum.other.example", "port": 1443, "scheme": "stratum+ssl"},
            ],
        )

    def test_extract_site_endpoints_skips_invalid_zero_ports(self):
        site = {"pool_name": "Example", "website_domain": "pool.example"}
        text = (
            "Bad ports: stratum+tcp://bad.pool.example:0, "
            "pool.example:0000, 192.0.2.10:0. "
            "Good port: pool.example:3333."
        )
        endpoints = extract_site_endpoints(text, site)
        self.assertEqual(endpoints, [{"domain": "pool.example", "port": 3333, "scheme": "stratum+tcp"}])

    def test_addnode_ip_port_is_split_from_pool_endpoints(self):
        site = {"pool_name": "Example", "website_domain": "pool.example"}
        text = "Wallet peers: addnode=192.0.2.10:8333 seednode 192.0.2.11:9333. Pool backup 192.0.2.12:4444."
        nodes = extract_blockchain_node_candidates(text, site, "https://pool.example/help", "2026-04-20")
        endpoints = extract_site_endpoints(text, site, node_endpoint_keys(nodes))

        self.assertEqual(
            nodes,
            [
                {
                    "node_host": "192.0.2.10",
                    "port": 8333,
                    "node_type": "addnode",
                    "pool_name": "Example",
                    "coin": "BTC",
                    "source_url": "https://pool.example/help",
                    "first_seen": "2026-04-20",
                    "last_seen": "2026-04-20",
                    "notes": (
                        "Blockchain peer node candidate extracted from addnode directive while "
                        "crawling pool.example; not a mining pool Stratum endpoint."
                    ),
                },
                {
                    "node_host": "192.0.2.11",
                    "port": 9333,
                    "node_type": "seednode",
                    "pool_name": "Example",
                    "coin": "LTC",
                    "source_url": "https://pool.example/help",
                    "first_seen": "2026-04-20",
                    "last_seen": "2026-04-20",
                    "notes": (
                        "Blockchain peer node candidate extracted from seednode directive while "
                        "crawling pool.example; not a mining pool Stratum endpoint."
                    ),
                },
            ],
        )
        self.assertEqual(endpoints, [{"domain": "192.0.2.12", "port": 4444, "scheme": "stratum+tcp"}])

    def test_worker_count_is_bounded_by_site_count(self):
        self.assertEqual(worker_count(10, 3), 3)
        self.assertEqual(worker_count(0, 3), 1)
        self.assertEqual(worker_count(4, 0), 1)

    def test_discover_sites_preserves_input_order_with_workers(self):
        sites = [
            {"pool_name": "A", "website_url": "https://a.example/", "website_domain": "a.example"},
            {"pool_name": "B", "website_url": "https://b.example/", "website_domain": "b.example"},
        ]

        def fake_discover(site, max_pages, timeout, page_delay_seconds):
            return (
                [{"domain": site["website_domain"], "pool_name": site["pool_name"]}],
                [],
                [{"pool_name": site["pool_name"], "url": site["website_url"], "ok": True}],
            )

        with patch("scripts.discover_from_pool_sites.discover_site", side_effect=fake_discover):
            records, nodes, reports = discover_sites(sites, 4, 10, 0.0, 0.0, 2)

        self.assertEqual([record["pool_name"] for record in records], ["A", "B"])
        self.assertEqual(nodes, [])
        self.assertEqual([report["pool_name"] for report in reports], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
