#!/usr/bin/env python3
"""
Sales Intelligence — täglicher Magazin-Generator.

Erzeugt den Tages-Schwerpunkt für sales-intelligence.b77.de:

  1. ruft Claude (Sonnet) für den redaktionellen Inhalt (strukturiertes JSON),
  2. rendert daraus on-brand SVG-Illustrationen + HTML deterministisch,
  3. schreibt die Standalone-Artikel-HTML (aus article-template.html),
  4. ergänzt den Eintrag oben in articles.json,
  5. spleißt Cover/Teaser + eingebetteten Schwerpunkt in index.html
     (zwischen den SI:*-Markern) und aktualisiert das „Stand ·"-Datum.

Die KI schreibt NUR Text/Zahlen — alle Koordinaten/Markup kommen aus den
Templates hier. Das hält die Ausgabe pixelgenau im bestehenden Design.

Aufrufe:
    python generate.py                # echter Lauf (braucht ANTHROPIC_API_KEY)
    python generate.py --sample       # offline, mit eingebautem Beispiel-Inhalt
    python generate.py --from-json x  # rendert aus vorhandenem JSON (Debug)
    python generate.py --date 2026-06-09   # Datum überschreiben (Test)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# Pfade & Konstanten
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_JSON = ROOT / "articles.json"
INDEX_HTML = ROOT / "index.html"
ARTICLE_TEMPLATE = ROOT / "article-template.html"
ARTICLES_DIR = ROOT / "articles"

MODEL = os.environ.get("SI_MODEL", "claude-sonnet-4-6")
BERLIN = ZoneInfo("Europe/Berlin")
ACCENT = "#5DEAFF"          # Marken-Cyan
ACCENT_DEEP = "#0096C7"

# Text-to-Speech (OpenAI). Optional: ohne OPENAI_API_KEY wird kein Audio
# erzeugt und die Artikelseite nutzt die Browser-Vorlesefunktion.
TTS_MODEL = os.environ.get("SI_TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.environ.get("SI_TTS_VOICE", "onyx")
TTS_SPEED = float(os.environ.get("SI_TTS_SPEED", "1.12"))  # via ffmpeg atempo
TTS_INSTRUCTIONS = (
    "Lies den Text sachlich und deutlich auf Hochdeutsch — im Stil eines "
    "seriösen Wirtschaftsmagazins."
)

MONTHS_DE = [
    "", "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

# --------------------------------------------------------------------------
# Kleine Hilfen
# --------------------------------------------------------------------------


def svg_text(s: str) -> str:
    """Escaped Text für SVG (kein Markup erlaubt)."""
    s = (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace("·", "&#183;")


def html_text(s: str) -> str:
    """Body-HTML: erlaubt <em>, escaped aber nackte Ampersands."""
    return re.sub(r"&(?!#?\w+;)", "&amp;", s or "")


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def german_date(d: datetime) -> str:
    return f"{d.day}. {MONTHS_DE[d.month]} {d.year}"


def replace_between(text: str, start: str, end: str, inner: str) -> str:
    """Ersetzt den Inhalt zwischen zwei Markern (Marker bleiben erhalten)."""
    i = text.find(start)
    j = text.find(end)
    if i == -1 or j == -1 or j < i:
        raise RuntimeError(f"Marker nicht gefunden: {start!r} … {end!r}")
    i += len(start)
    return text[:i] + inner + text[j:]


# --------------------------------------------------------------------------
# SVG-Templates (Text-Slots werden befüllt, Layout ist fix)
# --------------------------------------------------------------------------


def build_cover_svg(c: dict, date_display: str, color: str) -> str:
    """600x380-Karte für articles.json / Archiv-Grid."""
    eyebrow = svg_text(f"{c.get('eyebrow_caps', 'SCHWERPUNKT')} · {date_display.upper()}")
    return (
        '<svg viewBox="0 0 600 380" preserveAspectRatio="xMidYMid slice" width="100%" height="100%">'
        f'<rect width="600" height="380" fill="{color}"/>'
        f'<text x="40" y="60" font-family="Helvetica" font-size="11" fill="#0a0a0a" letter-spacing="3" font-weight="600">{eyebrow}</text>'
        f'<text x="40" y="185" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-weight="900" font-size="90" fill="#0a0a0a" letter-spacing="-3">{svg_text(c["kpi"])}</text>'
        f'<text x="40" y="220" font-family="Helvetica" font-size="13" fill="#0a0a0a" letter-spacing="2">{svg_text(c["caps_line"])}</text>'
        '<line x1="40" y1="248" x2="560" y2="248" stroke="#0a0a0a" opacity="0.2"/>'
        '<line x1="40" y1="248" x2="120" y2="248" stroke="#0a0a0a" stroke-width="2"/>'
        f'<text x="40" y="293" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-size="22" fill="#0a0a0a">{svg_text(c["italic1"])}</text>'
        f'<text x="40" y="321" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-size="22" fill="#0a0a0a">{svg_text(c["italic2"])}</text>'
        f'<rect x="40" y="338" width="20" height="20" fill="{ACCENT_DEEP}"/>'
        f'<text x="72" y="354" font-family="Helvetica" font-size="11" fill="#0a0a0a" letter-spacing="3" font-weight="600">{svg_text(c["bottom_caps"])}</text>'
        "</svg>"
    )


def build_hero_svg(h: dict, gid: str, color: str) -> str:
    """1280x720 Titel-Illustration (Standalone + eingebetteter Schwerpunkt)."""
    big = h.get("big_number", "")
    if len(big) <= 1:
        big_size, units_x = 210, 250
    elif len(big) == 2:
        big_size, units_x = 200, 330
    else:
        big_size, units_x = 150, 360
    return (
        '<svg viewBox="0 0 1280 720" preserveAspectRatio="xMidYMid slice" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">\n'
        "  <defs>\n"
        f'    <pattern id="{gid}" x="0" y="0" width="40" height="40" patternUnits="userSpaceOnUse">\n'
        f'      <rect width="40" height="40" fill="{color}"/>\n'
        '      <line x1="0" y1="0" x2="40" y2="0" stroke="#0a0a0a" stroke-width="0.5" opacity="0.18"/>\n'
        '      <line x1="0" y1="0" x2="0" y2="40" stroke="#0a0a0a" stroke-width="0.5" opacity="0.18"/>\n'
        "    </pattern>\n"
        "  </defs>\n"
        f'  <rect width="1280" height="720" fill="url(#{gid})"/>\n'
        f'  <text x="80" y="110" font-family="Helvetica" font-size="13" fill="#0a0a0a" letter-spacing="6" font-weight="600">{svg_text(h["top_caps"])}</text>\n'
        f'  <text x="80" y="320" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-weight="400" font-size="{big_size}" fill="#0a0a0a" letter-spacing="-8">{svg_text(big)}</text>\n'
        f'  <text x="{units_x}" y="320" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-weight="400" font-size="150" fill="#0a0a0a" letter-spacing="-4">{svg_text(h.get("big_units", "/ 100"))}</text>\n'
        f'  <text x="80" y="372" font-family="Helvetica" font-size="13" fill="#0a0a0a" letter-spacing="3" font-weight="600">{svg_text(h["caption_caps"])}</text>\n'
        '  <line x1="80" y1="414" x2="1200" y2="414" stroke="#0a0a0a" opacity="0.2"/>\n'
        '  <line x1="80" y1="414" x2="200" y2="414" stroke="#0a0a0a" stroke-width="2"/>\n'
        f'  <text x="80" y="472" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-size="28" fill="#0a0a0a">{svg_text(h["italic1"])}</text>\n'
        f'  <text x="80" y="510" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-size="28" fill="#0a0a0a">{svg_text(h["italic2"])}</text>\n'
        '  <rect x="80" y="559" width="430" height="108" fill="#0a0a0a"/>\n'
        f'  <text x="295" y="604" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-size="36" fill="{ACCENT}" text-anchor="middle">{svg_text(h["box_value"])}</text>\n'
        f'  <text x="295" y="638" font-family="Helvetica" font-size="11" fill="{ACCENT}" text-anchor="middle" letter-spacing="3" font-weight="600">{svg_text(h["box_caps"])}</text>\n'
        f'  <text x="560" y="588" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-size="20" fill="#0a0a0a">{svg_text(h["right1"])}</text>\n'
        f'  <text x="560" y="618" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-size="20" fill="#0a0a0a">{svg_text(h["right2"])}</text>\n'
        f'  <text x="560" y="648" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-size="20" fill="#0a0a0a">{svg_text(h.get("right3", ""))}</text>\n'
        f'  <text x="80" y="703" font-family="Helvetica" font-size="13" fill="#0a0a0a" letter-spacing="3" font-weight="500">{svg_text(h["source_caps"])}</text>\n'
        "</svg>"
    )


def build_inline_fig(fig: dict) -> str:
    cols = fig.get("cols", [])[:3]
    parts = [
        '    <div class="inline-fig">',
        '      <svg viewBox="0 0 1000 300" width="100%" xmlns="http://www.w3.org/2000/svg">',
        f'        <text x="0" y="18" font-family="Helvetica" font-size="12" fill="{ACCENT_DEEP}" letter-spacing="3" font-weight="600">{svg_text(fig["title_caps"])}</text>',
        '        <line x1="0" y1="38" x2="1000" y2="38" stroke="#0a0a0a" opacity="0.25"/>',
        '        <line x1="330" y1="62" x2="330" y2="288" stroke="#0a0a0a" opacity="0.15"/>',
        '        <line x1="690" y1="62" x2="690" y2="288" stroke="#0a0a0a" opacity="0.15"/>',
    ]
    line_ys = [214, 236, 258, 280]
    for i, col in enumerate(cols):
        x = [0, 360, 720][i]
        parts.append(f'        <text x="{x}" y="138" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-size="80" fill="#0a0a0a">{svg_text(col.get("num", ""))}</text>')
        parts.append(f'        <text x="{x}" y="178" font-family="Iowan Old Style, Georgia, serif" font-size="21" fill="#0a0a0a">{svg_text(col.get("head", ""))}</text>')
        for ly, line in zip(line_ys, col.get("lines", [])[:4]):
            parts.append(f'        <text x="{x}" y="{ly}" font-family="Iowan Old Style, Georgia, serif" font-size="15" fill="#2a2a2a">{svg_text(line)}</text>')
    parts.append("      </svg>")
    if fig.get("cap"):
        parts.append(f'      <div class="cap">{svg_text(fig["cap"])}</div>')
    parts.append("    </div>")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Body-Assemblierung
# --------------------------------------------------------------------------


def build_body_html(data: dict, date_display: str) -> str:
    out = []
    for block in data["body"]:
        t = block.get("type")
        if t == "p":
            out.append(f'    <p>{html_text(block["html"])}</p>')
        elif t == "h3":
            out.append(f'    <h3>{html_text(block["html"])}</h3>')
        elif t == "pullquote":
            src = block.get("source") or f"Editorial · Sales Intelligence · {date_display}"
            out.append(
                '    <div class="pullquote">\n'
                f'      {html_text(block["html"])}\n'
                f'      <span class="source">{svg_text(src)}</span>\n'
                "    </div>"
            )
        elif t == "inline_fig":
            out.append(build_inline_fig(block))
        else:
            raise RuntimeError(f"Unbekannter Block-Typ: {t!r}")
    # Abschluss-Byline mit Quellen
    sources = svg_text(data.get("sources", ""))
    out.append(
        '    <div class="byline-end">\n'
        f'      <span>Redaktion &#183; Sales Intelligence &#183; {date_display}</span>\n'
        f'      <span>Quellen &#183; {sources}</span>\n'
        "    </div>"
    )
    return "\n\n".join(out)


# --------------------------------------------------------------------------
# index.html-Blöcke
# --------------------------------------------------------------------------


def build_featured(data: dict, date_display: str, url: str) -> str:
    cov = data["cover"]
    return f"""
  <section class="featured">
    <div class="cover-card">
      <div class="cover-inner">
        <div class="cover-top">
          <span>Briefing · {date_display}</span>
          <span>kostenfrei</span>
        </div>
        <div class="brand-mark">Sales <em>Intelligence</em></div>
        <div class="issue-title">
          <svg class="curved-title" viewBox="0 0 360 360" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <path id="circle" d="M 180,180 m -135,0 a 135,135 0 1,1 270,0 a 135,135 0 1,1 -270,0" />
            </defs>
            <text fill="#ffffff" font-family="Iowan Old Style, Georgia, serif" font-size="24" font-weight="400">
              <textPath href="#circle" startOffset="2%">{svg_text(cov["curved_title"])}</textPath>
            </text>
            <text x="180" y="160" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-weight="400" font-size="46" fill="#ffffff" text-anchor="middle">{svg_text(cov["kpi"])}</text>
            <text x="180" y="200" font-family="Iowan Old Style, Georgia, serif" font-style="italic" font-weight="400" font-size="20" fill="#ffffff" text-anchor="middle">{svg_text(cov["subtitle"])}</text>
          </svg>
        </div>
        <div class="cover-bottom">sales-intel.de · KI im Vertrieb · Mittelstand-Edition</div>
      </div>
    </div>

    <div class="featured-text">
      <div class="kicker">Die aktuelle Sales Intelligence · {date_display}</div>
      <h2>{html_text(data["title_html"])}</h2>
      <p class="lead">{html_text(data["deck"])}</p>
      <a class="cta" id="latestArticleCta" href="{url}">Schwerpunkt lesen</a>
    </div>
  </section>
