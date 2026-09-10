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


class ArticleFactory:
    """Basis für Scoring- und Cluster-Tests: eindeutige Titel, gleiche Zeit."""

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
        base.setdefault("link", f"https://example.com/{base['id']}")
        return base

    def _distinct(self, index, **kwargs):
        # Themen-Cluster würden gleiche Titel zusammenfassen; wo es um Scoring
        # oder Limits geht, muss jeder Artikel ein eigenes Thema sein.
        words = ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa")
        word = words[index % len(words)]
        return self._article(title=f"{word}werk meldet {word}zahlen aus {word}stadt", **kwargs)


class TestTokenize(unittest.TestCase):
    def test_kurze_woerter_fallen_weg(self):
        self.assertEqual(fn.tokenize("Der Rat tut es"), set())

    def test_leerer_text(self):
        self.assertEqual(fn.tokenize(""), set())

    def test_stopwoerter_fallen_weg(self):
        self.assertNotIn("nicht", fn.tokenize("Das gilt nicht"))

    def test_flexion_wird_vereinheitlicht(self):
        self.assertEqual(fn.tokenize("Republikaner"), fn.tokenize("Republikanern"))

    def test_umlaute_werden_gefaltet(self):
        self.assertEqual(fn.tokenize("Wähler"), {"wahl"})

    def test_tausendertrenner_vereinheitlicht(self):
        self.assertIn("5000", fn.tokenize("5.000 Dollar"))
        self.assertIn("5000", fn.tokenize("5000 Dollar"))

    def test_datum_bleibt_unberuehrt(self):
        self.assertNotIn("09092026", fn.tokenize("09.09.2026"))


class TestClustering(ArticleFactory, unittest.TestCase):
    # Echte Schlagzeilen und Vorspänne desselben Ereignisses: unterschiedlich
    # formuliert, gleiche Nachricht. Genau der Fall, der doppelt oben stand.
    def _trump_tagesschau(self, **kwargs):
        return self._article(
            source="tagesschau",
            sourceWeight=1.0,
            category="Politik",
            title="Trump lockt auf Parteitag Wähler mit 5.000-Dollar-Versprechen",
            summary=(
                "Den Republikanern droht bei den US-Kongresswahlen im November eine "
                "Schlappe. Die Umfragewerte von Präsident Trump sinken. Er verspricht "
                "im Falle eines Wahlsiegs 5.000 Dollar für jeden Erwachsenen."
            ),
            **kwargs,
        )

    def _trump_spiegel(self, **kwargs):
        return self._article(
            source="spiegel",
            sourceWeight=0.95,
            category="Politik",
            title="USA: Donald Trump bietet US-Bürgern 5000 Dollar",
            summary=(
                "Beim Parteitag der US-Republikaner peitscht Präsident Trump seine "
                "Partei auf den Wahlkampfendspurt ein. Er bietet Bürgern Geld für ihre "
                "Stimme: 5000 Dollar, falls die Republikaner die Midterms gewinnen."
            ),
            **kwargs,
        )

    def test_gleiche_nachricht_erscheint_nur_einmal(self):
        ts = self._trump_tagesschau(id="a")
        sp = self._trump_spiegel(id="b")
        top = fn.generate_top_news([sp, ts])
        self.assertEqual([a["id"] for a in top], ["a"])

    def test_bessere_quelle_vertritt_das_thema(self):
        # Reihenfolge der Eingabe darf die Wahl des Vertreters nicht bestimmen.
        for order in ([0, 1], [1, 0]):
            articles = [self._trump_tagesschau(id="ts"), self._trump_spiegel(id="sp")]
            top = fn.generate_top_news([articles[i] for i in order])
            self.assertEqual(top[0]["source"], "tagesschau")

    def test_weitere_quellen_stehen_in_coverage(self):
        ts = self._trump_tagesschau(id="a")
        sp = self._trump_spiegel(id="b")
        top = fn.generate_top_news([ts, sp])
        coverage = top[0]["coverage"]
        self.assertEqual(coverage["sourceCount"], 2)
        self.assertEqual([o["source"] for o in coverage["others"]], ["spiegel"])
        self.assertEqual(coverage["others"][0]["link"], sp["link"])

    def test_einzelmeldung_ohne_coverage(self):
        top = fn.generate_top_news([self._distinct(0, id="a")])
        self.assertNotIn("coverage", top[0])

    def test_verschiedene_themen_bleiben_getrennt(self):
        a = self._article(id="a", title="Bundesbank senkt Prognose für das Wachstum")
        b = self._article(id="b", title="Apple stellt neue Uhr mit Sensoren vor")
        top = fn.generate_top_news([a, b])
        self.assertEqual(len(top), 2)

    def test_gemeinsames_themenfeld_reicht_nicht(self):
        # Zwei geteilte Wörter ziehen sonst ein ganzes Themenfeld zusammen.
        a = self._article(id="a", title="Sachsen-Anhalt bekommt Fördermittel für Verkehrsprojekte")
        b = self._article(id="b", title="Sachsen-Anhalt streitet über Bildungspolitik")
        self.assertEqual(len(fn.generate_top_news([a, b])), 2)

    def test_naechste_quelle_uebernimmt_wenn_ausgereizt(self):
        # tagesschau ist ausgereizt; das nächste Thema soll trotzdem erscheinen,
        # dann vertreten durch spiegel.
        articles = []
        for i in range(fn.MAX_PER_SOURCE + 1):
            articles.append(
                self._distinct(i, id=f"ts{i}", source="tagesschau", sourceWeight=1.0)
            )
            articles.append(self._distinct(i, id=f"sp{i}", source="spiegel", sourceWeight=0.95))

        top = fn.generate_top_news(articles)
        self.assertEqual(len(top), fn.MAX_PER_SOURCE + 1)
        self.assertEqual(sum(1 for a in top if a["source"] == "tagesschau"), fn.MAX_PER_SOURCE)
        self.assertEqual(sum(1 for a in top if a["source"] == "spiegel"), 1)


