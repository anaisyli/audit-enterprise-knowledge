"""End-to-end check without network: sample files + saved web pages -> findings -> report."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True, encoding="utf-8",
                          env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "AUDIT_OCR": "off"})  # OCR has its own test


def docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')


PAGE = """<html><head><title>{title}</title></head><body>
<div data-elementor-type="header"><a href="/faq/">FAQ</a> Menu text repeated everywhere</div>
<main><h1>{title}</h1>{body}</main><footer>Footer text repeated everywhere</footer></body></html>"""


class AuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="audit-test-"))
        files = cls.tmp / "资料"
        files.mkdir()
        docx(files / "公司简介.docx", ["关于我们：示例公司成立于2008年，总部位于北京，是一家贸易公司。",
                                     "公司运营五个仓库，常备库存超过80,000吨，产品出口到60个国家。"])
        (files / "证书.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 500)
        (files / "工厂.mp4").write_bytes(b"0" * 500)
        (files / "邮件.txt").write_text("报价 USD 560 per ton, 付款方式 T/T, 联系 a@b.com\n", encoding="utf-8")
        capture = cls.tmp / "capture"
        capture.mkdir()
        (capture / "about.html").write_text(PAGE.format(title="About Us", body="<p>We operate four modern warehouses and export to 100 countries.</p><p>Our strategic partners include Alpha Works and Beta Supply.</p>"), encoding="utf-8")
        (capture / "alpha.html").write_text(PAGE.format(title="Alpha Widget Supplier", body="<h2>Beta Widget Sizes Available</h2><p>Alpha widgets for many uses, supplied from stock in many sizes and finishes for general projects.</p>"), encoding="utf-8")
        (capture / "beta.html").write_text(PAGE.format(title="Beta Widget Supplier", body="<p>Beta widgets in small and large versions for frames, fittings and general assembly, in two standard grades.</p>"), encoding="utf-8")
        (capture / "faq.html").write_text(PAGE.format(title="FAQ", body="<p>What is the lead time?</p><p>Stock items usually ship within a few days after the order is confirmed by both sides.</p>"), encoding="utf-8")
        cls.out = cls.tmp / "run"
        cls.scan = run("scan_files.py", "--files", str(files), "--out", str(cls.out))
        cls.site = run("fetch_site.py", "--out", str(cls.out), "--capture", str(capture))
        cls.analyze = run("analyze.py", "--out", str(cls.out))
        cls.findings = json.loads((cls.out / "findings.json").read_text(encoding="utf-8"))

    def test_steps_succeed(self) -> None:
        for step in (self.scan, self.site, self.analyze):
            self.assertEqual(step.returncode, 0, step.stderr)

    def test_unreadable_files_go_to_mat(self) -> None:
        mat = [f for f in self.findings["flags"] if f["ledger"] == "MAT"]
        titles = " ".join(f["title"] for f in mat)
        self.assertIn("图片", titles)  # with OCR off: "图片里的字没法识别"
        self.assertIn("视频", titles)
        self.assertTrue(all(f["fixed"] for f in mat))

    def test_fact_conflicts_and_site_checks(self) -> None:
        kinds = {f["kind"]: f for f in self.findings["flags"]}
        self.assertIn("fact_conflict", kinds)
        warehouse = [f for f in self.findings["flags"] if f["kind"] == "fact_conflict" and "仓库" in f["title"]]
        self.assertTrue(warehouse, "4 vs 5 warehouses should be a conflict")
        self.assertIn("copy_paste", kinds)
        self.assertIn("brand", kinds)

    def test_header_and_footer_are_not_content(self) -> None:
        text = json.loads((self.out / "text" / "W001.json").read_text(encoding="utf-8"))["units"][0]["text"]
        self.assertNotIn("Menu text", text)
        self.assertNotIn("Footer text", text)

    def test_blank_review_is_refused_then_filled_review_renders(self) -> None:
        blank = run("render_report.py", "--out", str(self.out))
        self.assertEqual(blank.returncode, 1)
        self.assertIn("summary", blank.stderr)
        review = json.loads((self.out / "review.json").read_text(encoding="utf-8"))
        review["summary"] = "测试结论。"
        for row in review["sections"].values():
            row["note"] = "测试现状。"
        for flag_id, decision in review["flags"].items():
            decision["decision"] = "keep"
        review["priorities"] = [{"title": "先补案例", "detail": "测试。"}]
        review["items"] = [{"ledger": "CUS", "title": "价格是 USD 560 吗", "detail": "测试", "sources": ["D001"]}]
        (self.out / "review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        refused = run("render_report.py", "--out", str(self.out))
        self.assertEqual(refused.returncode, 1)
        self.assertIn("金额", refused.stderr)
        review["items"][0]["title"] = "报价单能否作为参考"
        (self.out / "review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        done = run("render_report.py", "--out", str(self.out))
        self.assertEqual(done.returncode, 0, done.stderr)
        page = (self.out / "report.html").read_text(encoding="utf-8")
        self.assertIn("无法自动判断", page)
        self.assertNotIn("USD 560", page)
        self.assertNotIn("a@b.com", page)
        self.assertNotIn(str(self.tmp), page)
        fragment = (self.out / "report-artifact.html").read_text(encoding="utf-8")
        self.assertFalse(fragment.lstrip().lower().startswith("<!doctype"))


if __name__ == "__main__":
    unittest.main()
