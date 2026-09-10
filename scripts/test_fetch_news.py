#!/usr/bin/env python3
"""Tests für fetch_news.py – ohne Netzwerkzugriff, ohne zusätzliche Pakete.

Ausführen: python -m unittest discover -s scripts -p "test_*.py"
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import fetch_news as fn


class TestParseDate(unittest.TestCase):
    def test_naive_wird_zeitzonenbewusst(self):
        # Ohne tzinfo wäre der Vergleich mit dem Cutoff ein TypeError.
        parsed = fn.parse_date({"published": "Mon, 09 Sep 2026 10:00:00"})
        self.assertIsNotNone(parsed.tzinfo)

    def test_zeitzone_bleibt_erhalten(self):
        parsed = fn.parse_date({"published": "Mon, 09 Sep 2026 10:00:00 +0200"})
        self.assertEqual(parsed.utcoffset(), timedelta(hours=2))

    def test_feldreihenfolge(self):
        entry = {"updated": "2026-09-09T10:00:00Z", "published": "2026-09-08T10:00:00Z"}
        self.assertEqual(fn.parse_date(entry).day, 8)

    def test_unlesbares_datum_faellt_zurueck(self):
        parsed = fn.parse_date({"published": "völliger Unsinn"})
        self.assertIsNotNone(parsed.tzinfo)

    def test_ohne_datum_faellt_zurueck(self):
        self.assertIsNotNone(fn.parse_date({}).tzinfo)

    def test_ergebnis_ist_mit_cutoff_vergleichbar(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        for value in ("Mon, 09 Sep 2026 10:00:00", "2026-09-09T10:00:00Z", "Unsinn"):
            with self.subTest(value=value):
                self.assertIsInstance(fn.parse_date({"published": value}) < cutoff, bool)


class TestIsHttpUrl(unittest.TestCase):
    def test_erlaubte_schemata(self):
        self.assertTrue(fn.is_http_url("http://example.com"))
        self.assertTrue(fn.is_http_url("https://example.com/a?b=c"))

    def test_abgelehnte_schemata(self):
        for value in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "/relativ"):
            with self.subTest(value=value):
                self.assertFalse(fn.is_http_url(value))

    def test_leere_werte(self):
        self.assertFalse(fn.is_http_url(None))
        self.assertFalse(fn.is_http_url(""))


class TestArticleId(unittest.TestCase):
    def test_stabil_bei_titelaenderung(self):
        # Redaktionen schärfen Überschriften nach – der Lesestatus muss halten.
        a = fn.article_id("heise", "https://heise.de/-123", "Titel")
        b = fn.article_id("heise", "https://heise.de/-123", "Titel, überarbeitet")
        self.assertEqual(a, b)

    def test_unterschiedlich_bei_anderem_link(self):
        a = fn.article_id("heise", "https://heise.de/-123", "Titel")
        b = fn.article_id("heise", "https://heise.de/-456", "Titel")
        self.assertNotEqual(a, b)

    def test_quelle_ist_teil_der_id(self):
        a = fn.article_id("heise", "https://x.de/1", "T")
        b = fn.article_id("golem", "https://x.de/1", "T")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("heise-"))

    def test_lange_titel_kollidieren_nicht(self):
        prefix = "Sehr langer Titel " * 10
        a = fn.article_id("heise", "", prefix + "Variante A")
        b = fn.article_id("heise", "", prefix + "Variante B")
        self.assertNotEqual(a, b)


class TestCleanHtml(unittest.TestCase):
    def test_entfernt_tags(self):
        self.assertEqual(fn.clean_html("<p>Hallo <b>Welt</b></p>"), "Hallo Welt")

    def test_normalisiert_whitespace(self):
        self.assertEqual(fn.clean_html("a\n\n   b\tc"), "a b c")

    def test_kuerzt_am_satzende(self):
        text = "Erster Satz. " + "x" * 200
        result = fn.clean_html(text, max_length=100)
        self.assertTrue(result.endswith("…"))
        self.assertLess(len(result), 120)

    def test_leerer_text(self):
        self.assertEqual(fn.clean_html(""), "")
        self.assertEqual(fn.clean_html(None), "")


class TestExtractImage(unittest.TestCase):
    def test_media_content(self):
        entry = {"media_content": [{"type": "image/jpeg", "url": "https://x.de/a.jpg"}]}
        self.assertEqual(fn.extract_image(entry), "https://x.de/a.jpg")

    def test_media_thumbnail(self):
        entry = {"media_thumbnail": [{"url": "https://x.de/t.jpg"}]}
        self.assertEqual(fn.extract_image(entry), "https://x.de/t.jpg")

    def test_img_tag_in_summary(self):
        entry = {"summary": '<p>Text <img src="https://x.de/i.jpg" alt=""></p>'}
        self.assertEqual(fn.extract_image(entry), "https://x.de/i.jpg")

    def test_content_als_liste(self):
        entry = {"content": [{"value": '<img src="https://x.de/c.jpg">'}]}
        self.assertEqual(fn.extract_image(entry), "https://x.de/c.jpg")

    def test_verwirft_unsichere_urls(self):
        entry = {"media_content": [{"medium": "image", "url": "javascript:alert(1)"}]}
        self.assertIsNone(fn.extract_image(entry))

    def test_ohne_bild(self):
        self.assertIsNone(fn.extract_image({"summary": "nur Text"}))


class TestExtractBestText(unittest.TestCase):
    def test_waehlt_laengsten_text(self):
        entry = {"summary": "kurz", "content": [{"value": "deutlich längerer Inhalt hier"}]}
        self.assertEqual(fn.extract_best_text(entry), "deutlich längerer Inhalt hier")

    def test_ohne_text(self):
        self.assertEqual(fn.extract_best_text({}), "")


class TestDetectCategory(unittest.TestCase):
    def test_erkennt_politik(self):
        self.assertEqual(fn.detect_category("Bundestag wählt", "", "Nachrichten"), "Politik")

    def test_faellt_auf_default_zurueck(self):
        self.assertEqual(fn.detect_category("Etwas Belangloses", "", "Nachrichten"), "Nachrichten")

    def test_ist_deterministisch(self):
        args = ("Wahl und Börse", "Regierung und Inflation", "Nachrichten")
        self.assertEqual({fn.detect_category(*args) for _ in range(20)}, {fn.detect_category(*args)})


class TestDedupe(unittest.TestCase):
    def test_entfernt_duplikate(self):
        articles = [{"id": "a"}, {"id": "b"}, {"id": "a"}]
        self.assertEqual([x["id"] for x in fn.dedupe(articles)], ["a", "b"])


class TestScoring(unittest.TestCase):
    def _article(self, **kwargs):
        base = {
            "id": "x",
            "source": "heise",
            "sourceWeight": 0.9,
            "title": "Ein ganz normaler Titel",
            "summary": "",
            "category": "Technologie",
            "published": datetime.now(timezone.utc).isoformat(),
        }
        base.update(kwargs)
        return base

    def test_neuer_artikel_schlaegt_alten(self):
        old = self._article(published=(datetime.now(timezone.utc) - timedelta(hours=40)).isoformat())
        new = self._article()
        top = fn.generate_top_news([old, new])
        self.assertGreater(new["score"], old["score"])
        self.assertEqual(top[0]["id"], new["id"])

    def test_schluesselwoerter_erhoehen_score(self):
        plain = self._article(id="a", title="Ein ganz normaler Titel")
        hot = self._article(id="b", title="Krieg in der Ukraine")
        fn.generate_top_news([plain, hot])
        self.assertGreater(hot["score"], plain["score"])

    def test_max_pro_quelle(self):
        articles = [self._article(id=f"h{i}", source="heise") for i in range(10)]
        articles += [self._article(id=f"g{i}", source="golem", sourceWeight=0.85) for i in range(10)]
        top = fn.generate_top_news(articles)
        for source in ("heise", "golem"):
            self.assertLessEqual(sum(1 for a in top if a["source"] == source), fn.MAX_PER_SOURCE)

    def test_top_limit(self):
        articles = [
            self._article(id=f"{src}{i}", source=src)
            for src in ("heise", "golem", "spiegel", "tagesschau")
            for i in range(10)
        ]
        self.assertLessEqual(len(fn.generate_top_news(articles)), fn.TOP_NEWS_LIMIT)

    def test_quellenuebergreifendes_thema_gewinnt(self):
        shared = "Bundestag beschliesst neues Klimapaket morgen"
        a = self._article(id="a", source="heise", title=shared)
        b = self._article(id="b", source="golem", title=shared, sourceWeight=0.9)
        alone = self._article(id="c", source="spiegel", title="Etwas völlig anderes ohne Bezug", sourceWeight=0.9)
        fn.generate_top_news([a, b, alone])
        self.assertGreater(a["score"], alone["score"])

    def test_eigene_quelle_zaehlt_nicht_als_verwandt(self):
        shared = "Bundestag beschliesst neues Klimapaket morgen"
        a = self._article(id="a", source="heise", title=shared)
        b = self._article(id="b", source="heise", title=shared)
        fn.generate_top_news([a, b])
        solo = self._article(id="c", source="heise", title=shared)
        fn.generate_top_news([solo])
        self.assertEqual(a["score"], solo["score"])


class TestPlausibility(unittest.TestCase):
    def test_leeres_ergebnis_abgelehnt(self):
        self.assertFalse(fn.is_plausible(0, 100))

    def test_einbruch_abgelehnt(self):
        self.assertFalse(fn.is_plausible(40, 100))

    def test_normaler_lauf_akzeptiert(self):
        self.assertTrue(fn.is_plausible(100, 100))
        self.assertTrue(fn.is_plausible(120, 100))

    def test_erster_lauf_akzeptiert(self):
        self.assertTrue(fn.is_plausible(40, 0))

    def test_unter_minimum_abgelehnt(self):
        self.assertFalse(fn.is_plausible(fn.MIN_ARTICLES - 1, 0))


class TestWriteJson(unittest.TestCase):
    def test_schreibt_und_hinterlaesst_keine_tempdatei(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.json")
            fn.write_json(path, {"a": "ä"})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"a": "ä"})
            self.assertEqual([f for f in os.listdir(tmp) if f.endswith(".tmp")], [])

    def test_previous_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news.json")
            self.assertEqual(fn.previous_count(path), 0)
            fn.write_json(path, {"articles": [{"id": "a"}, {"id": "b"}]})
            self.assertEqual(fn.previous_count(path), 2)


class TestFetchFeed(unittest.TestCase):
    """Prüft fetch_feed gegen einen vorgegebenen Feed, ohne Netzwerk."""

    FEED = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>Gueltiger Artikel</title>
        <link>https://example.com/a</link>
        <description>Eine Beschreibung.</description>
        <pubDate>{recent}</pubDate>
      </item>
      <item>
        <title>Zu alt</title>
        <link>https://example.com/b</link>
        <pubDate>{old}</pubDate>
      </item>
      <item>
        <title>Ohne Link</title>
        <pubDate>{recent}</pubDate>
      </item>
      <item>
        <title>Boeser Link</title>
        <link>javascript:alert(1)</link>
        <pubDate>{recent}</pubDate>
      </item>
    </channel></rss>"""

    def setUp(self):
        now = datetime.now(timezone.utc)
        fmt = "%a, %d %b %Y %H:%M:%S %z"
        self.raw = self.FEED.format(
            recent=now.strftime(fmt),
            old=(now - timedelta(hours=100)).strftime(fmt),
        ).encode("utf-8")
        self._original = fn.download_feed
        fn.download_feed = lambda name, url: self.raw

    def tearDown(self):
        fn.download_feed = self._original

    def test_filtert_korrekt(self):
        articles = fn.fetch_feed("test", {"url": "x", "weight": 1.0, "category": "Nachrichten"})
        titles = [a["title"] for a in articles]
        self.assertEqual(titles, ["Gueltiger Artikel"])

    def test_felder_vollstaendig(self):
        article = fn.fetch_feed("test", {"url": "x", "weight": 1.0, "category": "Nachrichten"})[0]
        for key in ("id", "source", "sourceWeight", "title", "link", "summary", "image",
                    "published", "category"):
            self.assertIn(key, article)
        self.assertEqual(article["source"], "test")
        self.assertTrue(article["published"].endswith(("+00:00", "Z")) or "+" in article["published"])

    def test_abruf_fehlgeschlagen_ergibt_leere_liste(self):
        fn.download_feed = lambda name, url: None
        self.assertEqual(fn.fetch_feed("test", {"url": "x", "weight": 1.0}), [])

    def test_unlesbarer_feed_ergibt_leere_liste(self):
        fn.download_feed = lambda name, url: b"kein xml"
        self.assertEqual(fn.fetch_feed("test", {"url": "x", "weight": 1.0}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
