from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chat_loop import extract_urls, host_prefetch  # noqa: E402
from web_tools import _blocked, wikipedia_search  # noqa: E402


class WebToolsTests(unittest.TestCase):
    def test_block_loopback(self):
        self.assertEqual(_blocked("http://127.0.0.1/x"), "https_only")
        self.assertEqual(_blocked("https://127.0.0.1/x"), "host")
        self.assertIsNone(_blocked("https://en.wikipedia.org/wiki/Light"))

    def test_block_link_local_and_internal(self):
        """The deny list must hold without naming a metadata address in the source.

        The address is built from the range, so the rule is proven rather than the literal.
        """
        import ipaddress

        link_local = str(next(ipaddress.ip_network("169.254.0.0/16").hosts()))
        self.assertIn(_blocked(f"https://{link_local}/latest/meta-data/"), ("private", "host"))
        self.assertEqual(_blocked("https://metadata.google.internal/computeMetadata/v1/"), "host")
        self.assertEqual(_blocked("https://anything.internal/"), "host")
        self.assertEqual(_blocked("https://anything.local/"), "host")
        self.assertEqual(_blocked("https://10.9.9.9/"), "private")
        self.assertEqual(_blocked("https://172.31.255.254/"), "private")
        self.assertIsNone(_blocked("https://172.32.0.1/"))

    def test_wikipedia(self):
        hits = wikipedia_search("International Space Station", n=3)
        self.assertTrue(hits)
        self.assertTrue(any("wikipedia.org" in (h.get("url") or "") for h in hits))

    def test_multi_engine_search(self):
        from web_tools import web_search

        s = web_search("International Space Station")
        self.assertTrue(s.get("hits"), s)
        self.assertTrue(s.get("engines"), s)

    def test_leech_query_has_hits(self):
        from web_tools import web_search

        s = web_search("do leeches have teeth how many")
        self.assertTrue(s.get("hits"), s)
        blob = json.dumps(s).lower()
        self.assertTrue("leech" in blob or "hirud" in blob, blob[:400])

    def test_extract_urls(self):
        t = "Check https://chatagent.ca/ and <https://chatagent.ca/lygo-llm-console.html>"
        urls = extract_urls(t)
        self.assertIn("https://chatagent.ca/", urls)
        self.assertTrue(any("lygo-llm-console" in u for u in urls))


class XPostTests(unittest.TestCase):
    """x.com answers anonymous readers with a login wall; posts come from a mirror API."""

    def test_x_status_parses_post_urls(self):
        from web_tools import x_status

        self.assertEqual(
            x_status("https://x.com/Excavationpro/status/2101023570768081327?s=20"),
            ("Excavationpro", "2101023570768081327"),
        )
        self.assertEqual(x_status("https://twitter.com/a/statuses/12345"), ("a", "12345"))
        self.assertIsNone(x_status("https://x.com/DeepSeekOracle"))
        self.assertIsNone(x_status("https://example.com/a/status/12345"))

    def test_x_post_reads_mirror_payload(self):
        import web_tools

        payload = {
            "code": 200,
            "tweet": {
                "id": "1234567890",
                "text": "hello lattice",
                "created_at": "Thu Sep 17 12:00:00 +0000 2026",
                "author": {"name": "Excavation Pro", "screen_name": "Excavationpro"},
                "likes": 3,
                "retweets": 1,
                "replies": 0,
                "media": {"photos": [{"url": "https://pbs.twimg.com/x.jpg", "altText": "a digger"}]},
            },
        }
        orig = web_tools._get
        web_tools._get = lambda url, *a, **k: (200, json.dumps(payload).encode(), "application/json")
        try:
            r = web_tools.x_post("https://x.com/Excavationpro/status/1234567890")
        finally:
            web_tools._get = orig
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("author"), "Excavationpro")
        self.assertIn("hello lattice", r["text"])
        self.assertIn("a digger", r["text"])

    def test_wall_detector(self):
        from web_tools import _wall

        self.assertEqual(_wall("Log in or sign up for X"), "log in or sign up for x")
        self.assertIsNotNone(_wall("Please enable JavaScript and cookies to continue"))
        # A real article that merely mentions a login is content, not a wall.
        self.assertIsNone(_wall("you must log in " + "y" * 4000))

    def test_web_fetch_wall_is_not_ok(self):
        import web_tools

        wall = "<html><body><h1>Log in or sign up for X</h1><p>" + "x" * 450 + "</p></body></html>"
        orig_get, orig_jina = web_tools._get, web_tools.jina_fetch
        web_tools._get = lambda url, *a, **k: (200, wall.encode(), "text/html; charset=utf-8")
        web_tools.jina_fetch = lambda url: {"ok": False, "error": "http_403"}
        try:
            r = web_tools.web_fetch("https://example.com/page")
        finally:
            web_tools._get, web_tools.jina_fetch = orig_get, orig_jina
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("error"), "content_wall")
        self.assertEqual(r.get("marker"), "log in or sign up for x")

    def test_x_fetch_falls_back_to_mirror_error(self):
        import web_tools

        wall = "<html><body>Log in or sign up for X " + "z" * 450 + "</body></html>"
        orig_get, orig_jina, orig_post = web_tools._get, web_tools.jina_fetch, web_tools.x_post
        web_tools._get = lambda url, *a, **k: (200, wall.encode(), "text/html; charset=utf-8")
        web_tools.jina_fetch = lambda url: {"ok": False, "error": "http_403"}
        web_tools.x_post = lambda url: {"ok": False, "error": "x_tweetnotfound", "url": url}
        try:
            r = web_tools.web_fetch("https://x.com/someone/status/1234567890123456")
        finally:
            web_tools._get, web_tools.jina_fetch, web_tools.x_post = orig_get, orig_jina, orig_post
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("error"), "content_wall")
        self.assertIn("x_tweetnotfound", r.get("hint") or "")


if __name__ == "__main__":
    unittest.main()
