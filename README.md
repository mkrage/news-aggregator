# News Aggregator

Ein statischer News-Aggregator, der auf GitHub Pages läuft und über GitHub Actions regelmäßig RSS-Feeds aktualisiert.

## Features

- RSS-Feeds von tagesschau, heise, golem und spiegel
- Gewichtete Top-News (nicht nur nach Aktualität)
- Lokaler Lesestatus im Browser
- PWA-fähig
- Optional: KI-Zusammenfassung der Top-Themen via Gemini

## Architektur

- **Frontend**: Statische HTML/CSS/JS-Seite auf GitHub Pages
- **Daten**: `data/news.json`, `data/top-news.json` und `data/ai-summary.json`
- **Updater**: GitHub Actions Workflow `.github/workflows/update-news.yml`
- **Parser**: Python-Script `scripts/fetch_news.py`
- **Tests**: `scripts/test_fetch_news.py` und `tests/frontend.test.mjs`, CI in `.github/workflows/tests.yml`

## Lesestatus

Artikel gelten als gelesen, wenn sie angeklickt, per Button markiert oder beim
Scrollen nach oben aus dem Viewport geschoben wurden. Markierte Artikel werden
sofort ausgegraut, bleiben aber bis zum nächsten Laden in der Liste – erst dann
greift „Gelesene ausblenden". So verschwindet nichts unter dem Finger.

Der Lesestatus liegt in `localStorage` und verfällt nach 30 Tagen. Die
Artikel-IDs sind Hashes des Links, damit ein nachträglich redigierter Titel den
Status nicht zurücksetzt.

## Einrichtung

1. Repository forken/klonen
2. GitHub Pages aktivieren: *Settings → Pages → Source: main / (root)*
3. Für KI-Zusammenfassung: `GEMINI_API_KEY` als Repository Secret hinterlegen
4. Workflow manuell ausführen oder warten, bis er automatisch läuft

## Aktualisierung

Der Workflow läuft zu den Minuten `:23` und `:53`. GitHub behandelt
`schedule`-Läufe als *best effort*: bei hoher Last werden sie verzögert oder
ganz verworfen, und zur vollen Stunde ist der Rückstau am größten. Zwei Versuche
zu Nebenzeiten ergeben in Summe verlässlich etwa ein Update pro Stunde.

> **Achtung:** GitHub deaktiviert geplante Workflows nach 60 Tagen ohne
> Repository-Aktivität. Commits von `github-actions[bot]` zählen dafür **nicht**.
> Wer den Zeitplan dauerhaft laufen lassen will, muss gelegentlich selbst pushen
> oder den Lauf extern per `workflow_dispatch`-API auslösen.

Schlägt der Abruf fehl oder liefert deutlich weniger Artikel als der letzte Lauf,
bricht das Script mit Exit-Code 1 ab und lässt die bestehenden Daten unangetastet
– eine leere Seite ist schlimmer als eine etwas veraltete.

## Lokale Entwicklung

```bash
# Python-Teil
cd scripts
python -m venv .venv
.venv\Scripts\activate        # Windows; unter Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python fetch_news.py

# Tests
python -m unittest discover -p "test_*.py" -v
```

```bash
# Frontend-Teil (im Repo-Wurzelverzeichnis)
npm install
npm test
```

Die Seite braucht einen HTTP-Server, weil `fetch` auf `file://` blockiert wird:

```bash
python -m http.server 8000
# dann http://localhost:8000 öffnen
```