"""


def build_schwerpunkt(data: dict, date_display: str, hero_svg: str, body_html: str) -> str:
    return f"""
<section class="page" id="page-schwerpunkt">
  <div class="article-hero">
    <div class="meta-line">{data["eyebrow"]} · {date_display} · Lesezeit {data["reading_time"]}</div>
    <h1>{html_text(data["title_html"])}</h1>
    <p class="standfirst">{html_text(data["deck"])}</p>
    <div class="byline">
      <span>Redaktion · Sales Intelligence</span>
      <span>{date_display} · Mittelstand-Edition</span>
    </div>
  </div>

    <div class="article-image" style="background: var(--issue);">
{hero_svg}
  </div>

  <div class="article-body longform">
{body_html}
  </div>
</section>
"""


# --------------------------------------------------------------------------
# Claude-Aufruf
# --------------------------------------------------------------------------

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string", "description": "kurzer kebab-case-Slug, nur a-z0-9-, ohne Datum"},
        "eyebrow": {"type": "string", "description": "Rubrik, i.d.R. 'Schwerpunkt'"},
        "title_html": {"type": "string", "description": "Schlagzeile, genau ein <em>…</em> für die Kernaussage"},
        "deck": {"type": "string", "description": "2–3 Sätze Vorspann/Standfirst"},
        "reading_time": {"type": "string", "description": "z.B. '6 Minuten'"},
        "kpi": {"type": "string", "description": "Leitkennzahl, z.B. '88 / 100' oder '41 %'"},
        "color": {"type": "string", "description": "Akzentfarbe Hex, Standard #5DEAFF"},
        "sources": {"type": "string", "description": "Quellenzeile, ' · '-getrennt, mit Jahreszahlen"},
        "cover": {
            "type": "object",
            "properties": {
                "curved_title": {"type": "string", "description": "ein Satz, läuft um den Kreis"},
                "kpi": {"type": "string"},
                "subtitle": {"type": "string", "description": "eine kurze Zeile unter der KPI"},
            },
            "required": ["curved_title", "kpi", "subtitle"],
        },
        "cover_svg": {
            "type": "object",
            "properties": {
                "eyebrow_caps": {"type": "string", "description": "GROSS, z.B. 'SCHWERPUNKT' (Datum wird ergänzt)"},
                "kpi": {"type": "string"},
                "caps_line": {"type": "string", "description": "GROSS, eine Zeile"},
                "italic1": {"type": "string"},
                "italic2": {"type": "string"},
                "bottom_caps": {"type": "string", "description": "GROSS, Schlagworte mit ·"},
            },
            "required": ["kpi", "caps_line", "italic1", "italic2", "bottom_caps"],
        },
        "hero_svg": {
            "type": "object",
            "properties": {
                "top_caps": {"type": "string", "description": "GROSS, Themazeile"},
                "big_number": {"type": "string", "description": "die große Zahl, z.B. '88'"},
                "big_units": {"type": "string", "description": "Einheit, z.B. '/ 100' oder '%'"},
                "caption_caps": {"type": "string", "description": "GROSS, Bildunterzeile mit Quelle"},
                "italic1": {"type": "string"},
                "italic2": {"type": "string"},
                "box_value": {"type": "string", "description": "Kennzahl im schwarzen Kasten"},
                "box_caps": {"type": "string", "description": "GROSS, Label im Kasten"},
                "right1": {"type": "string"},
                "right2": {"type": "string"},
                "right3": {"type": "string"},
                "source_caps": {"type": "string", "description": "GROSS, 'QUELLE · …'"},
            },
            "required": ["top_caps", "big_number", "big_units", "caption_caps",
                         "italic1", "italic2", "box_value", "box_caps",
                         "right1", "right2", "right3", "source_caps"],
        },
        "body": {
            "type": "array",
            "description": "geordnete Blöcke; idealerweise p, h3, p, pullquote, h3, inline_fig, p, p, h3, p, p",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["p", "h3", "pullquote", "inline_fig"]},
                    "html": {"type": "string", "description": "für p/h3/pullquote: Text, <em> erlaubt"},
                    "source": {"type": "string", "description": "nur pullquote"},
                    "title_caps": {"type": "string", "description": "nur inline_fig, GROSS"},
                    "cap": {"type": "string", "description": "nur inline_fig, Bildunterschrift"},
                    "cols": {
                        "type": "array",
                        "description": "nur inline_fig, genau 3 Spalten",
                        "items": {
                            "type": "object",
                            "properties": {
                                "num": {"type": "string", "description": "'01'/'02'/'03'"},
                                "head": {"type": "string"},
                                "lines": {"type": "array", "items": {"type": "string"}, "description": "max 4 kurze Zeilen"},
                            },
                            "required": ["num", "head", "lines"],
                        },
                    },
                },
                "required": ["type"],
            },
        },
    },
    "required": ["slug", "eyebrow", "title_html", "deck", "reading_time", "kpi",
                 "sources", "cover", "cover_svg", "hero_svg", "body"],
}


def build_prompt(date_display: str, recent: list[dict]) -> str:
    recent_lines = "\n".join(f"- {strip_tags(a['title'])}" for a in recent[:14])
    return f"""Du bist Chefredakteur von „Sales Intelligence", einem werktäglichen Fachmagazin \
für KI im Vertrieb des deutschen Mittelstands (Herausgeber: B_77 & Quoodix). \
Schreibe den Tagesschwerpunkt für den {date_display}.

