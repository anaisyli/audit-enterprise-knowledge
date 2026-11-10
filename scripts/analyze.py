#!/usr/bin/env python3
"""Sort the read text into the seven modules, judge coverage, and find CUS/MAT/ANM candidates.

Reads <out>/sources/*.json and <out>/text/*.json. Writes:
  findings.json         machine results the report is built from
  review-input.md       what the AI reads before writing its judgement
  review.json           a template to fill in (only written when it does not exist yet)
Everything here is a candidate. The AI confirms or drops each one in review.json.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (EMAIL, MODULES, MONEY, PAYMENT, PHONE, around, clip, key_of, load_config, load_sections, mask,  # noqa: E402
                    matches, read_json, write_json)

CASE_SOURCE = re.compile(r"(?i)(?:^|[/_\-\s.])(?:cases?|case-stud(?:y|ies)|projects?|references?|testimonials?|reviews?|案例|项目|业绩)(?:$|[/_\-\s.])")
CASE_STRONG = re.compile(r"(?i)案例|case stud|our cases|testimonial|客户评价|买家评价|project (?:name|location|background|overview)|项目(?:名称|地点|概况|背景)")
QUESTION = re.compile(r"[?？]|^(?:q[:：.\s]|问[:：])", re.I)
DRAFT = re.compile(r"(?i)内部审核版|草稿|\bdraft\b|仅供参考|公开参考|参考款|参考供应商|for reference only|not for distribution")
REPORT_PAGE = re.compile(r"(?i)检测报告|测试报告|审核报告|验厂报告|test report|inspection report|audit report|analysis report|mill test certificate")
# A page that only says "we provide test reports" is not a report; a real one has report fields.
REPORT_BODY = re.compile(r"(?i)报告编号|报告日期|检测结果|检验结果|签发|样品(?:名称|编号)|report\s*no|date of issue|issued by|test result|sample (?:no|name|description)|heat no|炉号|conclusion|结论")
DATE_NAME = re.compile(r"(?i)^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2},? \d{4})$|^(?:blog|news|home|首页|新闻|博客)$")
BRAND_CLAIM = re.compile(r"(?i)[®™]|approved supplier|authori[sz]ed (?:dealer|distributor|agent|supplier)|official partner|strategic partner|exclusive (?:agent|distributor)|授权|指定供应商|战略合作")
MANUFACTURER = re.compile(r"(?i)\bmanufacturer\b|\bfactory\b|our manufacturing|own factory|生产厂家|自有工厂|制造商|工厂直")
TRADER = re.compile(r"(?i)trading company|\btrader\b|\bstockist\b|\bdistributor\b|\bwholesaler\b|贸易公司|贸易商|经销商|批发商")
NOT_MANUFACTURER = re.compile(r"(?i)manufacturer or (?:a )?trading|not a manufacturer|unlike many trading|是工厂还是贸易")
WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
FACTS = [
    ("成立年份", [r"(?i)(?:established|founded|incorporated|成立|创立|创建|建厂)\s*(?:in|on|于|在)?\s*[:：]?\s*((?:19|20)\d{2})",
              r"((?:19|20)\d{2})\s*年?\s*(?:成立|创立|创建|建厂)"]),
    ("员工人数", [r"(?i)(\d[\d,]{0,6})\s*\+?\s*(?:名|位)?\s*(?:employees|workers|staff members|员工|名员工|工人)"]),
    ("厂房或仓库面积", [r"(?i)(\d[\d,.]{1,9})\s*\+?\s*(?:m²|㎡|m2\b|sqm\b|square met(?:er|re)s?|平方米|平米)"]),
    ("仓库数量", [r"(?i)\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+(?:[a-z-]+\s+){0,2}warehouses\b",
              r"(\d{1,2}|[一二两三四五六七八九十])\s*(?:个|座|大)?(?:现代化)?仓库"]),
    ("出口国家数", [r"(?i)(\d{2,3})\s*\+?\s*(?:countries|个国家|多个国家|国家和地区)"]),
    ("行业经验年数", [r"(?i)(\d{1,2})\s*\+?\s*years?\s+(?:of\s+)?(?:international\s+|export\s+|industry\s+|manufacturing\s+|trading\s+)?(?:experience|exporting)\b",
                r"(\d{1,2})\s*年(?:以上)?(?:的)?(?:出口|行业|生产|外贸)?经验"]),
    ("库存量", [r"(?i)(\d[\d,.]*)\s*\+?\s*(?:mt|metric tons?|tons?|吨)\b[^.。\n]{0,30}?(?:inventory|stock|库存)",
             r"库存[^。\n]{0,8}?(\d[\d,.]*)\s*(?:万)?吨"]),
    ("生产线数量", [r"(?i)(\d{1,3})\s*\+?\s*(?:production lines|条生产线|条流水线)"]),
]
NAME_NOISE = re.compile(r"(?i)\b(?:supplier|suppliers|manufacturer|manufacturers|factory|exporter|in china|china|from china|the|best|top|leading|bulk|supply|price)\b|[|｜\-–—:：].*$")


def fact_value(raw: str) -> str:
    raw = raw.strip().lower()
    if raw in WORD_NUMBERS:
        return str(WORD_NUMBERS[raw])
    return re.sub(r"[,.](?=\d{3}\b)", "", raw).rstrip(".")


def blocks_of(source: dict[str, Any], units: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Split text units into paragraph-sized blocks; returns (location, text)."""
    flowing = source["type"].startswith(("PDF", "PPT"))
    out = []
    for unit in units:
        text = unit["text"]
        if not flowing:
            pieces = [line.strip() for line in text.splitlines()]
        else:
            pieces, buffer = [], ""
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    if buffer:
                        pieces.append(buffer)
                    buffer = ""
                    continue
                buffer = f"{buffer} {line}".strip() if buffer else line
                if (len(buffer) >= 120 and re.search(r"[.。!?！？:：;；]$", line)) or len(buffer) >= 400:
                    pieces.append(buffer)
                    buffer = ""
            if buffer:
                pieces.append(buffer)
        for piece in pieces:
            letters = re.sub(r"[\d\W_]+", "", piece)
            cjk = len(re.findall(r"[一-鿿]", piece))
            if (cjk >= 6 or len(piece) >= 15) and len(letters) >= max(4, len(piece) * 0.3):
                out.append((unit.get("loc", ""), piece))
    return out


