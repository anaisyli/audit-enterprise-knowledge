#!/usr/bin/env python3
"""Check review.json against findings.json, then write the report web page.

Writes <out>/report.html (open it directly in a browser) and <out>/report-artifact.html
(the same page without the <html>/<head>/<body> wrapper, for hosts that publish pages).
Exit code 1 lists every problem in review.json; nothing is written until they are fixed.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EMAIL, LEDGERS, MODULE_CODES, MODULES, MONEY, SKILL_DIR, STATUSES, VERSION, load_config, read_json  # noqa: E402

STATUS_CLASS = {"有": "ok", "薄弱": "weak", "缺": "miss", "决定不写": "skip"}
JUDGE_CLASS = {"能": "ok", "部分": "weak", "不能": "miss"}
FONTS = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Noto+Sans+SC:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">'


def e(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def check(findings: dict[str, Any], review: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    ids = {s["id"] for s in findings["sources"]}
    auto = {row["key"]: row for row in findings["sections"]}
    flags = {f["id"]: f for f in findings["flags"]}

    def check_sources(where: str, values: Any) -> None:
        if values in (None, ""):
            return
        if not isinstance(values, list):
            errors.append(f"{where}：sources 要写成编号列表")
            return
        for value in values:
            if value not in ids:
                errors.append(f"{where}：来源编号 {value} 不存在")

    def check_text(where: str, text: str) -> None:
        if MONEY.search(text or "") or EMAIL.search(text or ""):
            errors.append(f"{where}：报告里不写金额、价格或邮箱，改成描述（比如“有报价”）")

    if not (review.get("summary") or "").strip():
        errors.append("summary：写一两句总体结论")
    sections = review.get("sections") or {}
    for key, row in auto.items():
        mine = sections.get(key)
        if not isinstance(mine, dict):
            errors.append(f"sections.{key}：没有判断")
            continue
        status = mine.get("status")
        if status not in STATUSES:
            errors.append(f"sections.{key}：status 只能是 {'/'.join(STATUSES)}")
        if not (mine.get("note") or "").strip():
            errors.append(f"sections.{key}：note 要写现状（有什么、缺什么）")
        if status in STATUSES and status != row["auto_status"] and not (mine.get("reason") or "").strip():
            errors.append(f"sections.{key}：状态从自动的“{row['auto_status']}”改成“{status}”，要在 reason 写原因")
        if status in {"薄弱", "缺"} and not (mine.get("how") or "").strip():
            errors.append(f"sections.{key}：薄弱或缺的小板块要写 how（怎么补）")
        check_sources(f"sections.{key}", mine.get("sources"))
        for field in ("note", "how", "reason"):
            check_text(f"sections.{key}.{field}", mine.get(field, ""))
    for key in sections:
        if key not in auto and not key.startswith("_"):
            errors.append(f"sections.{key}：findings.json 里没有这个小板块（改小板块要改配置后重跑 analyze.py）")

    decisions = review.get("flags") or {}
    for flag_id, flag in flags.items():
        decision = (decisions.get(flag_id) or {}).get("decision", "")
        if decision not in {"keep", "drop"}:
            errors.append(f"flags.{flag_id}（{flag['title']}）：decision 要写 keep 或 drop")
        elif decision == "drop" and flag["fixed"]:
            errors.append(f"flags.{flag_id}：这是读不了或只能部分读的资料，必须留在 MAT，不能 drop")
        elif decision == "drop" and not ((decisions.get(flag_id) or {}).get("note") or "").strip():
            errors.append(f"flags.{flag_id}：drop 时在 note 写为什么不算问题")
        for field in ("title", "detail", "note"):
            check_text(f"flags.{flag_id}.{field}", (decisions.get(flag_id) or {}).get(field, ""))
        title = (decisions.get(flag_id) or {}).get("title", "")
        if title and len(title) > 30:
            errors.append(f"flags.{flag_id}.title：一句话问题不超过 30 字（现在 {len(title)} 字）")

    for number, item in enumerate(review.get("items") or [], 1):
        where = f"items[{number}]"
        if item.get("ledger") not in LEDGERS:
            errors.append(f"{where}：ledger 只能是 CUS、MAT 或 ANM")
        title = (item.get("title") or "").strip()
        if not title:
            errors.append(f"{where}：title 要写一句话问题")
        elif len(title) > 30:
            errors.append(f"{where}：title 不超过 30 字（现在 {len(title)} 字），长内容写进 detail")
        if not (item.get("detail") or "").strip():
            errors.append(f"{where}：detail 要写具体涉及什么内容")
        if item.get("ledger") in {"CUS", "ANM"} and not item.get("sources") and not item.get("rows"):
            errors.append(f"{where}：CUS 和 ANM 事项要给 sources 或 rows，指到具体资料")
        check_sources(where, item.get("sources"))
        for row in item.get("rows") or []:
            for side in ("a_src", "b_src"):
                if row.get(side) and row[side] not in ids:
                    errors.append(f"{where}.rows：来源编号 {row[side]} 不存在")
        for field in ("title", "detail"):
            check_text(f"{where}.{field}", item.get(field, ""))
    for source_id in (review.get("source_notes") or {}):
        if source_id not in ids:
            errors.append(f"source_notes：来源编号 {source_id} 不存在")
    priorities = review.get("priorities") or []
    if not priorities:
        errors.append("priorities：写 3–7 项建议先补的事")
    for number, item in enumerate(priorities, 1):
        if not (item.get("title") or "").strip():
            errors.append(f"priorities[{number}]：title 不能空")
    return errors


def source_link(source: dict[str, Any] | None, source_id: str) -> str:
    if source is None:
        return f'<span class="src">{e(source_id)}</span>'
    if source["kind"] == "web" and source["label"].startswith("http"):
        return f'<a class="src" href="{e(source["label"])}" title="{e(source["label"])}" target="_blank" rel="noopener">{e(source_id)}</a>'
    return f'<span class="src" title="{e(source["label"])}">{e(source_id)}</span>'


def build(findings: dict[str, Any], review: dict[str, Any], config: dict[str, Any]) -> tuple[str, str]:
    sources = {s["id"]: s for s in findings["sources"]}
    links = lambda values: "".join(source_link(sources.get(v), v) for v in values)  # noqa: E731
    prefix = f"{config['project_id']}-" if config.get("project_id") else ""
    company = review.get("company") or config.get("company") or ""
    title = review.get("title") or (f"{company} 知识库缺口" if company else "知识库缺口分析")
    rows = findings["sections"]
    decided = {r["key"]: review["sections"][r["key"]] for r in rows}
    counts = Counter(decided[r["key"]]["status"] for r in rows)
    total = len(rows) or 1

    # Ledger entries: kept candidates first, then the AI's own items; numbered per ledger.
    ledgers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for flag in findings["flags"]:
        decision = review["flags"][flag["id"]]
        if decision["decision"] != "keep":
            continue
        ledgers[flag["ledger"]].append({"title": decision.get("title") or flag["title"], "detail": decision.get("detail") or flag["detail"],
                                        "note": decision.get("note", ""), "sources": flag["sources"], "rows": flag["rows"], "auto": True})
    for item in review.get("items") or []:
        ledgers[item["ledger"]].append({"title": item["title"], "detail": item["detail"], "note": item.get("note", ""),
                                        "sources": item.get("sources") or [], "rows": item.get("rows") or [], "auto": False})
    for ledger, items in ledgers.items():
        for number, item in enumerate(items, 1):
            item["id"] = f"{prefix}{ledger}-{number:03d}"

    files = [s for s in findings["sources"] if s["kind"] == "file"]
    pages = [s for s in findings["sources"] if s["kind"] == "web"]
    cannot = sum(1 for s in findings["sources"] if s.get("judge") == "不能")
    partial = sum(1 for s in findings["sources"] if s.get("judge") == "部分")
    sites = sorted({re.sub(r"^https?://", "", s["label"]).split("/")[0] for s in pages if s["label"].startswith("http")})
    source_line = "、".join(filter(None, [f"本地文件 {len(files)} 个" if files else "", f"官网页面 {len(pages)} 页（{'、'.join(sites)}）" if pages else ""]))

    def bar(counter: Counter, size: int, label: str) -> str:
        parts = "".join(f'<i class="{STATUS_CLASS[s]}" style="width:{counter[s] / size * 100:.2f}%"></i>' for s in STATUSES if counter[s])
        return f'<div class="bar" role="img" aria-label="{e(label)}">{parts}</div>'

    out: list[str] = []
    out.append('<div class="wrap">')
    # Header and summary
    out.append('<section class="sheet" aria-labelledby="t"><div class="head">')
    out.append('<div class="eyebrow">Knowledge Gap Audit · 覆盖与缺口</div>')
    out.append(f'<h1 id="t">{e(title)}</h1>')
    out.append(f'<p class="lede">{e(review.get("summary", ""))}</p></div>')
    out.append('<dl class="fields">')
    for name, value in [("检查对象", company or "（未填写）"), ("检查日期", findings["generated"]), ("来源", source_line or "无"),
                        ("能否自动判断", f"不能 {cannot} 份，部分 {partial} 份，见 MAT 和资料清单")]:
        out.append(f'<div class="field"><dt>{e(name)}</dt><dd>{e(value)}</dd></div>')
    out.append('</dl><div class="summary"><div class="tally">')
    for status in STATUSES:
        if counts[status] or status != "决定不写":
            out.append(f'<div class="t-{STATUS_CLASS[status]}"><span class="n">{counts[status]}</span><span class="lbl">{e(status)}</span></div>')
    out.append('<span class="sep" aria-hidden="true"></span>')
    for ledger in LEDGERS:
        out.append(f'<div><a href="#{ledger.lower()}" style="color:inherit;text-decoration:none"><span class="n">{len(ledgers[ledger])}</span><span class="lbl">{ledger}</span></a></div>')
    out.append("</div>")
    out.append(bar(counts, total, "，".join(f"{s} {counts[s]}" for s in STATUSES if counts[s]) + f"，共 {total} 个小板块"))
    out.append('<div class="mods" aria-label="各模块状态">')
    for module, name in MODULES.items():
        mine = [r for r in rows if r["module"] == module]
        if not mine:
            continue
        c = Counter(decided[r["key"]]["status"] for r in mine)
        out.append(f'<div class="mod-row"><span class="name">{e(MODULE_CODES[module][:2])} {e(name)}</span>{bar(c, len(mine), name)}'
                   f'<span class="count">{c["有"]} · {c["薄弱"]} · {c["缺"]}{" · " + str(c["决定不写"]) if c["决定不写"] else ""}</span></div>')
    out.append("</div>")
    out.append('<p class="note">数字依次是“有 · 薄弱 · 缺”。脚本先按关键词把段落分到小板块，按段数和来源数初判；AI 再读原文定状态，改过的写了原因。'
               'CUS 是要问客户的，MAT 是资料要处理的（包括读不了、无法自动判断的文件），ANM 是内部要判断的风险。</p></div></section>')

    # Modules
    out.append('<section class="sheet" aria-label="各模块明细"><div class="block" style="padding-bottom:0"><div class="filter" role="group" aria-label="按状态筛选"><span>只看：</span>')
    out.append('<button type="button" id="f-all" data-f="all" aria-pressed="true">全部</button>')
    for status in ("缺", "薄弱", "有"):
        out.append(f'<button type="button" id="f-{STATUS_CLASS[status]}" data-f="{STATUS_CLASS[status]}" aria-pressed="false">{e(status)}</button>')
    out.append("</div></div>")
    for module, name in MODULES.items():
        mine = [r for r in rows if r["module"] == module]
        if not mine:
            continue
        c = Counter(decided[r["key"]]["status"] for r in mine)
        out.append(f'<div class="block module"><div class="mhead"><span class="code">{e(MODULE_CODES[module])}</span><h2>{e(name)}</h2>'
                   f'<span class="sub">有 {c["有"]} · 薄弱 {c["薄弱"]} · 缺 {c["缺"]}</span></div>')
        out.append('<div class="scroll"><table class="wide"><thead><tr><th>小板块</th><th>状态</th><th>现状</th><th>原文</th></tr></thead><tbody>')
        for r in mine:
            d = decided[r["key"]]
            status = d["status"]
            refs = d.get("sources") or r["sources"][:6]
            reason = f'<span class="why">自动判断为“{e(r["auto_status"])}”，改为“{e(status)}”：{e(d["reason"])}</span>' if status != r["auto_status"] else ""
            how = f'<span class="how">{e(d["how"])}</span>' if status in {"薄弱", "缺"} and d.get("how") else ""
            out.append(f'<tr data-s="{STATUS_CLASS[status]}"><td class="name">{e(r["name"])}</td><td class="st"><span class="chip {STATUS_CLASS[status]}">{e(status)}</span></td>'
                       f'<td>{e(d["note"])}{how}{reason}<div>{links(refs)}</div></td>'
                       f'<td class="mono" style="white-space:nowrap">{r["blocks"]} 段 / {len(r["sources"])} 份</td></tr>')
        out.append("</tbody></table></div></div>")
    out.append("</section>")

    # Ledgers
    explain = {"CUS": "要客户回答或确认的问题。整理成客户确认表发给客户。",
               "MAT": "资料本身要处理的事：读不了、无法自动判断、要识别图片、要补读的页面。",
               "ANM": "内部要判断的风险：品牌和合作方说法、报告页、参考稿、官网的复制粘贴错误。"}
    for ledger, label in LEDGERS.items():
        out.append(f'<section class="sheet block" id="{ledger.lower()}" aria-labelledby="h-{ledger}"><div class="ledger-head"><div class="mhead">'
                   f'<span class="code">{ledger}</span><h2 id="h-{ledger}">{e(label)}</h2><span class="sub">{len(ledgers[ledger])} 项</span></div><p>{e(explain[ledger])}</p></div>')
        if not ledgers[ledger]:
            out.append('<p class="empty">没有事项。</p>')
        for item in ledgers[ledger]:
            meta = "脚本发现，AI 已确认" if item["auto"] else "AI 读原文后补充"
            out.append(f'<div class="ledger-item"><div class="top"><span class="id">{e(item["id"])}</span><b>{e(item["title"])}</b><span class="meta">{meta}</span></div>')
            out.append(f'<p>{e(item["detail"])}</p>')
            if item.get("note"):
                out.append(f'<p><b style="font-size:13px">判断：</b>{e(item["note"])}</p>')
            if item["rows"]:
                has_b = any(r.get("b") for r in item["rows"])
                out.append('<div class="scroll"><table class="wide"><thead><tr><th>项目</th><th>写法 / 内容</th>' + ("<th>对照</th>" if has_b else "") + "</tr></thead><tbody>")
                for r in item["rows"][:15]:
                    a = f'{e(r.get("a", ""))}{" （" + str(r["count"]) + " 处）" if r.get("count") else ""}' + (f'<div class="why">「{e(r["quote"])}」</div>' if r.get("quote") else "")
                    a += f' {source_link(sources.get(r["a_src"]), r["a_src"])}' if r.get("a_src") else ""
                    b = (f'{e(r.get("b", ""))} {source_link(sources.get(r["b_src"]), r["b_src"]) if r.get("b_src") else ""}') if has_b else ""
                    out.append(f'<tr><td>{e(r.get("item", ""))}</td><td>{a}</td>' + (f"<td>{b}</td>" if has_b else "") + "</tr>")
                if len(item["rows"]) > 15:
                    out.append(f'<tr><td colspan="3" class="note">另有 {len(item["rows"]) - 15} 行，见 findings.json</td></tr>')
                out.append("</tbody></table></div>")
            if item["sources"]:
                shown = item["sources"][:24]
                out.append(f'<div>{links(shown)}{" 等 " + str(len(item["sources"])) + " 份" if len(item["sources"]) > 24 else ""}</div>')
            out.append("</div>")
        out.append("</section>")

    # Cleanup
    if findings.get("cleanup"):
        out.append('<section class="sheet block" aria-labelledby="h-clean"><div class="mhead"><h2 id="h-clean">需清理的内容</h2><span class="sub">只计数，不显示原文</span></div>')
        out.append('<p class="note">建知识库或写文章前要删掉这些：报价金额、付款条件，本地文件里的邮箱和电话。官网公开的联系方式不算。</p>')
        out.append('<div class="scroll"><table class="wide"><thead><tr><th>资料</th><th>内容</th></tr></thead><tbody>')
        for c in findings["cleanup"]:
            parts = "、".join(f"{k} {v} 处" for k, v in c.items() if k != "src")
            label = sources.get(c["src"], {}).get("label", "")
            out.append(f'<tr><td>{source_link(sources.get(c["src"]), c["src"])} {e(label)}</td><td>{e(parts)}</td></tr>')
        out.append("</tbody></table></div></section>")

    # Priorities
    out.append('<section class="sheet block" aria-labelledby="h-pri"><div class="mhead"><h2 id="h-pri">建议先补的事</h2></div><ol class="steps">')
    for item in review.get("priorities") or []:
        out.append(f'<li><div><b>{e(item.get("title"))}</b><p>{e(item.get("detail", ""))}</p></div></li>')
    out.append("</ol></section>")

    # Source list
    notes = review.get("source_notes") or {}
    out.append('<section class="sheet block" aria-labelledby="h-src"><div class="mhead"><h2 id="h-src">资料清单</h2>'
               f'<span class="sub">{len(files)} 个文件 · {len(pages)} 个网页</span></div>')
    out.append('<p class="note">“能”= 文字读全了；“部分”= 有的页没有文字或要先清理；“不能”= 无法自动判断内容，原因写在后面，也登记在 MAT。</p>')
    out.append('<div class="scroll"><table class="wide"><thead><tr><th>编号</th><th>资料</th><th>类型</th><th>自动判断</th><th>说明</th></tr></thead><tbody>')
    for s in findings["sources"]:
        judge = s.get("judge", "")
        label = f'<a href="{e(s["label"])}" target="_blank" rel="noopener">{e(s["label"])}</a>' if s["kind"] == "web" and s["label"].startswith("http") else e(s["label"])
        detail = s.get("reason") or (f'{s.get("chars", 0)} 字' + (f'，{s["pages"]} 页' if s.get("pages") else ""))
        note = f'<div class="why">{e(notes[s["id"]])}</div>' if s["id"] in notes else ""
        out.append(f'<tr><td class="mono">{e(s["id"])}</td><td>{label}</td><td style="white-space:nowrap">{e(s["type"])}</td>'
                   f'<td><span class="chip {JUDGE_CLASS.get(judge, "skip")}">{e(judge)}</span></td><td>{e(detail)}{note}</td></tr>')
    out.append("</tbody></table></div></section>")

    out.append('<footer class="foot">')
    out.append("<p>这份报告是建库前的检查，不是知识库本身。框架和 compile-enterprise-knowledge 一致：七个模块、同样的小板块、同样的 CUS/MAT/ANM 分工。"
               "正式建库时先用 prepare-seo-knowledge-materials 筛选资料，再用 compile-enterprise-knowledge 按原文重新计算缺口。</p>")
    out.append(f"<p>报告里只写资料编号和相对路径，不写原文件在电脑上的位置；样例里的金额、邮箱、电话已隐去。由 audit-enterprise-knowledge {VERSION} 生成。</p>")
    out.append("</footer></div>")
    out.append("""<script>
