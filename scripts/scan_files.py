#!/usr/bin/env python3
"""List every local file, read the text that can be read, and say plainly what cannot.

Writes <out>/sources/files.json (one row per file) and <out>/text/<id>.json (text units).
Originals are only read, never changed. A file whose content cannot be judged
automatically gets judge = "不能" with the reason; the report lists it in MAT.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import read_text_file, write_json  # noqa: E402
from htmltext import parse_html  # noqa: E402
from ocr import NO_ENGINE, engines as ocr_engines, ocr_images, ocr_pdf_pages  # noqa: E402

TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".eml", ".log", ".xml", ".yaml", ".yml"}
HTML_EXT = {".html", ".htm", ".mhtml"}
OLD_OFFICE = {".doc": "docx", ".xls": "xlsx", ".ppt": "pptx", ".rtf": "docx", ".odt": "docx", ".ods": "xlsx", ".odp": "pptx", ".wps": "docx", ".et": "xlsx", ".dps": "pptx"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}
MEDIA_EXT = {".mp4", ".mov", ".avi", ".wmv", ".mkv", ".flv", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".amr"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"}
DESIGN_EXT = {".ai", ".psd", ".cdr", ".eps", ".dwg", ".dxf", ".skp", ".indd", ".sketch", ".fig", ".step", ".stp", ".igs"}
SYSTEM_FILES = re.compile(r"(?i)^(?:~\$.*|\.ds_store|thumbs\.db|desktop\.ini|\._.*)$")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
EMPTY_PAGE_CHARS = 20
MAX_SHEET_ROWS = 3000
OCR = {"enabled": True, "left": 300}  # PDF pages + images this run may OCR in total
# Images that are likely documents (certificates, reports, catalogue pages) are OCR'd first when the budget is short.
DOC_LIKE_IMAGE = re.compile(r"(?i)证书|证明|资质|执照|许可|报告|检测|认证|专利|画册|样本|手册|说明|参数|规格|扫描|截图|页|表|certif|licen|report|test|iso|patent|catalog|brochure|manual|spec|scan|page|screenshot|sheet")
MIN_OCR_CHARS = 20
QUOTE_HEADER = re.compile(r"(?i)单价|金额|总价|报价|unit\s*price|amount|fob|cif|cfr|exw|qty|quantity|数量")


def find_soffice() -> str | None:
    env = os.environ.get("LIBREOFFICE_PATH")
    if env and Path(env).exists():
        return env
    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for guess in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                  "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(guess).exists():
            return guess
    return None


def docx_units(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    units, buffer = [], []
    body = root.find(f"{W}body")
    for element in list(body) if body is not None else []:
        if element.tag == f"{W}p":
            text = "".join(t.text or "" for t in element.iter(f"{W}t")).strip()
            if text:
                buffer.append(text)
        elif element.tag == f"{W}tbl":
            if buffer:
                units.append({"loc": "正文", "text": "\n".join(buffer)})
                buffer = []
            rows = []
            for row in element.iter(f"{W}tr"):
                cells = ["".join(t.text or "" for t in cell.iter(f"{W}t")).strip() for cell in row.iter(f"{W}tc")]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                units.append({"loc": "表格", "text": "\n".join(rows)})
    if buffer:
        units.append({"loc": "正文", "text": "\n".join(buffer)})
    return units


def pptx_units(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        units = []
        for number, name in enumerate(names, 1):
            root = ET.fromstring(archive.read(name))
            paragraphs = []
            for paragraph in root.iter(f"{A}p"):
                text = "".join(t.text or "" for t in paragraph.iter(f"{A}t")).strip()
                if text:
                    paragraphs.append(text)
            units.append({"loc": f"幻灯片 {number}", "text": "\n".join(paragraphs)})
    return units


def column_number(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "A")
    number = 0
    for char in letters.group(0) if letters else "A":
        number = number * 26 + ord(char) - 64
    return number


def xlsx_units(path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    info: dict[str, Any] = {"sheets": 0, "quote_sheets": [], "truncated": []}
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).iter(f"{S}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{S}t")))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet_names = [s.get("name", "") for s in workbook.iter(f"{S}sheet")]
        rels = {}
        if "xl/_rels/workbook.xml.rels" in archive.namelist():
            for rel in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels")):
                rels[rel.get("Id")] = rel.get("Target", "")
        targets = []
        for sheet in workbook.iter(f"{S}sheet"):
            rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rels.get(rid, "")
            target = target.lstrip("/")
            targets.append(target if target.startswith("xl/") else f"xl/{target}")
        units = []
        for name, target in zip(sheet_names, targets):
            if target not in archive.namelist():
                continue
            info["sheets"] += 1
            rows = []
            for row in ET.fromstring(archive.read(target)).iter(f"{S}row"):
                cells: dict[int, str] = {}
                for cell in row.iter(f"{S}c"):
                    value = cell.find(f"{S}v")
                    inline = cell.find(f"{S}is")
                    if cell.get("t") == "s" and value is not None and value.text and value.text.isdigit():
                        text = shared[int(value.text)] if int(value.text) < len(shared) else ""
                    elif inline is not None:
                        text = "".join(t.text or "" for t in inline.iter(f"{S}t"))
                    else:
                        text = value.text if value is not None and value.text else ""
                    if text.strip():
                        cells[column_number(cell.get("r", ""))] = text.strip()
                if cells:
                    rows.append(" | ".join(cells[k] for k in sorted(cells)))
                if len(rows) >= MAX_SHEET_ROWS:
                    info["truncated"].append(name)
                    break
            if rows and QUOTE_HEADER.search(" ".join(rows[:5])):
                info["quote_sheets"].append(name)
            units.append({"loc": f"工作表 {name}", "text": "\n".join(rows)})
    return units, info


def pdf_units(path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    info: dict[str, Any] = {"reader": ""}
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            pymupdf = None
    if pymupdf is not None:
        document = pymupdf.open(str(path))
        if document.needs_pass:
            raise PermissionError("PDF 有密码")
        info["reader"] = "PyMuPDF"
        units = [{"loc": f"第 {i} 页", "text": page.get_text("text")} for i, page in enumerate(document, 1)]
        document.close()
        return units, info
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as error:
        raise RuntimeError("没装 PyMuPDF 或 pypdf，读不了 PDF") from error
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise PermissionError("PDF 有密码")
    info["reader"] = "pypdf"
    return [{"loc": f"第 {i} 页", "text": page.extract_text() or ""} for i, page in enumerate(reader.pages, 1)], info


def convert_old_office(path: Path, target: str, soffice: str, timeout: int) -> Path:
    work = Path(tempfile.mkdtemp(prefix="audit-lo-"))
    profile = work / "profile"
    command = [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", target, "--outdir", str(work), str(path)]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    produced = list(work.glob(f"*.{target}"))
    if not produced:
        raise RuntimeError("LibreOffice 转换没有产出文件")
    return produced[0]


def kind_of(extension: str) -> str:
    if extension == ".pdf":
        return "PDF"
    if extension in {".docx", ".docm"}:
        return "Word"
    if extension in {".pptx", ".pptm"}:
        return "PPT"
    if extension in {".xlsx", ".xlsm"}:
        return "Excel"
    if extension in OLD_OFFICE:
        return {"docx": "Word", "xlsx": "Excel", "pptx": "PPT"}[OLD_OFFICE[extension]] + "（旧格式）"
    if extension in TEXT_EXT:
        return "文本"
    if extension in HTML_EXT:
        return "网页文件"
    if extension in IMAGE_EXT:
        return "图片"
    if extension in MEDIA_EXT:
        return "视频/音频"
    if extension in ARCHIVE_EXT:
        return "压缩包"
    if extension in DESIGN_EXT:
        return "设计/图纸文件"
    return "其他"


def read_file(path: Path, soffice: str | None, timeout: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Return text units and facts about how far the file could be read."""
    extension = path.suffix.lower()
    info: dict[str, Any] = {}
    if path.stat().st_size == 0:
        return [], {"judge": "不能", "reason": "空文件"}
    if extension in MEDIA_EXT:
        return [], {"judge": "不能", "reason": "视频/音频没有转写，内容无法自动判断"}
    if extension in ARCHIVE_EXT:
        return [], {"judge": "不能", "reason": "压缩包没有解开，里面的文件没检查"}
    if extension in DESIGN_EXT:
        return [], {"judge": "不能", "reason": "设计或图纸文件读不出正文，要人打开看"}
    if extension in OLD_OFFICE:
        if not soffice:
            return [], {"judge": "不能", "reason": "旧版 Office 格式，需要 LibreOffice 转换；没找到 LibreOffice"}
        converted = convert_old_office(path, OLD_OFFICE[extension], soffice, timeout)
        try:
            units, info = read_converted(converted)
        finally:
            shutil.rmtree(converted.parent, ignore_errors=True)
        info["converted"] = True
        return units, info
    if extension == ".pdf":
        units, info = pdf_units(path)
        return units, ocr_empty_pages(path, units, info)
    return read_converted(path)


