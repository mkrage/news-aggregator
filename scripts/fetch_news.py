#!/usr/bin/env python3
"""
RSS-News-Aggregator für GitHub Pages.
Holt Feeds, gewichtet Artikel und erzeugt JSON-Dateien für das Frontend.
"""

import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests
from dateutil import parser as date_parser

# Konfiguration
FEEDS = {
    "tagesschau": {
        "url": "https://www.tagesschau.de/xml/rss2/",
        "weight": 1.0,
        "category": "Nachrichten",
    },
    "heise": {
        "url": "https://www.heise.de/rss/heise-top-atom.xml",
        "weight": 0.9,
        "category": "Technologie",
    },
    "golem": {
        "url": "https://rss.golem.de/rss.php?feed=RSS2.0",
        "weight": 0.85,
        "category": "Technologie",
    },
    "spiegel": {
        "url": "https://www.spiegel.de/schlagzeilen/index.rss",
        "weight": 0.95,
        "category": "Nachrichten",
    },
}

HIGHLIGHT_KEYWORDS = [
    "krieg", "ukraine", "gaza", "israel", "wahl", "bundestag", "trump",
    "unwetter", "sturm", "flut", "erdbeben", "krise", "inflation",
    "klimawandel", "corona", "pandemie"
]

CATEGORY_KEYWORDS = {
    "Politik": ["wahl", "bundestag", "regierung", "minister", "trump", "ukraine", "gaza", "israel"],
    "Wirtschaft": ["wirtschaft", "börse", "inflation", "ezb", "bundesbank", "aktie", "unternehmen"],
    "Technologie": ["ki", "künstliche intelligenz", "apple", "google", "microsoft", "meta", "cyber"],
    "Wissenschaft": ["forschung", "studie", "wissenschaft", "medizin", "impfung"],
    "Panorama": ["unfall", "kriminalität", "polizei", "prozess"],
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MAX_AGE_HOURS = 48
TOP_NEWS_LIMIT = 15
MAX_PER_SOURCE = 5

# Themen-Cluster: ab welcher Übereinstimmung zwei Artikel als dieselbe Nachricht
# gelten, und wie stark breite Berichterstattung den Score hebt. Drei gemeinsame
# Wörter sind nötig, weil zwei ("Sachsen", "Anhalt") schon ein ganzes Themenfeld
# zusammenziehen würden – lieber eine Dublette zu viel als eine Nachricht weg.
SIMILARITY_THRESHOLD = 0.5
MIN_SHARED_WORDS = 3
COVERAGE_BONUS = 12
COVERAGE_CAP = 3  # mehr als drei zusätzliche Quellen bringen keinen Bonus mehr

USER_AGENT = "news-aggregator/1.0 (+https://github.com/mkrage/news-aggregator)"
FEED_TIMEOUT = 15
OG_IMAGE_TIMEOUT = 5
OG_IMAGE_WORKERS = 8
OG_IMAGE_MAX_BYTES = 300_000
OG_IMAGE_BUDGET = 60  # max. Seitenabrufe pro Lauf, damit der Job nicht ausufert

# Plausibilitätsgrenzen: verhindern, dass ein fehlgeschlagener Lauf gute Daten
# mit einem leeren oder halben Stand überschreibt.
MIN_ARTICLES = 10
MIN_RATIO_OF_PREVIOUS = 0.5

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

OG_IMAGE_PATTERNS = (
    re.compile(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE
    ),
)
IMG_TAG_PATTERN = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")
WORD_SPLIT_PATTERN = re.compile(r"\w+")

# "5.000 Dollar" und "5000 Dollar" sollen dasselbe Wort ergeben. Nur Trenner
# zwischen Dreiergruppen entfernen, damit Datumsangaben unberührt bleiben.
THOUSANDS_PATTERN = re.compile(r"(?<=\d)[.,](?=\d{3}(?!\d))")

UMLAUT_MAP = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})

# Funktionswörter und Nachrichten-Floskeln unterscheiden keine Themen. Kurze
# Wörter fallen ohnehin durch die Längengrenze in tokenize().
STOPWORDS = frozenset(
    """
    aber alle allem allen aller alles also auch beim bereits damit dann dass
    dazu dessen diese diesem diesen dieser dieses doch dort durch eine einem
    einen einer eines einige erst erste ersten etwa gegen geht gibt haben hatte
    hatten heute hier ihre ihrem ihren immer jetzt kann kein keine konnte
    lassen laut machen mehr mehrere muss müssen nach neue neuen neuer neues
    nicht noch oder ohne schon sein seine seinen seit selbst sich sind soll
    sollen sowie über unter viele wegen weil weiter werden wieder will wird
    worden wurde wurden zwar zwei zwischen
    """.translate(UMLAUT_MAP).split()
)

