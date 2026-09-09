#!/usr/bin/env python3
"""
RSS-News-Aggregator für GitHub Pages.
Holt Feeds, gewichtet Artikel und erzeugt JSON-Dateien für das Frontend.
"""

import json
import os
import re
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
MAX_PER_SOURCE = 3


def parse_date(entry):
    """Extrahiert das Veröffentlichungsdatum aus einem Feed-Eintrag."""
    for field in ["published", "updated", "created"]:
        value = entry.get(field)
        if value:
            try:
                return date_parser.parse(value)
            except Exception:
                continue
    return datetime.now(timezone.utc)


def normalize_date(dt):
    """Gibt ein ISO-Datum zurück, auch wenn es naive Zeitzone hat."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def extract_image(entry):
    """Versucht ein Bild aus dem Feed-Eintrag zu extrahieren."""
    # 1. media_content
    if "media_content" in entry:
        for media in entry.media_content:
            if media.get("type", "").startswith("image"):
                return media.get("url")
            if media.get("medium") == "image":
                return media.get("url")

    # 2. media_thumbnail
    if "media_thumbnail" in entry:
        return entry.media_thumbnail[0].get("url")

    # 3. Suche nach img-Tag in summary und content
    for field in ["summary", "content"]:
        value = entry.get(field, "")
        if isinstance(value, list):
            value = value[0].get("value", "") if value else ""
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', value)
        if match:
            return match.group(1)

    # 4. Suche nach og:image oder twitter:image in den Links/Description
    for field in ["summary", "content"]:
        value = entry.get(field, "")
        if isinstance(value, list):
            value = value[0].get("value", "") if value else ""
        match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', value)
        if match:
            return match.group(1)
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', value)
        if match:
            return match.group(1)

    return None


def fetch_og_image(url):
    """Holt og:image von der Zielseite als Fallback."""
    try:
        response = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', response.text)
        if match:
            return match.group(1)
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', response.text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def clean_html(text):
    """Entfernt HTML-Tags und kürzt den Text."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300] + "…" if len(text) > 300 else text


def detect_category(title, summary, default_category):
    """Erkennt die Kategorie anhand von Schlagwörtern."""
    text = (title + " " + summary).lower()
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in text)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else default_category


def fetch_feed(source_name, config):
    """Holt und parst einen einzelnen Feed."""
    print(f"Fetching {source_name}...")
    try:
        parsed = feedparser.parse(config["url"])
    except Exception as e:
        print(f"Error parsing {source_name}: {e}")
        return []

    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

    for entry in parsed.entries:
        pub_date = parse_date(entry)
        if pub_date < cutoff:
            continue

        title = entry.get("title", "")
        link = entry.get("link", "")
        summary = clean_html(entry.get("summary", ""))
        category = detect_category(title, summary, config.get("category", "Allgemein"))
        image = extract_image(entry) or fetch_og_image(link)

        articles.append({
            "id": re.sub(r"\W+", "-", f"{source_name}-{title}").lower().strip("-")[:80],
            "source": source_name,
            "sourceWeight": config["weight"],
            "title": title,
            "link": link,
            "summary": summary,
            "image": image,
            "published": normalize_date(pub_date),
            "category": category,
        })

    return articles


def calculate_score(article, all_articles, now):
    """Berechnet einen Relevanz-Score für einen Artikel."""
    score = 0.0

    # Aktualität: neuer = besser, exponentieller Abfall
    pub_date = date_parser.parse(article["published"])
    age_hours = (now - pub_date).total_seconds() / 3600
    recency_score = max(0, 1 - (age_hours / MAX_AGE_HOURS))
    score += recency_score * 40

    # Quellen-Gewichtung
    score += article["sourceWeight"] * 20

    # Highlight-Schlüsselwörter
    text = (article["title"] + " " + article["summary"]).lower()
    keyword_hits = sum(1 for kw in HIGHLIGHT_KEYWORDS if kw in text)
    score += keyword_hits * 8

    # Themen-Häufigkeit über Quellen hinweg
    title_words = set(re.findall(r"\b\w{5,}\b", article["title"].lower()))
    related = 0
    for other in all_articles:
        if other["source"] == article["source"]:
            continue
        other_words = set(re.findall(r"\b\w{5,}\b", other["title"].lower()))
        if len(title_words & other_words) >= 2:
            related += 1
    score += min(related, 5) * 5

    # Kategorie-Bonus: Politik und Wirtschaft leicht bevorzugen
    if article["category"] in ["Politik", "Wirtschaft"]:
        score += 3

    return round(score, 2)


def generate_top_news(articles):
    """Erzeugt die Top-News mit Anti-Spam pro Quelle."""
    now = datetime.now(timezone.utc)
    scored = []
    for article in articles:
        article["score"] = calculate_score(article, articles, now)
        scored.append(article)

    # Sortiere nach Score
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Anti-Spam: max. MAX_PER_SOURCE Artikel pro Quelle in Top-News
    top_news = []
    source_counts = {}
    for article in scored:
        source = article["source"]
        if source_counts.get(source, 0) >= MAX_PER_SOURCE:
            continue
        top_news.append(article)
        source_counts[source] = source_counts.get(source, 0) + 1
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

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={api_key}"
    )
    payload = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ]
    }

    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "summary": text.strip(),
        }
    except Exception as e:
        print(f"AI summary failed: {e}")
        return None


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    all_articles = []
    for source_name, config in FEEDS.items():
        articles = fetch_feed(source_name, config)
        all_articles.extend(articles)
        print(f"  -> {len(articles)} articles from {source_name}")

    # Sortiere alle Artikel nach Datum (neueste zuerst)
    all_articles.sort(key=lambda x: x["published"], reverse=True)

    top_news = generate_top_news(all_articles)
    ai_summary = generate_ai_summary(top_news)

    # Speichern
    with open(os.path.join(DATA_DIR, "news.json"), "w", encoding="utf-8") as f:
        json.dump({
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "count": len(all_articles),
            "articles": all_articles,
        }, f, ensure_ascii=False, indent=2)

    with open(os.path.join(DATA_DIR, "top-news.json"), "w", encoding="utf-8") as f:
        json.dump({
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "count": len(top_news),
            "articles": top_news,
        }, f, ensure_ascii=False, indent=2)

    if ai_summary:
        with open(os.path.join(DATA_DIR, "ai-summary.json"), "w", encoding="utf-8") as f:
            json.dump(ai_summary, f, ensure_ascii=False, indent=2)

    print(f"\nDone: {len(all_articles)} articles, {len(top_news)} top news")


if __name__ == "__main__":
    main()