def ocr_empty_pages(path: Path, units: list[dict[str, str]], info: dict[str, Any]) -> dict[str, Any]:
    """Pages without a text layer get local OCR first; only what OCR cannot read is reported as unreadable."""
    total = len(units)
    empty = [n for n, u in enumerate(units, 1) if len(u["text"].strip()) < EMPTY_PAGE_CHARS]
    info["pages"] = total
    if not empty:
        return info
    problem, engine, recovered, skipped = "", "", [], 0
    if OCR["enabled"] and OCR["left"] > 0:
        todo = empty[: OCR["left"]]
        skipped = len(empty) - len(todo)
        OCR["left"] -= len(todo)
        print(f"     OCR {path.name}：{len(todo)} 页没有文字层，正在识别…", flush=True)
        texts, engine, problem = ocr_pdf_pages(path, todo)
        for number, text in texts.items():
            if len(text) >= EMPTY_PAGE_CHARS:
                units[number - 1]["text"] = text
                units[number - 1]["ocr"] = engine
                recovered.append(number)
    elif OCR["enabled"]:
        skipped = len(empty)
    else:
        problem = "按要求没有做 OCR（--no-ocr）"
    left = [n for n in empty if n not in recovered]
    info["empty_pages"] = len(left)
    if recovered:
        info["ocr_pages"] = len(recovered)
        info["ocr_engine"] = engine
    done = f"{len(recovered)} 页没有文字层，已用本机 OCR 读出（可能有错字）" if recovered else ""
    if skipped and not problem:
        problem = f"其中 {skipped} 页超过本次 OCR 页数上限没有识别（--ocr-max-pages）"
    if not left:
        info.update(judge="能", reason=done)
    elif len(left) == total:
        info.update(judge="不能", reason=f"全部 {total} 页都没有文字层（扫描件或图片型画册），{problem or 'OCR 后仍读不出文字'}，要 MinerU 或人工看图")
    else:
        info.update(judge="部分", reason="；".join(filter(None, [done, f"{len(left)}/{total} 页仍没有文字（{problem or 'OCR 后仍读不出'}）"])))
    return info