# Sehr grober Stemmer: er soll nur Flexionsendungen einsammeln, damit
# "Republikaner" und "Republikanern" als dasselbe Wort zählen.
STEM_SUFFIXES = ("innen", "erin", "ern", "ende", "en", "er", "es", "em", "e", "s", "n")

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def is_http_url(url):
    """Prüft, ob eine URL per HTTP(S) abrufbar ist."""
    if not url:
        return False
    try:
        return urlparse(url).scheme in ("http", "https")
    except ValueError:
        return False


def parse_date(entry):
    """Extrahiert das Veröffentlichungsdatum – immer zeitzonenbewusst."""
    for field in ("published", "updated", "created"):
        value = entry.get(field)
        if not value:
            continue
        try:
            parsed = date_parser.parse(value)
        except (ValueError, OverflowError, TypeError):
            continue
        # Ohne Zeitzone ist kein Vergleich mit aware-Datumsangaben möglich.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return datetime.now(timezone.utc)


def extract_image(entry):
    """Versucht ein Bild aus dem Feed-Eintrag zu extrahieren."""
    # 1. media_content
    for media in entry.get("media_content") or []:
        if media.get("type", "").startswith("image") or media.get("medium") == "image":
            url = media.get("url")
            if is_http_url(url):
                return url

    # 2. media_thumbnail
    for thumbnail in entry.get("media_thumbnail") or []:
        url = thumbnail.get("url")
        if is_http_url(url):
            return url

    # 3. img-Tag oder og:image in summary/content
    for value in (entry_text(entry, "summary"), entry_text(entry, "content")):
        if not value:
            continue
        for pattern in (IMG_TAG_PATTERN, *OG_IMAGE_PATTERNS):
            match = pattern.search(value)
            if match and is_http_url(match.group(1)):
                return match.group(1)

    return None


def entry_text(entry, field):
    """Liest ein Feld, das feedparser als String oder Liste liefern kann."""
    value = entry.get(field, "")
    if isinstance(value, list):
        return value[0].get("value", "") if value else ""
    return value if isinstance(value, str) else ""


def fetch_og_image(url):
    """Holt og:image von der Zielseite als Fallback."""
    if not is_http_url(url):
        return None
    try:
        response = session.get(url, timeout=OG_IMAGE_TIMEOUT)
        response.raise_for_status()
        # og:image steht im <head> – der Anfang der Seite genügt.
        html = response.text[:OG_IMAGE_MAX_BYTES]
        for pattern in OG_IMAGE_PATTERNS:
            match = pattern.search(html)
            if match and is_http_url(match.group(1)):
                return match.group(1)
    except requests.RequestException:
        pass
    return None


def add_og_images(articles):
    """Ergänzt fehlende Bilder parallel und mit gedeckeltem Aufwand."""
    missing = [a for a in articles if not a["image"] and is_http_url(a["link"])]
    if not missing:
        return

    if len(missing) > OG_IMAGE_BUDGET:
        print(f"  og:image: {len(missing)} Kandidaten, begrenzt auf {OG_IMAGE_BUDGET}")
        missing = missing[:OG_IMAGE_BUDGET]

    with ThreadPoolExecutor(max_workers=OG_IMAGE_WORKERS) as pool:
        for article, image in zip(missing, pool.map(fetch_og_image, [a["link"] for a in missing])):
            article["image"] = image

    print(f"  og:image: {sum(1 for a in missing if a['image'])}/{len(missing)} gefunden")


def clean_html(text, max_length=1200):
    """Entfernt HTML-Tags und kürzt den Text auf sinnvolle Länge."""
    if not text:
        return ""
    text = TAG_PATTERN.sub(" ", text)
    text = WHITESPACE_PATTERN.sub(" ", text).strip()
    if len(text) > max_length:
        # Kürze am letzten Satzende vor max_length
        cutoff = text.rfind(".", 0, max_length)
        if cutoff > max_length * 0.7:
            text = text[: cutoff + 1]
        else:
            text = text[:max_length]
        text += " …"
    return text


