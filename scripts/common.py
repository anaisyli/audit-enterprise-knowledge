"""Shared helpers for audit-enterprise-knowledge. Standard library only."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# Windows consoles default to GBK; print Chinese as UTF-8 so logs stay readable everywhere.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SKILL_DIR = Path(__file__).resolve().parent.parent
VERSION = "1.0.0"

# Same seven modules as compile-enterprise-knowledge, so a later build lines up with this audit.
MODULES = {
    "company": "公司概述",
    "product": "产品介绍",
    "solution": "解决方案",
    "case": "合作案例",
    "industry": "行业知识",
    "faq": "FAQ",
    "other": "其他",
}
MODULE_CODES = {
    "company": "10_公司概述",
    "product": "20_产品介绍",
    "solution": "30_解决方案",
    "case": "40_合作案例",
    "industry": "50_行业知识与洞察",
    "faq": "60_FAQ",
    "other": "70_其他",
}
STATUSES = ["有", "薄弱", "缺", "决定不写"]
LEDGERS = {
    "CUS": "待客户补充事项",
    "MAT": "源资料处理台账",
    "ANM": "源文与事实异常台账",
}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)\d{3,4}[\s-]?\d{3,4}(?!\d)")
MONEY = re.compile(
    r"(?i)(?:usd|us\$|rmb|cny|eur|gbp|\$|¥|￥|€|£)\s?\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:usd|rmb|元|美元|欧元)(?![a-z])"
    r"|(?:单价|报价|价格|unit price|price)\s*[:：]?\s*\d[\d,]*(?:\.\d+)?"
)
PAYMENT = re.compile(r"(?i)\bt/t\b|\bl/c\b|\btt advance|定金|尾款|付款方式|payment terms?|deposit\b")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def key_of(text: str) -> str:
    """Duplicate check: case, spaces and punctuation do not count."""
    squeezed = re.sub(r"[\W_]+", "", text.casefold())
    return hashlib.sha1(squeezed.encode("utf-8")).hexdigest()[:16]


def short_hash(text: str, size: int = 8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:size]


def mask(text: str) -> str:
    """Hide prices, payment terms, e-mails and phone numbers in excerpts shown to people."""
    text = EMAIL.sub("〔邮箱已隐去〕", text)
    text = MONEY.sub("〔金额已隐去〕", text)
    text = PHONE.sub(lambda m: m.group(0) if len(re.sub(r"\D", "", m.group(0))) < 8 else "〔电话已隐去〕", text)
    return text


def around(text: str, match: "re.Match[str]", size: int = 140) -> str:
    """The part of a long paragraph around a match, so excerpts show why it was picked."""
    text = norm(text)
    if len(text) <= size:
        return text
    start = max(0, match.start() - size // 3)
    piece = text[start: start + size]
    return ("…" if start else "") + piece + ("…" if start + size < len(text) else "")


def clip(text: str, size: int = 160) -> str:
    text = norm(text)
    return text if len(text) <= size else text[: size - 1] + "…"


_KEYWORD_CACHE: dict[str, re.Pattern[str]] = {}


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """ASCII keywords align to the start of a word (certif -> certificate, but not test -> latest)."""
    pattern = _KEYWORD_CACHE.get(keyword)
    if pattern is None:
        word = keyword.casefold()
        if re.fullmatch(r"[\x00-\x7f]+", word) and re.match(r"[a-z0-9]", word):
            pattern = re.compile(r"(?<![a-z0-9])" + re.escape(word))
        else:
            pattern = re.compile(re.escape(word))
        _KEYWORD_CACHE[keyword] = pattern
    return pattern


def matches(text_cf: str, keywords: list[str]) -> bool:
    return any(keyword_pattern(k).search(text_cf) for k in keywords)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    data = read_json(Path(path), {})
    if not isinstance(data, dict):
        raise SystemExit(f"配置文件不是 JSON 对象：{path}")
    return data


def load_sections(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Default sub-sections, then the project's own replacements and extra keywords."""
    sections = read_json(SKILL_DIR / "assets" / "sections-default.json")
    sections = {k: v for k, v in sections.items() if k in MODULES}
    for module, items in (config.get("sections") or {}).items():
        if module not in MODULES:
            continue
        current = {item["name"]: item for item in sections.get(module, [])}
        for item in items:
            current[item["name"]] = item
        sections[module] = list(current.values())
    for label, words in (config.get("extra_keywords") or {}).items():
        module, _, name = label.partition("/")
        for item in sections.get(module, []):
            if item["name"] == name:
                item["keywords"] = list(dict.fromkeys(item["keywords"] + list(words)))
    return sections


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
