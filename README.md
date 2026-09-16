# News Aggregator

Ein statischer News-Aggregator, der auf GitHub Pages läuft und über GitHub Actions regelmäßig RSS-Feeds aktualisiert.

## Features

- RSS-Feeds von tagesschau, heise, golem und spiegel
- Gewichtete Top-News (nicht nur nach Aktualität)
- Eine Nachricht, ein Eintrag: mehrfach gemeldete Themen werden zusammengefasst
- Lokaler Lesestatus im Browser
- Zwei umschaltbare Darstellungen (App / Zeitung), je mit Hell- und Dunkelmodus
- PWA-fähig
- Optional: KI-Zusammenfassung der Top-Themen via Gemini (zweimal täglich) im eigenen Überblick-Tab mit Stand-Angabe

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

### Tabs und Gelesen-Ansicht

Drei Haupttabs führen durch die Seite: **Überblick** (nur die
KI-Zusammenfassung samt Stand, bei fehlenden oder veralteten Daten eine ruhige
Leerstelle mit Hinweis), **Top-News** (die gewichtete Auswahl, startet direkt
mit den Artikeln) und **Neueste** (alles nach Zeit). Auf dem Telefon wechselt
eine Wischgeste zwischen den Tabs; sie funktioniert auch im Überblick, obwohl
dort keine Liste steht.

Den Lesestatus filtert ein segmentierter Dreifach-Umschalter in der
Steuerleiste: **Alle** (keine Statusfilterung), **Ungelesen** und **Gelesen**.
Er ersetzt die früheren zwei Kippschalter und ist deutlich kompakter, besonders
auf dem Telefon. Der Filter wirkt auf den gerade aktiven Artikel-Tab und
springt beim Tabwechsel auf „Alle" zurück, damit er nicht unbemerkt
weiterfiltert. Im Überblick ist die Steuerleiste ohnehin ausgeblendet.

Die Semantik von „Ungelesen" ist bewusst sitzungsfreundlich: Was beim Laden
schon gelesen war, ist ausgeblendet; was während der Sitzung gelesen wird,
bleibt markiert sichtbar und fliegt erst beim nächsten Laden raus – nichts
verschwindet unter dem Finger. „Gelesen" zeigt dagegen den ehrlichen
Ist-Zustand: nur tatsächlich gelesene Artikel, und ein wieder ungelesener
verschwindet sofort aus dieser Ansicht.

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

Artikel gelten als gelesen, wenn sie angeklickt oder beim
Scrollen nach oben aus dem Viewport geschoben wurden. Markierte Artikel werden
sofort ausgegraut, bleiben aber bis zum nächsten Laden in der Liste – erst dann
greift der Filter „Ungelesen". So verschwindet nichts unter dem Finger.

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

### KI-Zusammenfassung

Die Feeds kommen stündlich, die Gemini-Zusammenfassung nur zweimal täglich: im
Lauf um `:23` in der Stunde 07 und 19 **Berliner Ortszeit**. Cron kennt nur UTC
und läge nach jeder Zeitumstellung eine Stunde daneben, deshalb entscheidet
`fetch_news.py` anhand der Ortszeit, nicht der Zeitplan. Wird der Lauf verzögert,
greift noch jeder Start innerhalb derselben Stunde; der `:53`-Lauf erkennt an der
bereits vermerkten Fensterkennung (`slot`), dass nichts mehr zu tun ist.

Manuelle Starts über *Run workflow* bieten die Eingabe `ai_summary`:

| Wert | Verhalten |
| --- | --- |
| `force` (Vorgabe) | fragt Gemini in jedem Fall |
| `auto` | wie der Zeitplan – nur im Morgen- oder Abendfenster |
| `skip` | lässt die Zusammenfassung unberührt |

Stündliche Läufe ohne KI-Fenster fassen `data/ai-summary.json` nicht an, und ein
fehlgeschlagener Gemini-Aufruf löscht den letzten guten Stand nicht sofort – er
ist höchstens ein paar Stunden alt. Erst nach `AI_SUMMARY_MAX_AGE_HOURS` (26 h,
also zwei ausgefallenen Fenstern) verschwindet die Datei, ebenso bei unlesbarem
Zeitstempel. Ohne `GEMINI_API_KEY` entsteht gar keine Zusammenfassung.

Weil das nächste Fenster erst zwölf Stunden später kommt, gibt ein einzelner
Fehlversuch nicht auf – aber blindes Wiederholen hilft wenig, wenn das Modell
selbst klemmt. `fetch_news.py` arbeitet deshalb eine feste Kaskade von fünf
Aufrufen ab (`GEMINI_ATTEMPT_MODELS`, die Obergrenze für den ganzen Lauf):