def gate(module: str, source: dict[str, Any], text: str, previous: str) -> bool:
    """FAQ and case keywords appear everywhere; only count text that really is Q&A or a case."""
    if module == "faq":
        return bool(QUESTION.search(text[:240]) or QUESTION.search(previous[:240]))
    if module == "case":
        return bool(CASE_SOURCE.search(source["label"]) or CASE_STRONG.search(text))
    return True


def name_of(page: dict[str, Any]) -> str:
    name = NAME_NOISE.sub(" ", (page.get("h1") or page.get("title") or "").lower())
    return re.sub(r"\s+", " ", name).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Coverage, gaps and ledger candidates.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--config")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    config = load_config(args.config)
    sections = load_sections(config)
    own_terms = [t.casefold() for t in config.get("own_terms", [])]

    files = read_json(out / "sources" / "files.json", {}).get("files", [])
    site = read_json(out / "sources" / "site.json", {})
    pages = site.get("pages", [])
    sources = {s["id"]: s for s in files + pages}
    if not sources:
        print("没有任何资料：先运行 scan_files.py 或 fetch_site.py。", file=sys.stderr)
        return 2

    unique: dict[str, dict[str, Any]] = {}
    per_source_blocks: dict[str, list[str]] = defaultdict(list)
    section_hits: dict[tuple[str, str], list[str]] = defaultdict(list)
    cleanup: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    facts: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    identity = {"manufacturer": [], "trader": []}
    hits = {"draft": defaultdict(int), "report": [], "brand": [], "case": set()}

    for source_id, source in sources.items():
        text_file = read_json(out / "text" / f"{source_id}.json")
        if not text_file:
            continue
        if source["kind"] == "web" and CASE_SOURCE.search(urlparse(source["label"]).path):
            hits["case"].add(source_id)
        if source["kind"] == "file" and CASE_SOURCE.search(source["label"]):
            hits["case"].add(source_id)
        for unit in text_file["units"]:
            body = unit["text"]
            found = REPORT_PAGE.search(body)
            if found and REPORT_BODY.search(body):
                hits["report"].append({"src": source_id, "loc": unit.get("loc", ""), "text": around(mask(body), found, 120)})
        previous = ""
        for location, text in blocks_of(source, text_file["units"]):
            key = key_of(text)
            per_source_blocks[source_id].append(key)
            entry = unique.setdefault(key, {"text": text, "sources": [], "locs": []})
            if source_id not in entry["sources"]:
                entry["sources"].append(source_id)
                entry["locs"].append(location)
            folded = text.casefold()
            for module, items in sections.items():
                if not gate(module, source, text, previous):
                    continue
                for item in items:
                    if matches(folded, item["keywords"]):
                        bucket = section_hits[(module, item["name"])]
                        if key not in bucket:
                            bucket.append(key)
            previous = text
            # Things to clean before anything is used in public.
            if MONEY.search(text):
                cleanup[source_id]["价格或金额"] += len(MONEY.findall(text))
            if PAYMENT.search(text):
                cleanup[source_id]["付款条件"] += 1
            if source["kind"] == "file":
                if EMAIL.search(text):
                    cleanup[source_id]["邮箱"] += len(EMAIL.findall(text))
                if any(len(re.sub(r"\D", "", m)) >= 8 for m in PHONE.findall(text)):
                    cleanup[source_id]["电话"] += 1
            for fact, patterns in FACTS:
                for pattern in patterns:
                    for match in re.finditer(pattern, text):
                        value = fact_value(match.group(1))
                        if fact == "成立年份" and int(value) >= date.today().year:
                            continue
                        facts[fact][value].append({"src": source_id, "text": around(mask(text), match, 80)})
            trader = NOT_MANUFACTURER.search(text) or TRADER.search(text)
            if trader:
                identity["trader"].append({"src": source_id, "text": around(mask(text), trader, 120)})
            maker = MANUFACTURER.search(text)
            if maker and not NOT_MANUFACTURER.search(text):
                identity["manufacturer"].append({"src": source_id, "text": around(mask(text), maker, 120)})
            if DRAFT.search(text):
                hits["draft"][source_id] += len(DRAFT.findall(text))
            claim = BRAND_CLAIM.search(text)
            # The company's own trademark (EXAMPLE®) is not someone else's brand.
            own_mark = claim is not None and claim.group(0) in "®™" and any(t in folded[max(0, claim.start() - 24): claim.start()] for t in own_terms if t)
            if claim and not own_mark:
                hits["brand"].append({"src": source_id, "text": around(mask(text), claim, 140)})
            if CASE_STRONG.search(text):
                hits["case"].add(source_id)

    # Coverage per sub-section.
    section_rows = []
    for module, items in sections.items():
        for item in items:
            keys = section_hits.get((module, item["name"]), [])
            source_ids = sorted({s for k in keys for s in unique[k]["sources"]})
            if not keys:
                status = "缺"
            elif len(keys) < 3 or len(source_ids) < 2:
                status = "薄弱"
            else:
                status = "有"
            ranked = sorted(keys, key=lambda k: (-len(unique[k]["sources"]), -min(len(unique[k]["text"]), 300)))
            # Samples from different sources first, then fill up to four.
            picked, seen_sources = [], set()
            for k in ranked:
                if unique[k]["sources"][0] not in seen_sources:
                    picked.append(k)
                    seen_sources.add(unique[k]["sources"][0])
                if len(picked) >= 4:
                    break
            picked += [k for k in ranked if k not in picked][: 4 - len(picked)]
            samples = [{"src": unique[k]["sources"][0], "text": clip(mask(unique[k]["text"]), 200)} for k in picked]
            section_rows.append({"key": f"{module}/{item['name']}", "module": module, "name": item["name"], "how": item.get("how", ""),
                                 "auto_status": status, "blocks": len(keys), "sources": source_ids,
                                 "docs": sum(1 for s in source_ids if sources[s]["kind"] == "file"),
                                 "web": sum(1 for s in source_ids if sources[s]["kind"] == "web"), "samples": samples})

    flags: list[dict[str, Any]] = []

    def flag(ledger: str, kind: str, title: str, detail: str, src: list[str], rows: list[dict[str, Any]] | None = None, fixed: bool = False) -> None:
        flags.append({"id": f"F{len(flags) + 1:03d}", "ledger": ledger, "kind": kind, "title": title, "detail": detail,
                      "sources": list(dict.fromkeys(src)), "rows": rows or [], "fixed": fixed})

    # MAT: everything that could not be read or judged automatically. Fixed: always in the report.
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in sources.values():
        if source.get("judge") in {"不能", "部分"}:
            groups[f"{source['judge']}|{re.sub(r'[0-9]+', 'N', source.get('reason', ''))}"].append(source)
    for group_key, members in groups.items():
        judge, reason = group_key.split("|", 1)
        sample = members[0].get("reason", reason)
        # The title names the shared reason; page counts differ per file and stay in the rows.
        reason_text = re.sub(r"\s*全部\s*\d+\s*页都|\s*\d+/\d+\s*(?:页|张幻灯片)", "", sample).strip()
        title = ("无法自动判断：" if judge == "不能" else "只能部分判断：") + re.split(r"[，,（(：:]", reason_text)[0]
        rows = [{"item": m["label"], "a": m.get("reason", ""), "a_src": m["id"]} for m in members]
        flag("MAT", "unreadable" if judge == "不能" else "partial", title[:30], f"{len(members)} 份。{sample}", [m["id"] for m in members], rows, fixed=True)
    ocr_read = [s for s in sources.values() if s.get("ocr_pages")]
    if ocr_read:
        def what(s: dict[str, Any]) -> str:
            return "图片文字" if s["type"] == "图片" else f"{s['ocr_pages']} 页"
        rows = [{"item": s["label"], "a": f"{what(s)}由本机 OCR（{s.get('ocr_engine', '')}）读出", "a_src": s["id"]} for s in ocr_read]
        pictures = sum(1 for s in ocr_read if s["type"] == "图片")
        pdf_pages = sum(s["ocr_pages"] for s in ocr_read if s["type"] != "图片")
        counted = "、".join(filter(None, [f"PDF {pdf_pages} 页" if pdf_pages else "", f"图片 {pictures} 张" if pictures else ""]))
        flag("MAT", "ocr", "有资料靠本机 OCR 读出，文字待核对",
             f"{len(ocr_read)} 份资料（{counted}）没有文字层，已用本机 OCR 读出并参与了判断。OCR 可能有错字、漏字和分栏错位，"
             "建库前要用 MinerU 重新识别或人工对照原图。", [s["id"] for s in ocr_read], rows)
    over = site.get("report", {}).get("skipped_over_limit", 0)
    if over:
        flag("MAT", "crawl_limit", "官网页面没读完", f"超过页数上限，还有约 {over} 个地址没读。需要时用 --max-pages 调大后重跑。", [], fixed=True)

    # CUS: company facts written differently in different places.
    for fact, values in facts.items():
        if len(values) < 2:
            continue
        rows = [{"item": fact, "a": value, "a_src": hits_[0]["src"], "quote": hits_[0]["text"], "count": len(hits_)} for value, hits_ in
                sorted(values.items(), key=lambda kv: -len(kv[1]))]
        flag("CUS", "fact_conflict", f"{fact}有 {len(values)} 种写法", "同一项公司数据在资料里写法不同，请客户确认以哪个为准。个人履历、不同厂区的数字也会被列进来，判断时剔除。",
             [h["src"] for hs in values.values() for h in hs], rows[:8])
    if identity["manufacturer"] and identity["trader"]:
        rows = [{"item": "自称工厂/制造商", "a": identity["manufacturer"][0]["text"], "a_src": identity["manufacturer"][0]["src"], "count": len(identity["manufacturer"])},
                {"item": "自称贸易商/经销商", "a": identity["trader"][0]["text"], "a_src": identity["trader"][0]["src"], "count": len(identity["trader"])}]
        flag("CUS", "identity", "公司是工厂还是贸易商，说法不一", "有的地方写工厂或制造商，有的地方写贸易商、经销商。工贸一体的公司可以两种都写，但要客户确认对外怎么说。",
             [h["src"] for h in identity["manufacturer"][:6] + identity["trader"][:6]], rows)
    if hits["case"]:
        flag("CUS", "case", "案例和客户信息能否公开", "这些资料或页面里有案例、项目或客户评价。按规则，案例要客户确认能公开后才能写进整理页。", sorted(hits["case"]))

    # ANM: internal judgement.
    drafts = [s for s, n in hits["draft"].items() if n >= 3]
    if drafts:
        flag("ANM", "reference_draft", "疑似参考整理稿，不是公司原始资料", "文件里多次出现“草稿”“仅供参考”“内部审核版”这类说法，可能是从别家资料或旧稿整理的。", drafts)
    if hits["report"]:
        rows = [{"item": h["loc"] or "段落", "a": h["text"], "a_src": h["src"]} for h in hits["report"][:10]]
        flag("ANM", "report_page", "检测或审核报告内容需要内部判断", "报告页要看签发方、日期、对象是不是本公司产品，再决定能否引用。", [h["src"] for h in hits["report"]], rows)
    if hits["brand"]:
        rows = [{"item": "原文", "a": h["text"], "a_src": h["src"]} for h in hits["brand"][:10]]
        flag("ANM", "brand", "品牌、授权和合作方说法需核实", "出现了商标符号、授权、认可供应商或战略合作的说法。别家品牌名能否公开写，要内部核实后再定。", [h["src"] for h in hits["brand"]], rows)

    # ANM: website copy-paste and duplicate pages.
    web_pages = [p for p in pages if p.get("judge") == "能"]
    names = {p["id"]: name_of(p) for p in web_pages}
    names = {k: v for k, v in names.items() if len(v) >= 4 and not DATE_NAME.match(v)}
    paste_rows = []
    for page in web_pages:
        own = names.get(page["id"])
        if not own:
            continue
        own_words = set(own.split())
        found = []
        for heading in page.get("headings", []):
            low = heading.lower()
            if own in low:
                continue
            for other_id, other in names.items():
                if other_id == page["id"] or len(other.split()) < 2 or other in own or own in other:
                    continue
                if own_words <= set(other.split()):  # a variant of this page's own product
                    continue
                if re.search(r"(?<![a-z0-9])" + re.escape(other) + r"(?![a-z0-9])", low) and name_of({"h1": heading}) != other:
                    found.append({"item": page.get("h1") or page["label"], "a": heading, "a_src": page["id"], "b": f"像是 {other} 页的内容", "b_src": other_id})
                    break
        # A page whose headings name many other products is a listing page, not a mistake.
        if len({row["b_src"] for row in found}) < 4:
            paste_rows.extend(found)
    if paste_rows:
        flag("ANM", "copy_paste", "官网页面疑似复制粘贴错误", "这些页面里的小标题写的是另一个产品的名字，可能是套模板时没改。要人对照页面确认。",
             [r["a_src"] for r in paste_rows], paste_rows[:30])
    keysets = {p["id"]: set(per_source_blocks.get(p["id"], [])) for p in web_pages}
    duplicates = []
    ids = [i for i in keysets if len(keysets[i]) >= 5]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            overlap = len(keysets[a] & keysets[b]) / max(1, min(len(keysets[a]), len(keysets[b])))
            if overlap >= 0.8:
                duplicates.append({"item": "内容重复", "a": sources[a]["label"], "a_src": a, "b": sources[b]["label"], "b_src": b})
    if duplicates:
        flag("ANM", "duplicate", "官网有内容几乎相同的页面", "两页八成以上的段落相同，可能是重复页或测试页。", [d["a_src"] for d in duplicates] + [d["b_src"] for d in duplicates], duplicates[:20])

    findings = {
        "generated": date.today().isoformat(),
        "config": {k: config.get(k) for k in ("company", "industry", "project_id") if config.get(k)},
        "sources": list(sources.values()),
        "sections": section_rows,
        "flags": flags,
        "cleanup": [{"src": s, **dict(kinds)} for s, kinds in cleanup.items()],
        "unique_blocks": len(unique),
    }
    for source in findings["sources"]:
        source.pop("path", None)  # real paths stay on this computer
    write_json(out / "findings.json", findings)
    write_review_input(out, findings, sources)
    template = out / "review.json"
    if not template.exists():
        write_json(template, review_template(findings))
    counts = defaultdict(int)
    for row in section_rows:
        counts[row["auto_status"]] += 1
    print(f"小板块自动判断：有 {counts['有']}，薄弱 {counts['薄弱']}，缺 {counts['缺']}；候选事项 {len(flags)} 项。")
    print(f"下一步：读 {out / 'review-input.md'}，填写 {template}，再运行 render_report.py。")
    return 0


