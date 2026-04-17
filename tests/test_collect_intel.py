import unittest

from scripts.collect_intel import extract_endpoints, html_to_text, infer_coin_algorithm, unique_records


class CollectIntelTests(unittest.TestCase):
    def test_extracts_stratum_urls_and_host_port_rows(self):
        source = {
            "id": "fixture",
            "name": "Fixture",
            "url": "https://example.test",
            "source_type": "official",
            "confidence": "confirmed",
            "pool_name": "2Miners",
            "allowed_domain_suffixes": ["2miners.com"],
            "method": "html_stratum_extract",
        }
        text = """
        URL: stratum+tcp://erg.2miners.com:8888
        SSL table: us-erg.2miners.com:18888
        Ignore: unrelated.example.com:443
        """
        endpoints = extract_endpoints(text, source)
        self.assertEqual(
            endpoints,
            [
                {"domain": "erg.2miners.com", "port": 8888, "scheme": "stratum+tcp"},
                {"domain": "us-erg.2miners.com", "port": 18888, "scheme": "stratum+ssl"},
            ],
        )

    def test_infers_coin_and_algorithm_from_hostname(self):
        coin, algorithm = infer_coin_algorithm("btc-asia.f2pool.com", 1314, {})
        self.assertEqual((coin, algorithm), ("BTC", "SHA-256"))

    def test_infers_viabtc_coin_from_shared_mining_host_port(self):
        source = {"pool_name": "ViaBTC"}
        coin, algorithm = infer_coin_algorithm("mining.viabtc.io", 3010, source)
        self.assertEqual((coin, algorithm), ("ETC", "Etchash"))

    def test_html_to_text_removes_tags_and_scripts(self):
        text = html_to_text(b"<html><script>x()</script><body>A&nbsp;<b>B</b></body></html>")
        self.assertIn("A B", text)
        self.assertNotIn("x()", text)

    def test_unique_records_merges_source_urls(self):
        first = {
            "domain": "btc.pool.test",
            "port": 3333,
            "scheme": "stratum+tcp",
            "coin": "BTC",
            "confidence": "confirmed",
            "source_url": "https://one.example",
        }
        second = dict(first, source_url="https://two.example")
        merged = unique_records([first, second])
        self.assertEqual(len(merged), 1)
        self.assertIn("https://one.example; https://two.example", merged[0]["source_url"])


if __name__ == "__main__":
    unittest.main()