TONALITÄT: nüchtern, präzise, deutsches Wirtschaftsdeutsch (FAZ/Handelsblatt-Niveau). \
Keine Marketing-Floskeln, kein Hype, keine Emojis. Du schreibst für Geschäftsführer und \
Vertriebsleiter im Mittelstand (10–500 Mitarbeiter).

THEMA: ein konkreter, aktueller Aspekt von KI im B2B-Vertrieb / Mittelstand 2026 \
(z.B. Forecasting, Lead-Qualifizierung, CRM-Datenqualität, Agentic AI, Angebots-Automatisierung, \
Gesprächsanalyse, Pricing, Pilot-zu-Produktiv, Change-Management). Wähle ein Thema, das sich \
klar von diesen zuletzt erschienenen Beiträgen unterscheidet:
{recent_lines}

INHALT: ~1.200–1.500 Wörter, gegliedert in 3–4 Zwischenüberschriften (h3), mit genau einem \
Pullquote und genau einer inline_fig (3-Spalten-Grafik). Verwende belastbare, real existierende \
Statistiken mit benannten Quellen (Gartner, Bitkom, BCG, McKinsey, Forrester, Salesforce, IDC etc.) \
und realistischen Werten für 2025/2026 — KEINE erfundenen Präzisionszahlen. Wenn du eine Zahl nicht \
sicher belegen kannst, formuliere sie als Größenordnung. Die Leitkennzahl (kpi/big_number) soll \
einprägsam sein und im Text hergeleitet werden.

