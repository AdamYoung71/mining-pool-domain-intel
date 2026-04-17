import unittest

from scripts.discover_from_pool_sites import extract_site_endpoints, likely_help_link, page_urls_for_site


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


if __name__ == "__main__":
    unittest.main()
