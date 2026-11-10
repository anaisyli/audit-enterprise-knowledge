#!/usr/bin/env python3
"""Read the client's own website (only the given hosts) into page texts.

Writes <out>/sources/site.json and <out>/text/W###.json. robots.txt is respected,
links to other sites are never followed, and the crawl stops at --max-pages.
Pages built by JavaScript come back nearly empty: save them with a browser
(.html, or .txt whose first line is "URL: <address>") and pass --capture <folder>.
Exit code 4 means no page with usable text was read.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import VERSION, key_of, read_text_file, write_json  # noqa: E402
from htmltext import parse_html  # noqa: E402

USER_AGENT = f"Mozilla/5.0 (compatible; audit-enterprise-knowledge/{VERSION}; client website review)"
SKIP_EXT = re.compile(r"(?i)\.(?:jpe?g|png|gif|webp|svg|ico|pdf|zip|rar|7z|mp4|mov|mp3|css|js|json|docx?|xlsx?|pptx?|woff2?|ttf)$")
NOISE = re.compile(r"(?i)/wp-json|/feed/?$|/tag/|/author/|/page/\d|/wp-content/|/wp-admin|xmlrpc|/category/|/\d{4}/\d{2}(?:/\d{2})?/?$|/cart|/checkout|/my-account|/login|elementor-action|\?(?:s|p|replytocom)=")
EMPTY_SHELL_CHARS = 80


def same_site(url: str, hosts: set[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return host in hosts or host.removeprefix("www.") in hosts


def clean(url: str) -> str:
    """Normalize a link the way a browser would: drop tabs/newlines, percent-encode spaces and other unsafe characters."""
    url = re.sub(r"[\x00-\x1f\x7f]", "", url.strip())
    url, _ = urldefrag(url)
    parts = urlparse(url)
    # unquote first so links that are already encoded are not encoded twice
    path = quote(unquote(parts.path or "/"), safe="/:@!$&'()*+,;=-._~")
    query = quote(unquote(parts.query), safe="=&:/@!$'()*+,;-._~?") if parts.query else ""
    return f"{parts.scheme}://{parts.netloc.lower()}{path}" + (f"?{query}" if query else "")


def get(url: str, timeout: int = 20) -> tuple[int, str, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(6_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
        kind = response.headers.get_content_type() or ""
        return response.status, kind, raw.decode(charset, errors="replace")


def sitemap_urls(start: str, robots_text: str, hosts: set[str], limit: int = 2000) -> list[str]:
    root = f"{urlparse(start).scheme}://{urlparse(start).netloc}"
    queue = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_text) or [f"{root}/sitemap.xml", f"{root}/sitemap_index.xml", f"{root}/wp-sitemap.xml"]
    seen, found = set(), []
    while queue and len(seen) < 40 and len(found) < limit:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            _, _, text = get(url)
            tree = ET.fromstring(text.encode("utf-8"))
        except Exception:
            continue
        for loc in tree.iter():
            if not loc.tag.endswith("loc") or not loc.text:
                continue
            value = loc.text.strip()
            if value.endswith(".xml") or "sitemap" in value.rsplit("/", 1)[-1]:
                if "image" not in value and "author" not in value:
                    queue.append(value)
            elif same_site(value, hosts) and not SKIP_EXT.search(urlparse(value).path) and not NOISE.search(value):
                found.append(clean(value))
    return list(dict.fromkeys(found))


def read_page(url: str, robots: RobotFileParser | None, delay: float) -> dict[str, Any]:
    if robots is not None and not robots.can_fetch(USER_AGENT, url):
        return {"url": url, "status": "robots", "error": "robots.txt 不允许读取"}
    time.sleep(delay)
    try:
        status, kind, html = get(url)
    except HTTPError as error:
        return {"url": url, "status": error.code, "error": f"HTTP {error.code}"}
    except (URLError, TimeoutError, OSError) as error:
        return {"url": url, "status": "error", "error": str(getattr(error, "reason", error))[:120]}
    except Exception as error:  # a bad link (e.g. http.client.InvalidURL) must not stop the whole crawl
        return {"url": url, "status": "error", "error": f"{type(error).__name__}: {error}"[:120]}
    if "html" not in kind:
        return {"url": url, "status": status, "error": f"不是网页（{kind}）"}
    page = parse_html(html)
    page.update(url=url, status=status)
    return page


def import_capture(folder: Path) -> list[dict[str, Any]]:
    pages = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm", ".txt", ".md"}:
            continue
        text = read_text_file(path)
        if path.suffix.lower() in {".html", ".htm"}:
            page = parse_html(text)
            canonical = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', text, re.I)
            page["url"] = canonical.group(1) if canonical else f"capture://{path.name}"
        else:
            first, _, rest = text.partition("\n")
            url = first.split(":", 1)[1].strip() if first.lower().startswith("url:") else f"capture://{path.name}"
            body = rest if first.lower().startswith("url:") else text
            blocks = [b.strip() for b in re.split(r"\n\s*\n|\n", body) if b.strip()]
            page = {"title": path.stem, "h1": "", "headings": [], "blocks": blocks, "links": [], "images": 0, "url": url}
        page.update(status="capture")
        pages.append(page)
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description="Read the client's website into page texts.")
    parser.add_argument("--site", nargs="*", default=[], help="官网地址，可以几个")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--delay", type=float, default=0.3, help="每页之间等几秒")
    parser.add_argument("--capture", help="浏览器保存的页面文件夹（JS 页面用）")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    pages: list[dict[str, Any]] = []
    report: dict[str, Any] = {"sites": args.site, "sitemap": 0, "errors": []}
    for start in args.site:
        start = start if re.match(r"(?i)https?://", start) else f"https://{start}"
        host = urlparse(start).netloc.lower()
        hosts = {host, host.removeprefix("www."), f"www.{host.removeprefix('www.')}"}
        robots = RobotFileParser()
        robots_text = ""
        try:
            _, _, robots_text = get(f"{urlparse(start).scheme}://{host}/robots.txt")
            robots.parse(robots_text.splitlines())
        except Exception:
            robots = None
        home = read_page(clean(start), robots, 0)
        if home.get("error"):
            report["errors"].append(f"{start}：{home['error']}")
            pages.append(home)
            continue
        pages.append(home)
        from_home = [clean(urljoin(start, link)) for link in home.get("links", [])]
        mapped = sitemap_urls(start, robots_text, hosts)
        report["sitemap"] += len(mapped)
        queue = [u for u in dict.fromkeys(from_home + mapped)
                 if same_site(u, hosts) and not SKIP_EXT.search(urlparse(u).path) and not NOISE.search(u) and u != clean(start)]
        queue = queue[: max(0, args.max_pages - 1)]
        report.setdefault("skipped_over_limit", 0)
        report["skipped_over_limit"] += max(0, len(set(from_home + mapped)) - len(queue) - 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            pages.extend(pool.map(lambda u: read_page(u, robots, args.delay), queue))
    if args.capture:
        pages.extend(import_capture(Path(args.capture)))

    # Text that repeats on most pages is the site's header, footer or sidebar, not content.
    good = [p for p in pages if not p.get("error")]
    counts = Counter(key_of(b) for p in good for b in set(p.get("blocks", [])))
    threshold = max(3, int(len(good) * 0.5)) if len(good) >= 4 else 10**9
    rows = []
    for number, page in enumerate(pages, 1):
        source_id = f"W{number:03d}"
        row: dict[str, Any] = {"id": source_id, "kind": "web", "label": page["url"], "url": page["url"], "type": "官网页面",
                               "title": page.get("title", ""), "h1": page.get("h1", ""), "headings": page.get("headings", [])[:40],
                               "images": page.get("images", 0)}
        if page.get("error"):
            row.update(judge="不能", reason=f"没读到：{page['error']}", chars=0)
            rows.append(row)
            continue
        blocks = [b for b in page.get("blocks", []) if counts[key_of(b)] < threshold]
        blocks = list(dict.fromkeys(blocks))
        chars = sum(len(b) for b in blocks)
        row["chars"] = chars
        if chars < EMPTY_SHELL_CHARS:
            row.update(judge="不能", reason="页面几乎没有正文：可能是 JS 页面、空白页或测试页，要用浏览器看一下")
        else:
            row["judge"] = "能"
        rows.append(row)
        write_json(out / "text" / f"{source_id}.json", {"id": source_id, "units": [{"loc": "网页", "text": "\n".join(blocks)}]})
    write_json(out / "sources" / "site.json", {"report": report, "pages": rows})
    usable = sum(1 for r in rows if r["judge"] == "能")
    print(f"官网：读了 {len(rows)} 页，有正文 {usable} 页；sitemap 里有 {report['sitemap']} 个地址。")
    for error in report["errors"]:
        print("  " + error)
    if args.site and usable == 0:
        print("一页可用正文都没读到。先检查网址和网络，或者用浏览器保存页面后用 --capture 导入。", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
