# Sales Intelligence — Tagesschwerpunkt-Generator

Ersetzt den alten „Sales Intelligence Bot", der bis zum 06.06.2026 die täglichen
Magazin-Beiträge erzeugt hat. Läuft jetzt **vollautomatisch in der GitHub-Cloud**
und ist an keinen einzelnen Rechner mehr gebunden.

## Was passiert täglich

Die GitHub Action [`daily-article.yml`](../.github/workflows/daily-article.yml) läuft
jeden Morgen (Standard: 04:00 UTC ≈ 06:00 Berlin) und ruft [`generate.py`](generate.py) auf.
Das Skript:

1. ruft **Claude (Sonnet 4.6)** und lässt einen redaktionellen Tagesschwerpunkt als
   **strukturiertes JSON** erzeugen (Schlagzeile, Vorspann, Text, Kennzahlen, Quellen);
2. rendert daraus **deterministisch** die On-Brand-SVGs und das HTML — die KI schreibt
   nur Text/Zahlen, alle Koordinaten und das Markup kommen aus den Templates im Skript,
   damit die Ausgabe pixelgenau im bestehenden Design bleibt;
3. schreibt die neue **Standalone-Artikel-HTML** nach `articles/AAAA-MM-TT-slug.html`
   (aus `article-template.html`);
4. ergänzt den Eintrag oben in **`articles.json`** (Archiv/TOC laden daraus per JS);
5. ersetzt in **`index.html`** den Cover-/Teaser-Block und den eingebetteten
   Schwerpunkt-Artikel (zwischen den `SI:*`-Markern) und aktualisiert „Stand · …".

Statt direkt live zu gehen, legt die Action das Ergebnis als **Pull Request** an.
Du prüfst Zahlen/Quellen und mergest mit einem Klick → GitHub Pages veröffentlicht
automatisch.

## Einmalige Einrichtung

1. **API-Key als Secret hinterlegen**
   GitHub → Repo `sales-intelligence` → *Settings* → *Secrets and variables* →
   *Actions* → *New repository secret*
   - Name: `ANTHROPIC_API_KEY`
   - Wert: dein Anthropic-API-Key (console.anthropic.com → API Keys)

2. **Actions dürfen PRs anlegen**
   *Settings* → *Actions* → *General* → *Workflow permissions* →
   „Read and write permissions" **und** Häkchen bei
   „Allow GitHub Actions to create and approve pull requests" → *Save*.

Danach läuft alles automatisch. Ein Lauf lässt sich jederzeit manuell auslösen:
*Actions* → *Daily Sales Intelligence* → *Run workflow*.

## Lokal testen

```bash
pip install -r generator/requirements.txt

# Offline, ohne API-Key — rendert eingebauten Beispiel-Inhalt:
python generator/generate.py --sample --date 2026-06-09

# Echter Lauf (braucht den Key in der Umgebung):
export ANTHROPIC_API_KEY=sk-ant-...
python generator/generate.py
```

Vorschau im Browser z. B. mit `python3 -m http.server 8099` im Repo-Wurzelverzeichnis.
Test-Änderungen vor dem Commit zurücksetzen mit
`git checkout index.html articles.json && git clean -f articles/`.

## Stellschrauben

| Was | Wo |
|-----|----|
| Uhrzeit / Tage | `cron` in `.github/workflows/daily-article.yml` (UTC!) |
| Modell | `SI_MODEL`-Env oder Default `claude-sonnet-4-6` in `generate.py` |
| Tonalität / Themen / Quellen | `build_prompt()` in `generate.py` |
| SVG-Layout, Templates | `build_*`-Funktionen in `generate.py` |
| Direkt live statt PR | im Workflow den PR-Schritt durch `git commit && git push` ersetzen |

## Wichtig

- **Idempotent:** Existiert für den Tag schon ein Beitrag, passiert nichts (kein PR).
- **Statistiken prüfen:** Der Prompt fordert belegbare Zahlen mit benannten Quellen,
  trotzdem kann ein Sprachmodell Zahlen erfinden. Deshalb der PR-Review-Schritt —
  bitte vor dem Merge kurz gegenlesen.
- Die `SI:*`-Marker in `index.html` **nicht entfernen** — daran erkennt der Generator,
  welche Bereiche er ersetzen darf.