FORM: Schlagzeile mit genau einem <em>…</em>. Im Fließtext sparsam <em> für Betonung. \
Alle GROSS-Felder wirklich in Großbuchstaben. Halte SVG-Textzeilen kurz (Cover-Italics ≤ ~48 Zeichen, \
Hero-Italics ≤ ~62 Zeichen, inline_fig-Zeilen ≤ ~30 Zeichen), damit nichts aus dem Rahmen läuft. \
Akzentfarbe #5DEAFF. Gib alles über das Werkzeug submit_article aus."""


def call_claude(date_display: str, recent: list[dict]) -> dict:
    from anthropic import Anthropic

    client = Anthropic()  # liest ANTHROPIC_API_KEY
    tool = {
        "name": "submit_article",
        "description": "Gibt den fertigen Tagesschwerpunkt strukturiert zurück.",
        "input_schema": ARTICLE_SCHEMA,
    }
    msg = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_article"},
        messages=[{"role": "user", "content": build_prompt(date_display, recent)}],
    )
    for block in msg.content:
        if block.type == "tool_use" and block.name == "submit_article":
            return block.input
    raise RuntimeError("Claude hat kein submit_article zurückgegeben.")


# --------------------------------------------------------------------------
# Zusammenbau & Schreiben
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Vorlese-Audio (OpenAI TTS)
# --------------------------------------------------------------------------