class TestScoring(ArticleFactory, unittest.TestCase):
    def test_neuer_artikel_schlaegt_alten(self):
        old = self._distinct(
            0, id="a", published=(datetime.now(timezone.utc) - timedelta(hours=40)).isoformat()
        )
        new = self._distinct(1, id="b")
        top = fn.generate_top_news([old, new])
        self.assertGreater(new["score"], old["score"])
        self.assertEqual(top[0]["id"], new["id"])

    def test_schluesselwoerter_erhoehen_score(self):
        plain = self._distinct(0, id="a")
        hot = self._article(id="b", title="Krieg in der Ukraine")
        fn.generate_top_news([plain, hot])
        self.assertGreater(hot["score"], plain["score"])

    def test_max_pro_quelle(self):
        articles = [self._distinct(i, id=f"h{i}", source="heise") for i in range(10)]
        articles += [
            self._distinct(i, id=f"g{i}", source="golem", sourceWeight=0.85) for i in range(10)
        ]
        top = fn.generate_top_news(articles)
        for source in ("heise", "golem"):
            self.assertLessEqual(sum(1 for a in top if a["source"] == source), fn.MAX_PER_SOURCE)

    def test_top_limit(self):
        articles = [
            self._distinct(i, id=f"{src}{i}", source=src)
            for src in ("heise", "golem", "spiegel", "tagesschau")
            for i in range(10)
        ]
        self.assertLessEqual(len(fn.generate_top_news(articles)), fn.TOP_NEWS_LIMIT)

    def test_mehr_quellen_heben_das_thema(self):
        title = "Bundestag beschliesst Klimapaket mit Milliardenhilfen"
        a = self._article(id="a", source="heise", title=title)
        b = self._article(id="b", source="golem", title=title, sourceWeight=0.9)
        alone = self._distinct(5, id="c", source="spiegel", sourceWeight=0.9)
        fn.generate_top_news([a, b, alone])
        self.assertGreater(a["score"], alone["score"])

    def test_eine_quelle_hebt_sich_nicht_selbst(self):
        title = "Bundestag beschliesst Klimapaket mit Milliardenhilfen"
        doubled = self._article(id="a", source="heise", title=title)
        fn.generate_top_news([doubled, self._article(id="b", source="heise", title=title)])
        solo = self._article(id="c", source="heise", title=title)
        fn.generate_top_news([solo])
        self.assertEqual(doubled["score"], solo["score"])


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