def extract_best_text(entry):
    """Wählt den aussagekräftigsten Text aus summary oder content."""
    candidates = []

    summary = entry.get("summary", "")
    if summary:
        candidates.append(clean_html(summary))

    content = entry.get("content", "")
    if isinstance(content, list):
        for part in content:
            value = part.get("value", "")
            if value:
                candidates.append(clean_html(value))
    elif isinstance(content, str):
        candidates.append(clean_html(content))

    # Wähle den längsten sauberen Text
    candidates = [c for c in candidates if c]
    return max(candidates, key=len) if candidates else ""


def detect_category(title, summary, default_category):
    """Erkennt die Kategorie anhand von Schlagwörtern."""
    text = (title + " " + summary).lower()
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in text)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else default_category


def article_id(source_name, link, title):
    """Stabile ID – am Link, weil Titel nachträglich redigiert werden."""
    basis = link if link else title
    digest = hashlib.sha1(f"{source_name}|{basis}".encode("utf-8")).hexdigest()
    return f"{source_name}-{digest[:16]}"


def download_feed(source_name, url):
    """Lädt einen Feed mit Timeout – feedparser.parse(url) hat keinen."""
    try:
        response = session.get(url, timeout=FEED_TIMEOUT)
        response.raise_for_status()
        return response.content
    except requests.RequestException as error:
        print(f"  ! {source_name}: Abruf fehlgeschlagen: {error}")
        return None


def fetch_feed(source_name, config):
    """Holt und parst einen einzelnen Feed."""
    print(f"Fetching {source_name}...")
    raw = download_feed(source_name, config["url"])
    if raw is None:
        return []

    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        print(f"  ! {source_name}: Feed nicht lesbar: {parsed.get('bozo_exception')}")
        return []

    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

    for entry in parsed.entries:
        # Ein kaputter Eintrag darf nicht den ganzen Feed mitnehmen.
        try:
            pub_date = parse_date(entry)
            if pub_date < cutoff:
                continue

            title = (entry.get("title") or "").strip()
            link = entry.get("link") or ""
            if not title or not is_http_url(link):
                continue

            summary = extract_best_text(entry)
            articles.append({
                "id": article_id(source_name, link, title),
                "source": source_name,
                "sourceWeight": config["weight"],
                "title": title,
                "link": link,
                "summary": summary,
                "image": extract_image(entry),
                "published": pub_date.isoformat(),
                "category": detect_category(title, summary, config.get("category", "Allgemein")),
            })
        except Exception as error:  # noqa: BLE001 - ein Eintrag ist nie kritisch
            print(f"  ! {source_name}: Eintrag übersprungen: {error}")

    return articles


def dedupe(articles):
    """Entfernt Duplikate anhand der ID (behält den ersten Treffer)."""
    seen = set()
    unique = []
    for article in articles:
        if article["id"] in seen:
            continue
        seen.add(article["id"])
        unique.append(article)
    return unique


def stem(word):
    """Schneidet eine Flexionsendung ab, wenn genug Wortstamm übrig bleibt."""
    for suffix in STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokenize(text):
    """Zerlegt Text in die Wortstämme, die Themen unterscheiden."""
    if not text:
        return set()

    lowered = THOUSANDS_PATTERN.sub("", text.lower().translate(UMLAUT_MAP))

    words = set()
    for raw in WORD_SPLIT_PATTERN.findall(lowered):
        if raw in STOPWORDS:
            continue
        if raw.isdigit():
            # Jahreszahlen und Beträge sind starke Marker, kleine Zahlen nicht.
            if len(raw) >= 3:
                words.add(raw)
            continue
        stemmed = stem(raw)
        if len(stemmed) >= 4:
            words.add(stemmed)
    return words


def word_weights(documents):
    """IDF über den aktuellen Lauf: was überall steht, unterscheidet nichts.

    Die 1 davor ist Absicht: jedes Wort zählt mindestens einmal, seltene mehr.
    Ohne diesen Sockel wiegen in kleinen Textmengen ausgerechnet die geteilten
    Wörter am wenigsten – zwei Meldungen zum selben Ereignis würden dann
    unähnlicher wirken, je mehr sie sich gleichen.
    """
    total = len(documents)
    frequency = Counter(word for document in documents for word in document)
    return {word: 1 + math.log(1 + total / (1 + count)) for word, count in frequency.items()}


def coverage_ratio(words, other_words, weights):
    """Wie viel einer Schlagzeile (IDF-gewichtet) im anderen Text vorkommt."""
    shared = words & other_words
    if len(shared) < MIN_SHARED_WORDS:
        # Zu wenige Treffer sind Zufall oder ein gemeinsames Themenfeld – beides
        # macht aus zwei Meldungen noch keine gemeinsame Nachricht.
        return 0.0
    total = sum(weights.get(word, 1.0) for word in words)
    if total <= 0:
        return 0.0
    return sum(weights.get(word, 1.0) for word in shared) / total


