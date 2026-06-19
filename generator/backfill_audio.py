#!/usr/bin/env python3
"""
Einmaliger Backfill: erzeugt nachträglich Vorlese-MP3s für bereits
bestehende Artikel und bindet sie ein (<audio id="ra-audio">).

Läuft idempotent — Artikel, die schon eine MP3/ein ra-audio-Element haben,
werden übersprungen. Nutzt dieselbe OpenAI-TTS-Logik wie der Daily-Generator.
"""

from __future__ import annotations

import html as htmllib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate import synthesize_audio  # noqa: E402  (gleiche TTS-Logik)

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "articles"


def _clean_line(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", htmllib.unescape(s)).strip()


def _clean_body(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*", "\n\n", s)
    return s.strip()


def extract_narration(h: str) -> str:
    def grab(pat: str) -> str:
        m = re.search(pat, h, re.S)
        return _clean_line(m.group(1)) if m else ""

    title = grab(r'<h1 class="article-title">(.*?)</h1>')
    standfirst = grab(r'<p class="article-standfirst">(.*?)</p>')

    bm = re.search(r'<article class="article-body">(.*?)</article>', h, re.S)
    body = bm.group(1) if bm else ""
    body = re.sub(r"<svg.*?</svg>", " ", body, flags=re.S)               # Grafiken raus
    body = re.sub(r'<div class="cap">.*?</div>', " ", body, flags=re.S)  # Bildunterschrift
    body = re.sub(r'<div class="byline-end">.*?</div>', " ", body, flags=re.S)
    body = re.sub(r'<span class="source">.*?</span>', " ", body, flags=re.S)
    body = re.sub(r"</(p|h2|h3|blockquote)>", "\n\n", body)
    body = _clean_body(body)

    return "\n\n".join(p for p in (title, standfirst, body) if p)


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("[Backfill] FEHLER: OPENAI_API_KEY nicht gesetzt.", file=sys.stderr)
        return 1

    done = skipped = failed = 0
    for f in sorted(ARTICLES.glob("*.html")):
        h = f.read_text(encoding="utf-8")
        if 'id="ra-audio"' in h:
            skipped += 1
            continue
        mp3 = f.with_suffix(".mp3")
        if not mp3.exists():
            narration = extract_narration(h)
            if not narration or not synthesize_audio(narration, mp3):
                failed += 1
                continue
        tag = f'<audio id="ra-audio" src="/articles/{mp3.name}" preload="none"></audio>'
        if '<div class="article-image">' in h:
            h = h.replace('<div class="article-image">', tag + '\n\n  <div class="article-image">', 1)
        else:
            h = h.replace("</main>", tag + "\n</main>", 1)
        f.write_text(h, encoding="utf-8")
        done += 1
        print("[Backfill] OK:", f.name)

    print(f"[Backfill] fertig — {done} erzeugt, {skipped} bereits vorhanden, {failed} Fehler.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