(function () {
  var buttons = document.querySelectorAll('.filter button');
  var rows = document.querySelectorAll('tr[data-s]');
  var modules = document.querySelectorAll('.module');
  function apply(f) {
    buttons.forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.f === f)); });
    rows.forEach(function (r) { r.hidden = !(f === 'all' || r.dataset.s === f); });
    modules.forEach(function (m) { m.hidden = !m.querySelector('tr[data-s]:not([hidden])'); });
  }
  buttons.forEach(function (b) { b.addEventListener('click', function () { apply(b.dataset.f); }); });
})();
</script>""")
    return title, "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate review.json and write the report page.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--config")
    parser.add_argument("--review", help="默认是 <out>/review.json")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    findings = read_json(out / "findings.json")
    if not findings:
        print("没有 findings.json：先运行 analyze.py。", file=sys.stderr)
        return 2
    review = read_json(Path(args.review) if args.review else out / "review.json")
    if not review:
        print("没有 review.json：先按 review-input.md 填写。", file=sys.stderr)
        return 2
    errors = check(findings, review)
    if errors:
        print(f"review.json 有 {len(errors)} 处要改：", file=sys.stderr)
        for error in errors:
            print("  - " + error, file=sys.stderr)
        return 1
    title, body = build(findings, review, load_config(args.config))
    css = (SKILL_DIR / "assets" / "report.css").read_text(encoding="utf-8")
    head = f'<title>{html.escape(title)}</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n{FONTS}\n<style>\n{css}</style>\n'
    (out / "report-artifact.html").write_text(head + body + "\n", encoding="utf-8")
    full = ('<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f"{head}</head>\n<body>\n{body}\n</body>\n</html>\n")
    (out / "report.html").write_text(full, encoding="utf-8")
    print(f"已生成：{out / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
