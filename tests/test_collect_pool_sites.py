import unittest

from scripts.collect_pool_sites import (
    canonical_domain,
    extract_minerstat_profiles,
    extract_miningpoolstats_coin_urls,
    extract_official_site,
    is_ip_endpoint_url,
    miningpoolstats_data_url,
    worker_count,
)


class CollectPoolSitesTests(unittest.TestCase):
    def test_extracts_minerstat_pool_profiles(self):
        source = {"url": "https://minerstat.com/pools", "base_url": "https://minerstat.com"}
        html = '<a href="/pools/f2pool">F2Pool</a><a href="/coins/btc">BTC</a>'
        profiles = extract_minerstat_profiles(html, source)
        self.assertEqual(
            profiles,
            [{"pool_name": "F2Pool", "profile_url": "https://minerstat.com/pools/f2pool", "slug": "f2pool"}],
        )

    def test_extracts_website_link_from_profile(self):
        html = '<h2>Connect</h2><a href="https://f2pool.com/">F2Pool website</a><a href="https://x.com/f2pool">X</a>'
        self.assertEqual(
            extract_official_site(html, "https://minerstat.com/pools/f2pool", "F2Pool"),
            ("https://f2pool.com/", "f2pool.com"),
        )

    def test_canonical_domain_removes_www(self):
        self.assertEqual(canonical_domain("https://www.example.com/path"), "example.com")
        self.assertEqual(canonical_domain("https://www.example.com:8443/path"), "example.com")

    def test_detects_ip_endpoint_urls(self):
        self.assertTrue(is_ip_endpoint_url("http://192.0.2.10:3333"))
        self.assertFalse(is_ip_endpoint_url("https://example.com:3333"))

    def test_extracts_miningpoolstats_coin_urls_from_sitemap(self):
        source = {"base_url": "https://miningpoolstats.stream"}
        sitemap = (
            "<urlset>"
            "<url><loc>https://miningpoolstats.stream</loc></url>"
            "<url><loc>https://miningpoolstats.stream/bitcoin</loc></url>"
            "<url><loc>https://miningpoolstats.stream/litecoin</loc></url>"
            "<url><loc>https://example.com/not-owned</loc></url>"
            "</urlset>"
        )
        self.assertEqual(
            extract_miningpoolstats_coin_urls(sitemap, source),
            ["https://miningpoolstats.stream/bitcoin", "https://miningpoolstats.stream/litecoin"],
        )

    def test_extracts_miningpoolstats_data_preload_url(self):
        html = '<link rel="preload" href="https://data.miningpoolstats.stream/data/bitcoin.js?t=123" as="fetch">'
        self.assertEqual(
            miningpoolstats_data_url(html),
            "https://data.miningpoolstats.stream/data/bitcoin.js?t=123",
        )

    def test_worker_count_is_bounded_by_work_items(self):
        self.assertEqual(worker_count(10, 2), 2)
        self.assertEqual(worker_count(0, 2), 1)
        self.assertEqual(worker_count(4, 0), 1)


if __name__ == "__main__":
    unittest.main()
