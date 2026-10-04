"""Languages for the human-readable text the API returns: error messages, decision reasons, rule and
scorecard texts, model explanations, the deterministic agent's report and receipt notes.

Machine-readable fields never change with the language: error `code`s, reason `code`s, rule ids,
`required_actions`, enum values. Integrations branch on those; only the text meant for people is translated.

How it works (gettext style): the English text *is* the message id. Each locale's catalog
(payguard/locales/<locale>.json) maps an English message, or an English template such as
"case already resolved as {resolution}", to its translation. `translate` translates any English string
the platform produced, including ones stored in the database before a translation existed: an exact match
first, then the templates, whose placeholders are themselves translated ("fraud" -> "fraude"). Text with no
translation (e.g. a free-form report written by the LLM agent) is returned unchanged, in English.

Which language: `?lang=` on the request, else the API client's profile locale, else the request's
Accept-Language, else the deployment default (PAYGUARD_DEFAULT_LOCALE).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# Native names, shown in language pickers. Order is the picker order.
LOCALES: dict[str, str] = {
    "en": "English",
    "fr": "Français",
    "yo": "Yorùbá",
    "ha": "Hausa",
    "ig": "Igbo",
    "pcm": "Naijá (Pidgin)",
}
DEFAULT = "en"
_DIR = Path(__file__).parent / "locales"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def normalize(tag: str | None) -> str | None:
    """'fr-CA' -> 'fr', 'yo_NG' -> 'yo', 'PCM' -> 'pcm'; None if unsupported."""
    if not tag:
        return None
    t = tag.strip().replace("_", "-").lower()
    if t in LOCALES:
        return t
    primary = t.split("-")[0]
    return primary if primary in LOCALES else None


def negotiate(accept_language: str | None) -> str | None:
    """Best supported locale from an Accept-Language header (q-values honoured), or None."""
    if not accept_language:
        return None
    ranked = []
    for i, part in enumerate(accept_language.split(",")):
        tag, _, params = part.strip().partition(";")
        q = 1.0
        m = re.search(r"q=([0-9.]+)", params)
        if m:
            try:
                q = float(m.group(1))
            except ValueError:
                q = 0.0
        loc = normalize(tag)
        if loc and q > 0:
            ranked.append((-q, i, loc))
    return min(ranked)[2] if ranked else None


@lru_cache
def msgids() -> tuple[str, ...]:
    return tuple(json.loads((_DIR / "en.json").read_text(encoding="utf-8")))


@lru_cache
def catalog(locale: str) -> dict[str, str]:
    if locale == DEFAULT:
        return {}
    path = _DIR / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@lru_cache
def _templates() -> list[tuple[re.Pattern, str]]:
    """Every message id with placeholders, as an anchored regex. Most specific (most literal text) first, so
    "Processor signal {name} = {value}" wins over a shorter, more generic template."""
    out = []
    for mid in msgids():
        if not _PLACEHOLDER.search(mid):
            continue
        parts, last = [], 0
        for m in _PLACEHOLDER.finditer(mid):
            parts.append(re.escape(mid[last:m.start()]))
            parts.append(f"(?P<{m.group(1)}>.+?)")
            last = m.end()
        parts.append(re.escape(mid[last:]))
        literal = len(_PLACEHOLDER.sub("", mid))
        out.append((literal, re.compile("".join(parts), re.S), mid))
    return [(rx, mid) for _, rx, mid in sorted(out, key=lambda t: -t[0])]


def translate(text, locale: str | None):
    """Translate English text produced by the platform. Non-strings and untranslatable text pass through."""
    if not isinstance(text, str) or not text or not locale or locale == DEFAULT:
        return text
    cat = catalog(locale)
    if not cat:
        return text
    hit = cat.get(text)
    if hit is not None:
        return hit
    for rx, mid in _templates():
        m = rx.fullmatch(text)
        if m and mid in cat:
            try:
                return cat[mid].format(**{k: translate(v, locale) for k, v in m.groupdict().items()})
            except (KeyError, IndexError, ValueError):
                return text
    return text


def t(msgid: str, locale: str | None, **params) -> str:
    """Render a message id in a locale (English if untranslated)."""
    template = catalog(locale).get(msgid, msgid) if locale and locale != DEFAULT else msgid
    return template.format(**params) if params else template