def similarity(one, other, weights):
    """Ähnlichkeit zweier Artikel, in beide Richtungen geprüft.

    Schlagzeilen sind unterschiedlich lang – eine knappe Zeile kann in einem
    ausführlichen Text vollständig aufgehen, umgekehrt aber nicht. Darum zählt
    die bessere der beiden Richtungen.
    """
    return max(
        coverage_ratio(one["title_words"], other["text_words"], weights),
        coverage_ratio(other["title_words"], one["text_words"], weights),
    )


def rank_key(item):
    """Quellen-Ranking: höheres Gewicht zuerst, dann das Neuere."""
    article = item["article"]
    return (
        -article.get("sourceWeight", 0),
        -item["published_at"].timestamp(),
        article["id"],
    )


def build_stories(articles):
    """Gruppiert Artikel, die dieselbe Nachricht melden.

    Verglichen wird nur gegen den Vertreter eines Themas, nicht gegen alle
    Mitglieder: sonst könnten sich über Zwischenschritte ganze Themenketten zu
    einem Klumpen verbinden.
    """
    prepared = [
        {
            "article": article,
            "title_words": tokenize(article["title"]),
            "text_words": tokenize(f"{article['title']} {article.get('summary') or ''}"),
            "published_at": date_parser.parse(article["published"]),
        }
        for article in articles
    ]
    weights = word_weights([item["text_words"] for item in prepared])

    # Beste Quelle zuerst – dadurch wird sie automatisch Vertreter des Themas.
    prepared.sort(key=rank_key)

    stories = []
    for item in prepared:
        best, best_score = None, 0.0
        for story in stories:
            score = similarity(item, story["members"][0], weights)
            if score > best_score:
                best, best_score = story, score

        if best is not None and best_score >= SIMILARITY_THRESHOLD:
            best["members"].append(item)
        else:
            stories.append({"members": [item]})

    for story in stories:
        story["newest"] = max(item["published_at"] for item in story["members"])
    return stories


def calculate_score(story, now):
    """Berechnet einen Relevanz-Score für ein Thema."""
    lead = story["members"][0]["article"]
    score = 0.0

    # Aktualität: der jüngste Artikel bestimmt, wie frisch das Thema ist.
    age_hours = (now - story["newest"]).total_seconds() / 3600
    score += max(0, 1 - (age_hours / MAX_AGE_HOURS)) * 40

    # Quellen-Gewichtung der besten berichtenden Quelle
    score += lead.get("sourceWeight", 0) * 20

    # Highlight-Schlüsselwörter
    text = f"{lead['title']} {lead.get('summary') or ''}".lower()
    score += sum(1 for kw in HIGHLIGHT_KEYWORDS if kw in text) * 8

    # Breite der Berichterstattung: zählt Quellen, nicht Artikel. Sonst hebt ein
    # Portal mit mehreren Meldungen zum Thema sich selbst nach oben.
    sources = {item["article"]["source"] for item in story["members"]}
    score += min(len(sources) - 1, COVERAGE_CAP) * COVERAGE_BONUS

    # Kategorie-Bonus: Politik und Wirtschaft leicht bevorzugen
    if lead.get("category") in ("Politik", "Wirtschaft"):
        score += 3

    return round(score, 2)


def build_coverage(chosen, members):
    """Listet die weiteren Quellen, die dieselbe Nachricht melden."""
    others = []
    seen = {chosen["source"]}
    for article in members:
        if article["source"] in seen:
            continue
        seen.add(article["source"])
        others.append({
            "source": article["source"],
            "title": article["title"],
            "link": article["link"],
        })
    return {"sourceCount": len(seen), "others": others}


def generate_top_news(articles):
    """Erzeugt die Top-News: ein Artikel pro Thema, beste Quelle zuerst."""
    now = datetime.now(timezone.utc)
    stories = build_stories(articles)

    # Der Vorlauf kann Reste hinterlassen haben; Coverage gilt nur für die
    # tatsächlich gewählten Vertreter.
    for article in articles:
        article.pop("coverage", None)

    for story in stories:
        story["score"] = calculate_score(story, now)
        for item in story["members"]:
            item["article"]["score"] = story["score"]

    stories.sort(key=lambda story: (-story["score"], -story["newest"].timestamp()))

    top_news = []
    source_counts = Counter()
    for story in stories:
        members = [item["article"] for item in story["members"]]
        # Ist die beste Quelle ausgereizt, vertritt die nächste das Thema –
        # so fällt keine Nachricht nur wegen der Quellen-Balance heraus.
        chosen = next(
            (a for a in members if source_counts[a["source"]] < MAX_PER_SOURCE), None
        )
        if chosen is None:
            continue

        coverage = build_coverage(chosen, members)
        if coverage["others"]:
            chosen["coverage"] = coverage

        top_news.append(chosen)
        source_counts[chosen["source"]] += 1
        if len(top_news) >= TOP_NEWS_LIMIT:
            break

    return top_news


