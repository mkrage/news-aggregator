# News Aggregator

Ein statischer News-Aggregator, der auf GitHub Pages läuft und über GitHub Actions regelmäßig RSS-Feeds aktualisiert.

## Features

- RSS-Feeds von tagesschau, heise, golem und spiegel
- Gewichtete Top-News (nicht nur nach Aktualität)
- Eine Nachricht, ein Eintrag: mehrfach gemeldete Themen werden zusammengefasst
- Lokaler Lesestatus im Browser
- Zwei umschaltbare Darstellungen (App / Zeitung), je mit Hell- und Dunkelmodus
- PWA-fähig
- Optional: KI-Zusammenfassung der Top-Themen via Gemini

## Architektur

- **Frontend**: Statische HTML/CSS/JS-Seite auf GitHub Pages
- **Daten**: `data/news.json`, `data/top-news.json` und `data/ai-summary.json`
- **Updater**: GitHub Actions Workflow `.github/workflows/update-news.yml`
- **Parser**: Python-Script `scripts/fetch_news.py`
- **Tests**: `scripts/test_fetch_news.py` und `tests/frontend.test.mjs`, CI in `.github/workflows/tests.yml`
- **Icons**: `scripts/make_icons.py` erzeugt `favicon.svg` und die PNG-Fallbacks

## Sortierung der Top-News

Vor dem Bewerten werden Artikel zu Themen gruppiert: Wer über dieselbe Nachricht
berichtet, landet in einem Cluster. Bewertet wird dann das Thema, nicht der
einzelne Artikel – sonst stünde eine breit gemeldete Nachricht vier Mal
untereinander oben in der Liste.

In die Bewertung gehen ein:

| Faktor | Gewicht |
| --- | --- |
| Aktualität des jüngsten Artikels zum Thema | bis 40 |
| Gewicht der besten berichtenden Quelle | bis 20 |
| Schlüsselwörter (`HIGHLIGHT_KEYWORDS`) | 8 je Treffer |
| Weitere Quellen zum selben Thema | 12 je Quelle, max. 36 |
| Politik oder Wirtschaft | 3 |

Gezählt werden **Quellen, nicht Artikel** – ein Portal mit fünf Meldungen zum
gleichen Thema hebt sich damit nicht selbst nach oben.

### Welcher Artikel ein Thema vertritt

Das Quellen-Ranking ist das `weight` in `FEEDS`: tagesschau (1.0), spiegel
(0.95), heise (0.9), golem (0.85). Berichten mehrere über dasselbe, steht der
Artikel der bestplatzierten Quelle in der Liste, die übrigen erscheinen als
„Auch bei" darunter – mit Link, damit die Auswahl nachvollziehbar bleibt. Bei
gleichem Gewicht gewinnt der neuere Artikel.

Ist eine Quelle mit `MAX_PER_SOURCE` Artikeln ausgereizt, vertritt die
nächstbeste Quelle das Thema. So fällt keine Nachricht nur wegen der
Quellen-Balance aus der Liste.

### Wann zwei Artikel dasselbe Thema sind

Verglichen werden die Wortstämme von Schlagzeile und Vorspann, gewichtet mit der
Seltenheit der Wörter im aktuellen Lauf: „Ukraine" trennt nichts, „Midterms"
schon. Gezählt wird, wie viel einer Schlagzeile im Text der anderen Meldung
vorkommt – in beide Richtungen, weil eine knappe Zeile in einem ausführlichen
Text aufgehen kann, aber nicht umgekehrt.

Nötig sind mindestens drei gemeinsame Wörter (`MIN_SHARED_WORDS`) und eine
Übereinstimmung von 50 Prozent (`SIMILARITY_THRESHOLD`). Zwei Wörter reichen
nicht: „Sachsen" und „Anhalt" würden sonst ein ganzes Themenfeld zu einem
Klumpen zusammenziehen. Im Zweifel bleiben zwei Einträge stehen – eine Dublette
ist harmloser als eine unterdrückte Nachricht.