def read_converted(path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    extension = path.suffix.lower()
    info: dict[str, Any] = {}
    if extension in {".docx", ".docm"}:
        units = docx_units(path)
    elif extension in {".pptx", ".pptm"}:
        units = pptx_units(path)
        empty = [u for u in units if len(u["text"].strip()) < EMPTY_PAGE_CHARS]
        info["pages"] = len(units)
        info["empty_pages"] = len(empty)
        if units and len(empty) == len(units):
            info.update(judge="不能", reason="幻灯片上没有文字，内容在图片里，要 OCR 后才能判断")
        elif len(empty) > len(units) / 2:
            info.update(judge="部分", reason=f"{len(empty)}/{len(units)} 张幻灯片基本没有文字，内容可能在图片里")
    elif extension in {".xlsx", ".xlsm"}:
        units, sheet_info = xlsx_units(path)
        info.update(sheet_info)
        if sheet_info["quote_sheets"]:
            info.update(judge="部分", reason="像报价单或订单（表头有单价、数量、金额），价格和买家信息要清理，不能直接当公开知识")
    elif extension in HTML_EXT:
        page = parse_html(read_text_file(path))
        units = [{"loc": "网页", "text": "\n".join(page["blocks"])}]
        info["title"] = page["title"]
    elif extension in TEXT_EXT:
        units = [{"loc": "全文", "text": read_text_file(path)}]
    else:
        return [], {"judge": "不能", "reason": f"不认识的文件类型（{extension or '无扩展名'}），没有读取"}
    return units, info


def read_images(images: list[tuple[dict[str, Any], Path]], out: Path) -> None:
    """OCR image files in one batch; an image is only "cannot judge" when OCR finds no real text in it."""
    ordered = sorted(images, key=lambda item: 0 if DOC_LIKE_IMAGE.search(item[1].name) else 1)
    allowed = ordered[: OCR["left"]] if OCR["enabled"] else []
    OCR["left"] -= len(allowed)
    texts, engine, problem = {}, "", ""
    if allowed:
        print(f"     OCR 图片：{len(allowed)} 张，正在识别…", flush=True)
        texts, engine, problem = ocr_images([path for _, path in allowed])
    allowed_ids = {row["id"] for row, _ in allowed}
    for row, path in images:
        text = texts.get(str(path), "")
        if not OCR["enabled"]:
            row.update(judge="不能", reason="图片里的字没有识别（按要求没有做 OCR，--no-ocr），内容无法自动判断")
        elif row["id"] not in allowed_ids:
            row.update(judge="不能", reason="超过本次 OCR 页数上限没有识别（--ocr-max-pages），内容无法自动判断")
        elif problem == NO_ENGINE:
            row.update(judge="不能", reason=f"图片里的字没法识别：{NO_ENGINE}")
        elif len(text) >= MIN_OCR_CHARS:
            row.update(judge="能", reason="图片里的字已用本机 OCR 读出（可能有错字）", ocr_pages=1, ocr_engine=engine)
            write_json(out / "text" / f"{row['id']}.json", {"id": row["id"], "units": [{"loc": "图片", "text": text, "ocr": engine}]})
        else:
            row.update(judge="不能", reason="OCR 没读出成段文字：多半是产品、现场或工厂照片，内容要人看")
        row["chars"] = len(text) if row["judge"] == "能" else 0
        print(f"{row['id']} {row['judge']:>2}  {row['label']}  — {row['reason']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory local files and extract readable text.")
    parser.add_argument("--files", nargs="+", required=True, help="一个或几个本地文件夹或文件")
    parser.add_argument("--out", required=True, help="运行目录")
    parser.add_argument("--timeout", type=int, default=120, help="LibreOffice 每个文件最多等几秒")
    parser.add_argument("--ocr-max-pages", type=int, default=300, help="本次最多 OCR 多少页（没有文字层的 PDF 页）")
    parser.add_argument("--no-ocr", action="store_true", help="不做 OCR")
    args = parser.parse_args()
    OCR["enabled"], OCR["left"] = not args.no_ocr, args.ocr_max_pages

    out = Path(args.out).resolve()
    (out / "text").mkdir(parents=True, exist_ok=True)
    soffice = find_soffice()
    rows: list[dict[str, Any]] = []
    images: list[tuple[dict[str, Any], Path]] = []
    number = 0
    for given in args.files:
        root = Path(given).resolve()
        if not root.exists():
            print(f"找不到：{root}", file=sys.stderr)
            return 2
        paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        base = root.parent if root.is_file() else root
        for path in paths:
            if SYSTEM_FILES.match(path.name):
                continue
            number += 1
            source_id = f"D{number:03d}"
            relative = f"{base.name}/{path.relative_to(base).as_posix()}"
            row: dict[str, Any] = {"id": source_id, "kind": "file", "label": relative, "path": str(path),
                                   "type": kind_of(path.suffix.lower()), "size": path.stat().st_size}
            if path.suffix.lower() in IMAGE_EXT and row["size"] > 0:
                images.append((row, path))  # OCR'd together after the other files, in one batch
                rows.append(row)
                continue
            try:
                units, info = read_file(path, soffice, args.timeout)
            except (zipfile.BadZipFile, ET.ParseError, KeyError):
                units, info = [], {"judge": "不能", "reason": "文件损坏或不是标准 Office 格式，读不出来"}
            except PermissionError as error:
                units, info = [], {"judge": "不能", "reason": f"{error}，要客户提供无密码版本"}
            except subprocess.TimeoutExpired:
                units, info = [], {"judge": "不能", "reason": "LibreOffice 转换超时"}
            except Exception as error:  # report, keep going
                units, info = [], {"judge": "不能", "reason": f"读取失败：{type(error).__name__}: {error}"[:160]}
            chars = sum(len(u["text"].strip()) for u in units)
            if "judge" not in info:
                if chars == 0:
                    info.update(judge="不能", reason="读到的文字为空")
                else:
                    info["judge"] = "能"
            row.update(info)
            row["chars"] = chars
            rows.append(row)
            if units:
                write_json(out / "text" / f"{source_id}.json", {"id": source_id, "units": units})
            print(f"{source_id} {row['judge']:>2}  {relative}" + (f"  — {row.get('reason')}" if row.get("reason") else ""))
    if images:
        read_images(images, out)
    write_json(out / "sources" / "files.json", {"libreoffice": bool(soffice), "ocr_engines": ocr_engines() if OCR["enabled"] else [], "files": rows})
    count = {k: sum(1 for r in rows if r["judge"] == k) for k in ("能", "部分", "不能")}
    ocr_files = sum(1 for r in rows if r.get("ocr_pages"))
    print(f"共 {len(rows)} 个文件：能读 {count['能']} 个，部分能读 {count['部分']} 个，无法自动判断 {count['不能']} 个"
          + (f"；其中 {ocr_files} 个用了本机 OCR。" if ocr_files else "。"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