def generate_ai_summary(articles):
    """Optional: Fragt Gemini nach einer Zusammenfassung der Top-Themen."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    top_titles = [f"- {a['title']} ({a['source']})" for a in articles[:20]]
    prompt = (
        "Fasse die wichtigsten Nachrichten-Themen des Tages in 3–5 kurzen Punkten zusammen. "
        "Jeder Punkt sollte einen Satz lang sein und das Thema sowie die Bedeutung kurz erklären.\n\n"
        + "\n".join(top_titles)
    )

    try:
        # Key im Header, nicht im Query-String: sonst landet er in Logs.
        response = session.post(
            GEMINI_URL,
            headers={"x-goog-api-key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if not text:
            return None
        return {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "summary": text,
        }
    except requests.RequestException as error:
        # Nur Statuscode ausgeben – Exception-Texte enthalten die volle URL.
        status = error.response.status_code if error.response is not None else "n/a"
        print(f"AI summary failed: {type(error).__name__} (HTTP {status})")
    except (KeyError, IndexError, TypeError, ValueError) as error:
        print(f"AI summary failed: unerwartete Antwort ({error})")
    return None


def previous_count(path):
    """Liest die Artikelanzahl des letzten Laufs, falls vorhanden."""
    try:
        with open(path, encoding="utf-8") as f:
            return len(json.load(f).get("articles", []))
    except (OSError, ValueError):
        return 0


def is_plausible(new_count, old_count):
    """Schützt davor, gute Daten durch einen Teilausfall zu ersetzen."""
    if new_count < MIN_ARTICLES:
        print(f"Abbruch: nur {new_count} Artikel (Minimum {MIN_ARTICLES})")
        return False
    if old_count and new_count < old_count * MIN_RATIO_OF_PREVIOUS:
        print(f"Abbruch: {new_count} Artikel gegenüber {old_count} im letzten Lauf")
        return False
    return True


def write_json(path, payload):
    """Schreibt erst in eine temporäre Datei, damit nichts halb landet."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    news_path = os.path.join(DATA_DIR, "news.json")
    top_path = os.path.join(DATA_DIR, "top-news.json")
    ai_path = os.path.join(DATA_DIR, "ai-summary.json")

    all_articles = []
    failed_sources = []
    for source_name, config in FEEDS.items():
        articles = fetch_feed(source_name, config)
        if not articles:
            failed_sources.append(source_name)
        all_articles.extend(articles)
        print(f"  -> {len(articles)} articles from {source_name}")

    all_articles = dedupe(all_articles)
    add_og_images(all_articles)

    # Sortiere alle Artikel nach Datum (neueste zuerst)
    all_articles.sort(key=lambda x: x["published"], reverse=True)

    if not is_plausible(len(all_articles), previous_count(news_path)):
        print(f"Fehlgeschlagene Quellen: {', '.join(failed_sources) or 'keine'}")
        print("Bestehende Daten bleiben unverändert.")
        return 1

    top_news = generate_top_news(all_articles)
    generated_at = datetime.now(timezone.utc).isoformat()

    write_json(news_path, {
        "generatedAt": generated_at,
        "count": len(all_articles),
        "articles": all_articles,
    })
    write_json(top_path, {
        "generatedAt": generated_at,
        "count": len(top_news),
        "articles": top_news,
    })

    ai_summary = generate_ai_summary(top_news)
    if ai_summary:
        write_json(ai_path, ai_summary)
    elif os.path.exists(ai_path):
        # Keine neue Zusammenfassung ist besser als eine von gestern.
        os.remove(ai_path)
        print("Alte AI-Zusammenfassung entfernt")

    if failed_sources:
        print(f"Warnung: keine Artikel von {', '.join(failed_sources)}")

    print(f"\nDone: {len(all_articles)} articles, {len(top_news)} top news")
    return 0


if __name__ == "__main__":
    sys.exit(main())
