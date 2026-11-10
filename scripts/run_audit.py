#!/usr/bin/env python3
"""One command for the machine part: list local files, read the website, run the first judgement.

  python scripts/run_audit.py --files <folder> [<folder> ...] --site <url> --out <run dir> [--config audit-config.json]

Then the AI reads <out>/review-input.md, fills <out>/review.json, and runs render_report.py.
Re-running is safe: review.json is never overwritten.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402,F401  (sets UTF-8 console output)


def run(script: str, *arguments: str) -> int:
    print(f"\n== {script} ==", flush=True)
    return subprocess.call([sys.executable, str(HERE / script), *arguments], env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory, website reading and first judgement in one go.")
    parser.add_argument("--files", nargs="*", default=[], help="本地文件夹或文件")
    parser.add_argument("--site", nargs="*", default=[], help="官网地址")
    parser.add_argument("--capture", help="浏览器保存的官网页面文件夹")
    parser.add_argument("--out", required=True, help="运行目录，放在资料文件夹外面")
    parser.add_argument("--config", help="audit-config.json")
    parser.add_argument("--max-pages", default="80")
    parser.add_argument("--ocr-max-pages", default="300", help="本次最多 OCR 多少页")
    parser.add_argument("--no-ocr", action="store_true", help="不做 OCR")
    args = parser.parse_args()
    if not args.files and not args.site and not args.capture:
        parser.error("至少给 --files、--site 或 --capture 之一")
    out = Path(args.out).resolve()
    for given in args.files:
        if out.is_relative_to(Path(given).resolve()):
            parser.error("运行目录不要放在资料文件夹里面，否则下次会把结果当资料读")
    config = ["--config", args.config] if args.config else []
    if args.files:
        ocr = ["--no-ocr"] if args.no_ocr else ["--ocr-max-pages", args.ocr_max_pages]
        code = run("scan_files.py", "--files", *args.files, "--out", str(out), *ocr)
        if code:
            return code
    if args.site or args.capture:
        site_args = ["--out", str(out), "--max-pages", args.max_pages]
        if args.site:
            site_args += ["--site", *args.site]
        if args.capture:
            site_args += ["--capture", args.capture]
        code = run("fetch_site.py", *site_args)
        if code == 4:
            print("官网没读到正文，先别出报告：检查网址，或用浏览器保存页面后加 --capture 重跑。", file=sys.stderr)
            return 4
        if code:
            return code
    return run("analyze.py", "--out", str(out), *config)


if __name__ == "__main__":
    sys.exit(main())
