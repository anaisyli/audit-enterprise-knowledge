"""Scanned PDF pages are OCR'd locally before anything is called unreadable."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ocr  # noqa: E402

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None


def picture_pdf(path: Path, lines: list[str], font: str) -> None:
    """A page that is only a picture of text, like a scan."""
    source = fitz.open()
    page = source.new_page(width=595, height=842)
    for number, line in enumerate(lines):
        page.insert_text((60, 90 + number * 30), line, fontsize=16, fontname=font)
    picture = page.get_pixmap(dpi=150).tobytes("png")
    scan = fitz.open()
    scan.new_page(width=595, height=842).insert_image(fitz.Rect(0, 0, 595, 842), stream=picture)
    photo = scan.new_page(width=595, height=842)
    photo.draw_rect(fitz.Rect(50, 50, 545, 792), fill=(0.8, 0.8, 0.8))  # a photo with no words
    scan.save(str(path))


class CleanTest(unittest.TestCase):
    def test_chinese_spacing_is_joined(self) -> None:
        self.assertEqual(ocr.clean("公司成立于 2 0 0 8 年，库存 8 0 ， 0 0 0 吨"), "公司成立于2008年，库存80,000吨")

    def test_english_spacing_is_kept(self) -> None:
        self.assertEqual(ocr.clean("OD 21.3 to 610 mm"), "OD 21.3 to 610 mm")

    def test_split_codes_are_joined_in_chinese_lines(self) -> None:
        self.assertEqual(ocr.clean("证书编号：12345 Q 6789"), "证书编号：12345Q6789")
        self.assertEqual(ocr.clean("通过 ISO 9001 认证"), "通过ISO 9001认证")


@unittest.skipIf(fitz is None or not ocr.engines(), "no PyMuPDF or no OCR engine on this computer")
class ScannedPdfTest(unittest.TestCase):
    def test_scan_is_read_and_photo_page_stays_unreadable(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="audit-ocr-test-"))
        files = tmp / "资料"
        files.mkdir()
        picture_pdf(files / "扫描件.pdf", ["示例公司 公司简介", "公司成立于2008年，总部位于北京。", "我们运营五个仓库。"], "china-s")
        out = tmp / "run"
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        done = subprocess.run([sys.executable, str(SCRIPTS / "scan_files.py"), "--files", str(files), "--out", str(out)],
                              capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        row = json.loads((out / "sources" / "files.json").read_text(encoding="utf-8"))["files"][0]
        self.assertEqual(row["judge"], "部分", row)  # text page read by OCR, photo page not
        self.assertEqual(row["ocr_pages"], 1)
        self.assertIn("仍没有文字", row["reason"])
        text = json.loads((out / "text" / "D001.json").read_text(encoding="utf-8"))["units"][0]["text"]
        self.assertIn("2008", text)
        analyzed = subprocess.run([sys.executable, str(SCRIPTS / "analyze.py"), "--out", str(out)],
                                  capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(analyzed.returncode, 0, analyzed.stderr)
        kinds = {f["kind"] for f in json.loads((out / "findings.json").read_text(encoding="utf-8"))["flags"]}
        self.assertIn("ocr", kinds)
        self.assertIn("partial", kinds)

    def test_images_are_read_and_photos_are_reported(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="audit-ocr-test-"))
        files = tmp / "资料"
        files.mkdir()
        page = fitz.open().new_page(width=600, height=300)
        for number, line in enumerate(["质量管理体系认证证书", "认证范围：产品的销售和仓储服务", "有效期至：2028年5月31日"]):
            page.insert_text((40, 60 + number * 34), line, fontsize=18, fontname="china-s")
        page.get_pixmap(dpi=150).save(str(files / "证书.png"))
        photo = fitz.open().new_page(width=600, height=300)
        photo.draw_rect(fitz.Rect(20, 20, 580, 280), fill=(0.3, 0.5, 0.7))
        photo.get_pixmap(dpi=100).save(str(files / "仓库.png"))
        out = tmp / "run"
        done = subprocess.run([sys.executable, str(SCRIPTS / "scan_files.py"), "--files", str(files), "--out", str(out)],
                              capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = {Path(r["label"]).name: r for r in json.loads((out / "sources" / "files.json").read_text(encoding="utf-8"))["files"]}
        self.assertEqual(rows["证书.png"]["judge"], "能", rows["证书.png"])
        self.assertEqual(rows["仓库.png"]["judge"], "不能")
        self.assertIn("照片", rows["仓库.png"]["reason"])
        text = json.loads((out / "text" / f"{rows['证书.png']['id']}.json").read_text(encoding="utf-8"))["units"][0]["text"]
        self.assertIn("2028", text)

    def test_no_ocr_flag_reports_why(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="audit-ocr-test-"))
        files = tmp / "资料"
        files.mkdir()
        picture_pdf(files / "扫描件.pdf", ["Company profile", "Founded in 2008."], "helv")
        out = tmp / "run"
        subprocess.run([sys.executable, str(SCRIPTS / "scan_files.py"), "--files", str(files), "--out", str(out), "--no-ocr"],
                       capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        row = json.loads((out / "sources" / "files.json").read_text(encoding="utf-8"))["files"][0]
        self.assertEqual(row["judge"], "不能")
        self.assertIn("--no-ocr", row["reason"])


if __name__ == "__main__":
    unittest.main()