def review_template(findings: dict[str, Any]) -> dict[str, Any]:
    return {
        "_说明": "AI 填写。规则见 references/review-rules.md。sections 每项都要写 note；status 和自动判断不同时写 reason。flags 每项写 decision：keep 或 drop（fixed 的只能 keep）。",
        "title": "", "company": findings["config"].get("company", ""), "industry": findings["config"].get("industry", ""),
        "summary": "",
        "sections": {row["key"]: {"status": row["auto_status"], "note": "", "how": row["how"], "reason": "", "sources": []}
                     for row in findings["sections"]},
        "flags": {f["id"]: {"decision": "keep" if f["fixed"] else "", "title": "", "detail": "", "note": ""} for f in findings["flags"]},
        "items": [],
        "source_notes": {},
        "priorities": [],
    }


def write_review_input(out: Path, findings: dict[str, Any], sources: dict[str, dict[str, Any]]) -> None:
    lines = ["# 待判断材料", "", "这是脚本的初判，全部是候选。读完后填写 review.json，规则见 skill 的 references/review-rules.md。",
             "原文全文在 text/<编号>.json；样例已隐去金额、邮箱、电话。", "", "## 资料", "",
             "| 编号 | 资料 | 类型 | 能否自动判断 | 说明 |", "|---|---|---|---|---|"]
    for s in findings["sources"]:
        label = s["label"] if len(s["label"]) <= 70 else "…" + s["label"][-69:]
        lines.append(f"| {s['id']} | {label} | {s['type']} | {s.get('judge', '')} | {s.get('reason', '') or (str(s.get('chars', 0)) + ' 字')} |")
    lines.extend(["", "## 小板块初判", "", "状态是按段数和来源数自动算的：0 段为“缺”；少于 3 段或只有 1 份来源为“薄弱”；其余为“有”。"
                  "关键词会误命中，“有”不等于内容够用，要看样例和原文再定。", ""])
    current = ""
    for row in findings["sections"]:
        if row["module"] != current:
            current = row["module"]
            lines.extend(["", f"### {MODULES[current]}（{current}）", ""])
        lines.append(f"- **{row['name']}** — 自动：{row['auto_status']}；{row['blocks']} 段，来源 {len(row['sources'])} 份（文件 {row['docs']}、网页 {row['web']}）")
        for sample in row["samples"]:
            lines.append(f"  - [{sample['src']}] {sample['text']}")
    lines.extend(["", "## 候选事项", ""])
    for f in findings["flags"]:
        lines.append(f"### {f['id']} · {f['ledger']} · {f['title']}{'（固定保留）' if f['fixed'] else ''}")
        lines.append(f"{f['detail']}  来源：{', '.join(f['sources'][:15])}{' 等' if len(f['sources']) > 15 else ''}")
        for r in f["rows"][:12]:
            extra = f"；另见 {r['b']} [{r['b_src']}]" if r.get("b") else ""
            quote = f"「{r['quote']}」" if r.get("quote") else ""
            count = f"（{r['count']} 处）" if r.get("count") else ""
            lines.append(f"- {r['item']}：{r['a']} [{r['a_src']}]{count}{quote}{extra}")
        lines.append("")
    if findings["cleanup"]:
        lines.extend(["## 需清理的内容（只计数）", ""])
        for c in findings["cleanup"]:
            parts = "、".join(f"{k} {v}" for k, v in c.items() if k != "src")
            lines.append(f"- [{c['src']}] {parts}")
    web = [s for s in findings["sources"] if s["kind"] == "web" and s.get("headings")]
    if web:
        lines.extend(["", "## 官网各页小标题（用来查套模板没改、内容放错）", ""])
        for s in web:
            lines.append(f"- [{s['id']}] {s.get('h1') or s.get('title')}：{' | '.join(s['headings'][:14])[:400]}")
    (out / "review-input.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