def build_narration(data: dict) -> str:
    """Reiner Vorlesetext: Schlagzeile, Vorspann, Fließtext (ohne Grafiken)."""
    parts = [strip_tags(data["title_html"]), strip_tags(data["deck"])]
    for block in data["body"]:
        if block.get("type") in ("p", "h3", "pullquote"):
            parts.append(strip_tags(block["html"]))
    return "\n\n".join(p for p in parts if p)


def _split_text(text: str, limit: int = 3500) -> list[str]:
    """Zerlegt den Text in Stücke <= limit Zeichen (OpenAI-Grenze 4096)."""
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if len(para) > limit:
            for sent in re.split(r"(?<=[.!?]) ", para):
                if cur and len(cur) + len(sent) + 1 > limit:
                    chunks.append(cur.strip()); cur = ""
                cur += sent + " "
        else:
            if cur and len(cur) + len(para) + 2 > limit:
                chunks.append(cur.strip()); cur = ""
            cur += para + "\n\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def _tts_chunk(text: str, key: str, model: str) -> bytes:
    body = {"model": model, "voice": TTS_VOICE, "input": text, "response_format": "mp3"}
    if model.startswith("gpt-4o"):
        body["instructions"] = TTS_INSTRUCTIONS
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {e.code}: {detail}")