| Versuch | Modell |
| --- | --- |
| 1, 2 | `gemini-3.8-flash` (Primär) |
| 3, 4, 5 | `gemini-2.5-flash` (Fallback) |

Ein kurzer zweiter Versuch fängt echte Aussetzer ab; hängt das neue Modell
dagegen an seinem Kontingent oder ist überlastet, übernimmt das bewährte
ältere. Ausgelöst wird das von HTTP 408, 429, 500, 502, 503, 504 sowie von
Verbindungs- und Timeout-Fehlern. Beim selben Modell wächst die Wartezeit
(rund 1, 2, 4, 8 s plus Jitter), beim Modellwechsel genügt eine kurze Pause
(`GEMINI_SWITCH_DELAY`) – dort ist das andere Modell das Mittel, nicht die
Zeit. Nennt die Antwort ein brauchbares `Retry-After`, gilt dessen Wert,
gedeckelt auf `GEMINI_RETRY_MAX_DELAY` (30 s).

HTTP 400, 401, 403 und 404 (`GEMINI_FATAL_STATUSES`, etwa falscher Key oder
unbekanntes Modell) brechen sofort ab: weder Wiederholung noch Fallback ändern
daran etwas. Dasselbe gilt für unerwartet gebaute Antworten. Bleibt es beim
Fehlschlag, verhält sich der Lauf wie bisher: keine neue Datei, der bisherige
Stand bleibt stehen.

Jeder Versuch landet mit Nummer, Modell und Statuscode bzw. Fehlerklasse im Log:

```
AI summary Versuch 1/5 (gemini-3.8-flash) fehlgeschlagen: HTTP 429 api_status=RESOURCE_EXHAUSTED api_code=429 reason=RATE_LIMIT_EXCEEDED
```

Die drei `api_*`/`reason`-Felder stammen aus Googles JSON-Fehlerbody
(`error.status`, `error.code` und `reason` aus `error.details`) und sagen beim
Nachsehen, ob es Kontingent, Überlastung oder Berechtigung war. Mehr geht nicht
ins Log: `error.message` zitiert mitunter den Prompt und nennt Projekt oder Key,
Response-URL, Header und Body bleiben ebenfalls draußen. Durchgelassen werden nur
kurze Kennungen (`SAFE_TOKEN_PATTERN`) – steht in einem dieser Felder ausnahmsweise
Prosa, fällt sie weg. Fehlt der Body oder ist er kein brauchbares JSON, bleibt es
still bei `HTTP <status>`.

Eine gelungene Zusammenfassung vermerkt in `data/ai-summary.json` unter `model`,
welches Modell sie geschrieben hat. Das Frontend liest nur `summary` und
`generatedAt` und ignoriert das Feld – es steht dort fürs Nachsehen, wenn eine
Zusammenfassung anders klingt als sonst.

#### Format der Zusammenfassung

`app.js` macht aus jeder Zeile einen eigenen Absatz und setzt sie als Text, nicht
als Markdown. Freier Fließtext mit Überschriften und Schlussfloskel sieht dort
aus wie ein Unfall, deshalb gibt `AI_SUMMARY_FORMAT` das Format eng vor: 3 bis 5
Zeilen, jede beginnt mit `- ` und enthält genau einen Satz, optional mit einem
`**Kurztitel:**` am Satzanfang – keine Einleitung, keine Überschrift, kein
Schlusswort, keine Leerzeilen.

Weil sich kein Modell immer daran hält, räumt `normalize_summary()` vor dem
Speichern das Nötigste auf: eine Anmoderation vor dem ersten Punkt ("Hier die
wichtigsten Themen:") fällt weg, `*` und `•` werden zu `- `, Leerzeilen und
Einrückung verschwinden. Mehr nicht – Inhalt wird nie abgeschnitten, HTML bleibt
Text, und ein Satz, der mit `**Kurztitel:**` beginnt, bleibt unangetastet. Hält
sich das Modell gar nicht ans Format, steht der Fließtext eben unverändert da:
unschön, aber besser als eine leere Seite. Bleibt nach dem Aufräumen nichts
übrig, gilt die Antwort als leer und der bisherige Stand bleibt stehen.

Im Frontend akzeptiert `app.js` die Datei nur innerhalb von 24 h
(`AI_SUMMARY_MAX_AGE_HOURS` dort); danach zeigt der Überblick-Tab eine ruhige
Leerstelle statt veraltetem Text. Der Tab selbst bleibt stehen – er ist einer
der drei Haupttabs und verschwindet nie.

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