Verglichen wird nur gegen den Vertreter eines Themas, nicht gegen alle
Mitglieder: sonst könnten sich über Zwischenschritte ganze Ketten verbinden.

## Darstellung

Zwei Themes stehen zur Wahl, umschaltbar in der Kopfzeile:

- **App** – kompakte Kopfleiste, System-Sans, Karten mit dünnen Rändern
- **Zeitung** – zentrierter Masthead, Serif-Schlagzeilen (Newsreader), warmes
  Papier, Haarlinien statt Karten

Ohne eigene Wahl richtet sich das Theme nach der Bildschirmbreite: ab 64rem
(1024 px) die Zeitung, darunter die App-Ansicht. Die Vorgabe wird nicht
gespeichert – erst ein Klick auf „App" oder „Zeitung" schreibt sie fest und gilt
dann überall.

Ältere Stände schrieben das Theme bei jeder Änderung mit, auch wenn es nur die
Vorgabe war. Ein gespeicherter Stand gilt darum erst ab der Versionsmarke
`v: 2` als eigene Wahl; ohne sie greift wieder die Vorgabe. Wer sein Theme
vorher bewusst gesetzt hatte, wählt es einmal neu.

Jedes Theme hat einen Hell- und einen Dunkelmodus. Ohne eigene Wahl folgt die
Seite der Systemeinstellung und reagiert live auf deren Wechsel; sobald der
Modus einmal von Hand umgeschaltet wurde, gilt diese Wahl. Theme, Modus, Layout
und Dichte liegen unter `news-aggregator-prefs` in `localStorage`.
Ein Inline-Skript im `<head>` setzt beides vor dem ersten Paint, damit nichts
sichtbar umspringt – die Breitenabfrage ist dort bewusst doppelt gepflegt.

Die Serif-Schrift wird erst geladen, wenn das Zeitungs-Theme tatsächlich aktiv
ist – wer bei „App" bleibt, holt keinen externen Font.

Auf schmalen Displays teilen sich die drei Tabs die volle Breite; am großen
Bildschirm behalten sie ihre natürliche Größe.

### Layout und Dichte

Welche Frage der Umschalter rechts in der Filterzeile stellt, hängt von der
Breite ab – ab 40rem (641 px) das Layout, darunter die Dichte:

| Ab 641 px | Bis 640 px |
| --- | --- |
| **Kacheln** – mehrspaltiges Raster, Bild oben | **Kompakt** – Zeile mit Vorschaubild, ohne Vorspann |
| **Liste** – volle Breite, Bild an der Seite | **Karten** – eine Spalte, Bild oben, mit Vorspann |
| | **Große Karten** – Bild im 4:3-Format, größere Schlagzeile |

Der Grund für die Trennung: einspaltig sehen Kacheln und Liste praktisch gleich
aus, dort ist nicht das Layout die Frage, sondern wie viel Platz ein Artikel
bekommt. Beide Wahlen liegen nebeneinander in den Einstellungen, jedes Gerät
benutzt die für seine Breite.

Im Stylesheet heißt das: die `list-view`-Regeln stehen in
`@media (min-width: 40.0625rem)`, die drei `density-*`-Regeln in
`@media (max-width: 40rem)`. `render()` setzt beide Klassen immer, welche greift,
entscheidet allein die Breite – so braucht das Skript keinen Resize-Listener.

## Icons

`favicon.svg` und die PNG-Fallbacks entstehen aus einer gemeinsamen
Beschreibung in `scripts/make_icons.py` (reine Standardbibliothek, kein
Pillow). Das SVG bringt eine eigene Dark-Mode-Variante mit; die Kacheln für iOS
und Android sind randlos, weil beide Systeme selbst maskieren.

```bash
python scripts/make_icons.py
```

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