def _speed_up(mp3: bytes, factor: float) -> bytes:
    """Beschleunigt die MP3 tonhöhen-erhaltend per ffmpeg (atempo).
    Ohne ffmpeg oder bei Fehler: Original zurück (kein Abbruch)."""
    if abs(factor - 1.0) < 0.01 or not shutil.which("ffmpeg"):
        return mp3
    try:
        p = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
             "-filter:a", f"atempo={factor}", "-b:a", "128k", "-f", "mp3", "pipe:1"],
            input=mp3, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        return p.stdout or mp3
    except Exception as e:  # noqa: BLE001
        print(f"[TTS] ffmpeg-Tempo übersprungen: {e}", file=sys.stderr)
        return mp3


def synthesize_audio(narration: str, out_path: Path) -> bool:
    """Erzeugt eine MP3. Fehlt der Key oder schlägt es fehl: False (kein Abbruch).
    Bei Modell-Problemen automatischer Fallback auf tts-1. Tempo via ffmpeg."""
    synthesize_audio.last_error = None
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("[TTS] OPENAI_API_KEY nicht gesetzt — überspringe Audio (Browser-Vorlesen bleibt).")
        return False
    parts = _split_text(narration)
    models = [TTS_MODEL] + (["tts-1"] if TTS_MODEL != "tts-1" else [])
    for model in models:
        try:
            audio = b"".join(_tts_chunk(p, key, model) for p in parts)
            audio = _speed_up(audio, TTS_SPEED)
            out_path.write_bytes(audio)
            print(f"[TTS] Audio: {out_path.name} via {model} x{TTS_SPEED} ({len(parts)} Seg., {len(audio)//1024} KB)")
            return True
        except Exception as e:  # noqa: BLE001
            synthesize_audio.last_error = f"{model}: {e}"
            print(f"[TTS] FEHLER ({model}): {e}", file=sys.stderr)
            # Bei Quota/Auth-Fehlern bringt der Fallback nichts → abbrechen.
            if not any(s in str(e) for s in ("400", "404", "model", "invalid_request")):
                break
    return False


synthesize_audio.last_error = None


