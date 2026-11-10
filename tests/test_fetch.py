"""Bad links on a client site must not stop the crawl."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_site  # noqa: E402


class CleanUrlTest(unittest.TestCase):
    def test_spaces_are_encoded(self) -> None:
        url = "https://www.example.com/products/category-a/Product Name ABC-123/item.html"
        self.assertEqual(fetch_site.clean(url), "https://www.example.com/products/category-a/Product%20Name%20ABC-123/item.html")

    def test_already_encoded_is_not_encoded_twice(self) -> None:
        url = "https://www.example.com/a%20b/c.html"
        self.assertEqual(fetch_site.clean(url), url)

    def test_tabs_newlines_and_fragments_are_dropped(self) -> None:
        self.assertEqual(fetch_site.clean(" https://www.example.com/a\n/b\t.html#top "), "https://www.example.com/a/b.html")

    def test_chinese_path_and_query(self) -> None:
        self.assertEqual(fetch_site.clean("https://www.example.com/产品/列表?q=a b&page=2"),
                         "https://www.example.com/%E4%BA%A7%E5%93%81/%E5%88%97%E8%A1%A8?q=a%20b&page=2")


class ReadPageTest(unittest.TestCase):
    def test_invalid_url_is_reported_not_raised(self) -> None:
        page = fetch_site.read_page("http://127.0.0.1:9/a b/c.html", None, 0)  # raw space -> http.client.InvalidURL
        self.assertIn("error", page)

    def test_unreachable_page_is_reported_not_raised(self) -> None:
        page = fetch_site.read_page(fetch_site.clean("http://127.0.0.1:9/a b/c.html"), None, 0)
        self.assertIn("error", page)


if __name__ == "__main__":
    unittest.main()
