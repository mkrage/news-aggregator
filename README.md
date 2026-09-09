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

## Einrichtung

1. Repository forken/klonen
2. GitHub Pages aktivieren: *Settings → Pages → Source: main / (root)*
3. Für KI-Zusammenfassung: `GEMINI_API_KEY` als Repository Secret hinterlegen
4. Workflow manuell ausführen oder warten, bis er automatisch läuft

## Lokale Entwicklung

```bash
cd scripts
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python fetch_news.py
```

Danach kann `index.html` lokal im Browser geöffnet werden.