def render_and_write(data: dict, run_date: datetime) -> dict:
    date = run_date.strftime("%Y-%m-%d")
    date_display = german_date(run_date)
    iso_date = run_date.replace(hour=6, minute=0, second=0, microsecond=0).isoformat()
    color = data.get("color") or ACCENT
    slug = re.sub(r"[^a-z0-9-]", "", data["slug"].lower())
    filename = f"{date}-{slug}.html"
    url = f"/articles/{filename}"
    gid = f"grid{date.replace('-', '')}"

    hero_svg = build_hero_svg(data["hero_svg"], gid, color)
    body_html = build_body_html(data, date_display)
    cover_svg = build_cover_svg(data["cover_svg"], date_display, color)

    # 0) Vorlese-Audio (optional)
    ARTICLES_DIR.mkdir(exist_ok=True)
    audio_name = f"{date}-{slug}.mp3"
    audio_ok = synthesize_audio(build_narration(data), ARTICLES_DIR / audio_name)
    audio_tag = (
        f'<audio id="ra-audio" src="/articles/{audio_name}" preload="none"></audio>'
        if audio_ok else ""
    )

    # 1) Standalone-Artikel aus Template
    tpl = ARTICLE_TEMPLATE.read_text(encoding="utf-8")
    repl = {
        "AUDIO_TAG": audio_tag,
        "TITLE_PLAIN": strip_tags(data["title_html"]),
        "DECK_PLAIN": strip_tags(data["deck"]),
        "URL": url,
        "ISO_DATE": iso_date,
        "EYEBROW": data["eyebrow"],
        "DATE_DISPLAY": date_display,
        "READING_TIME": data["reading_time"],
        "TITLE_HTML": html_text(data["title_html"]),
        "STANDFIRST": html_text(data["deck"]),
        "HERO_SVG": hero_svg,
        "BODY_HTML": body_html,
        "SLUG": slug,
    }
    for k, v in repl.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    ARTICLES_DIR.mkdir(exist_ok=True)
    (ARTICLES_DIR / filename).write_text(tpl, encoding="utf-8")

    # 2) articles.json — neuen Eintrag vorne einfügen
    articles = json.loads(ARTICLES_JSON.read_text(encoding="utf-8"))
    entry = {
        "slug": slug,
        "date": date,
        "date_display": date_display,
        "iso_date": iso_date,
        "url": url,
        "title": html_text(data["title_html"]),
        "deck": html_text(data["deck"]),
        "eyebrow": data["eyebrow"],
        "kpi": data["kpi"],
        "color": color,
        "cover_svg": cover_svg,
        "reading_time": data["reading_time"],
    }
    articles = [a for a in articles if a.get("date") != date]  # idempotent
    articles.insert(0, entry)
    ARTICLES_JSON.write_text(
        json.dumps(articles, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
    )

    # 3) index.html — Blöcke zwischen den Markern ersetzen
    idx = INDEX_HTML.read_text(encoding="utf-8")
    idx = replace_between(
        idx,
        "<!-- SI:FEATURED:START — automatisch täglich generiert, nicht von Hand bearbeiten -->",
        "  <!-- SI:FEATURED:END -->",
        build_featured(data, date_display, url),
    )
    idx = replace_between(
        idx,
        "<!-- SI:SCHWERPUNKT:START — automatisch täglich generiert, nicht von Hand bearbeiten -->",
        "<!-- SI:SCHWERPUNKT:END -->",
        build_schwerpunkt(data, date_display, hero_svg, body_html),
    )
    idx = replace_between(idx, "<!-- SI:STAND:START -->", "<!-- SI:STAND:END -->", date_display)
    INDEX_HTML.write_text(idx, encoding="utf-8")

    return {"date": date, "title": strip_tags(data["title_html"]), "url": url, "file": filename}


# --------------------------------------------------------------------------
# Beispiel-Inhalt für Offline-Tests (--sample)
# --------------------------------------------------------------------------

SAMPLE = {
    "slug": "datenqualitaet-entscheidet-ki-vertrieb",
    "eyebrow": "Schwerpunkt",
    "title_html": "Die unsichtbare Bremse. Warum <em>Datenqualität</em> über jedes KI-Projekt im Vertrieb entscheidet.",
    "deck": "KI-Werkzeuge im Vertrieb versprechen Tempo und Treffsicherheit. Doch die Hälfte der Projekte scheitert, bevor sie wirkt — nicht am Modell, sondern an den Daten, die es füttern. Eine Bestandsaufnahme für den Mittelstand.",
    "reading_time": "6 Minuten",
    "kpi": "60 %",
    "color": "#5DEAFF",
    "sources": "Gartner 2025 · Bitkom 2026 · McKinsey State of AI 2025 · Forrester 2026",
    "cover": {
        "curved_title": "Datenqualität im Vertrieb. Die unsichtbare Bremse jedes KI-Projekts.",
        "kpi": "60 %",
        "subtitle": "scheitern an Daten, nicht am Modell.",
    },
    "cover_svg": {
        "eyebrow_caps": "SCHWERPUNKT",
        "kpi": "60 %",
        "caps_line": "DER KI-PROJEKTE SCHEITERN AN DER DATENQUALITÄT",
        "italic1": "Das Modell rechnet nur so gut,",
        "italic2": "wie das CRM es füttert.",
        "bottom_caps": "DATENQUALITÄT · CRM · MITTELSTAND",
    },
    "hero_svg": {
        "top_caps": "DATENQUALITÄT & KI IM VERTRIEB · MITTELSTAND 2026",
        "big_number": "60",
        "big_units": "%",
        "caption_caps": "DER KI-PROJEKTE SCHEITERN AN SCHLECHTEN DATEN · GARTNER 2025",
        "italic1": "Die meisten KI-Projekte scheitern nicht am Algorithmus.",
        "italic2": "Sie scheitern an Karteileichen und Lücken im CRM.",
        "box_value": "+20 %",
        "box_caps": "GENAUIGKEIT DURCH SAUBERE DATEN",
        "right1": "Wer die Pflege spart, kauft sich teure",
        "right2": "Nachkommastellen. Der Hebel liegt im CRM,",
        "right3": "nicht im Modell.",
        "source_caps": "QUELLE · GARTNER 2025 · BITKOM 2026 · MCKINSEY · ABBILDUNG TITEL",
    },
    "body": [
        {"type": "p", "html": "Es ist ein stilles Versprechen, das sich durch jede Vertriebssoftware mit KI-Etikett zieht: schneller qualifizieren, präziser prognostizieren, weniger Bauchgefühl. Die Werkzeuge sind besser geworden. Die Ergebnisse oft nicht — und der Grund liegt selten dort, wo man ihn sucht."},
        {"type": "h3", "html": "Wo der Hebel <em>wirklich liegt</em>"},
        {"type": "p", "html": "Gartner führt einen Großteil gescheiterter KI-Projekte auf mangelnde Datenqualität zurück. Im Mittelstand verschärft sich das Bild: gewachsene CRM-Systeme, doppelte Kontakte, Abschlusstermine aus dem Vorjahr. Ein Modell lernt aus diesem Durcheinander — und gibt es mit mehr Nachkommastellen zurück."},
        {"type": "pullquote", "html": "Das Modell ist nur so ehrlich wie das CRM, das es füttert. Wer die Pflege spart, kauft sich <em>teure Nachkommastellen.</em>"},
        {"type": "h3", "html": "Drei Bedingungen, <em>bevor die KI trägt</em>"},
        {"type": "inline_fig", "title_caps": "DREI BEDINGUNGEN — BEVOR DIE KI IM VERTRIEB TRÄGT", "cap": "Drei Bedingungen — keine davon ist ein Technikproblem.", "cols": [
            {"num": "01", "head": "Gepflegtes CRM", "lines": ["Aktuelle Termine, keine", "Leichen, ein Pflege-", "Rhythmus, den der", "Vertrieb einhält."]},
            {"num": "02", "head": "Klare Definitionen", "lines": ["Jede Phase im Trichter", "eindeutig — damit alle", "dasselbe meinen, wenn", "sie „50 Prozent“ sagen."]},
            {"num": "03", "head": "Ein Verantwortlicher", "lines": ["Wer pflegt, prüft, haftet?", "Ohne Eigentümer im", "Vertrieb bleibt es ein", "IT-Projekt ohne Wirkung."]},
        ]},
        {"type": "p", "html": "Wer diese drei Punkte stellt, bevor das erste Modell läuft, gehört zu der Minderheit, deren KI-Projekte messbar liefern. Wer sie überspringt, finanziert ein weiteres Werkzeug, das am echten Datenstand zerschellt."},
    ],
}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Sales Intelligence Tagesschwerpunkt-Generator")
    ap.add_argument("--sample", action="store_true", help="Offline mit eingebautem Beispiel-Inhalt")
    ap.add_argument("--from-json", metavar="PFAD", help="Inhalt aus JSON-Datei statt API")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="Laufdatum überschreiben")
    args = ap.parse_args()

    if args.date:
        run_date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=BERLIN)
    else:
        run_date = datetime.now(BERLIN)
    date = run_date.strftime("%Y-%m-%d")

    # Idempotenz: existiert für heute schon ein Beitrag?
    articles = json.loads(ARTICLES_JSON.read_text(encoding="utf-8"))
    if any(a.get("date") == date for a in articles) and not (args.sample or args.from_json):
        print(f"[SI] Für {date} existiert bereits ein Beitrag — nichts zu tun.")
        return 0

    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    elif args.sample:
        data = SAMPLE
    else:
        print(f"[SI] Generiere Tagesschwerpunkt für {date} mit {MODEL} …")
        data = call_claude(german_date(run_date), articles)

    result = render_and_write(data, run_date)
    print(f"[SI] Fertig: {result['title']}")
    print(f"[SI]   Datei: articles/{result['file']}")
    print(f"[SI]   articles.json + index.html aktualisiert.")
    # Für die GitHub Action (Commit-/PR-Titel)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"article_date={result['date']}\n")
            fh.write(f"article_title={result['title']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
