#!/usr/bin/env python3
"""Tests für fetch_news.py – ohne Netzwerkzugriff, ohne zusätzliche Pakete.

Ausführen: python -m unittest discover -s scripts -p "test_*.py"
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import requests

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

    def test_read_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.json")
            self.assertIsNone(fn.read_json(path))
            fn.write_json(path, {"a": 1})
            self.assertEqual(fn.read_json(path), {"a": 1})
            with open(path, "w", encoding="utf-8") as f:
                f.write("kein json")
            self.assertIsNone(fn.read_json(path))


class TestAiSummarySchedule(unittest.TestCase):
    """07:23 und 19:23 Berliner Zeit – im Sommer wie im Winter."""

    @staticmethod
    def _utc(*args):
        return datetime(*args, tzinfo=timezone.utc)

    def test_sommerzeit_fenster(self):
        # CEST = UTC+2: 05:23 UTC ist 07:23 in Berlin.
        self.assertEqual(fn.ai_summary_slot(self._utc(2026, 7, 1, 5, 23)), "2026-07-01T07")
        self.assertEqual(fn.ai_summary_slot(self._utc(2026, 7, 1, 17, 23)), "2026-07-01T19")

    def test_winterzeit_fenster(self):
        # CET = UTC+1: dieselbe Ortszeit liegt eine Stunde später in UTC.
        self.assertEqual(fn.ai_summary_slot(self._utc(2026, 1, 15, 6, 23)), "2026-01-15T07")
        self.assertEqual(fn.ai_summary_slot(self._utc(2026, 1, 15, 18, 23)), "2026-01-15T19")

    def test_sommerzeit_utc_zeitpunkt_ist_im_winter_kein_fenster(self):
        self.assertIsNone(fn.ai_summary_slot(self._utc(2026, 1, 15, 5, 23)))
        self.assertIsNone(fn.ai_summary_slot(self._utc(2026, 7, 1, 6, 23)))

    def test_uebrige_stunden_ohne_fenster(self):
        for hour in range(24):
            moment = self._utc(2026, 7, 1, hour, 23)
            if hour in (5, 17):
                continue
            with self.subTest(hour=hour):
                self.assertIsNone(fn.ai_summary_slot(moment))

    def test_auto_erzeugt_nur_im_fenster(self):
        self.assertTrue(fn.should_generate_ai_summary("auto", self._utc(2026, 7, 1, 5, 23)))
        self.assertFalse(fn.should_generate_ai_summary("auto", self._utc(2026, 7, 1, 8, 23)))

    def test_auto_nur_einmal_pro_fenster(self):
        # Der :53-Lauf derselben Stunde soll nicht noch einmal fragen.
        existing = {"slot": "2026-07-01T07"}
        self.assertFalse(
            fn.should_generate_ai_summary("auto", self._utc(2026, 7, 1, 5, 53), existing)
        )
        self.assertTrue(
            fn.should_generate_ai_summary("auto", self._utc(2026, 7, 1, 17, 23), existing)
        )

    def test_force_und_skip(self):
        outside = self._utc(2026, 7, 1, 8, 23)
        inside = self._utc(2026, 7, 1, 5, 23)
        self.assertTrue(fn.should_generate_ai_summary("force", outside))
        self.assertFalse(fn.should_generate_ai_summary("skip", inside))

    def test_unbekannter_modus_verhaelt_sich_wie_auto(self):
        self.assertFalse(fn.should_generate_ai_summary("", self._utc(2026, 7, 1, 8, 23)))
        self.assertTrue(fn.should_generate_ai_summary(None, self._utc(2026, 7, 1, 5, 23)))


class TestDropStaleAiSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "ai-summary.json")
        self.now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, generated_at):
        payload = {"summary": "x"}
        if generated_at is not None:
            payload["generatedAt"] = generated_at
        fn.write_json(self.path, payload)
        return payload

    def test_frische_zusammenfassung_bleibt(self):
        payload = self._write((self.now - timedelta(hours=6)).isoformat())
        self.assertFalse(fn.drop_stale_ai_summary(self.path, payload, self.now))
        self.assertTrue(os.path.exists(self.path))

    def test_stuendlicher_lauf_loescht_nicht(self):
        # Zwischen zwei KI-Fenstern liegen zwölf Stunden; die dürfen nichts kosten.
        payload = self._write((self.now - timedelta(hours=12)).isoformat())
        self.assertFalse(fn.drop_stale_ai_summary(self.path, payload, self.now))
        self.assertTrue(os.path.exists(self.path))

    def test_ueberalterte_zusammenfassung_faellt_weg(self):
        payload = self._write((self.now - timedelta(hours=30)).isoformat())
        self.assertTrue(fn.drop_stale_ai_summary(self.path, payload, self.now))
        self.assertFalse(os.path.exists(self.path))

    def test_unlesbarer_zeitstempel_faellt_weg(self):
        for value in (None, "Unsinn", "2026-07-01T10:00:00"):
            with self.subTest(value=value):
                payload = self._write(value)
                self.assertTrue(fn.drop_stale_ai_summary(self.path, payload, self.now))
                self.assertFalse(os.path.exists(self.path))

    def test_ohne_datei_passiert_nichts(self):
        self.assertFalse(fn.drop_stale_ai_summary(self.path, None, self.now))


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


class FakeResponse:
    """Minimale requests.Response-Attrappe für die Gemini-Tests."""

    def __init__(self, status_code=200, payload=None, headers=None, text="Zusammenfassung",
                 json_error=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.json_error = json_error
        self._payload = payload if payload is not None else {
            "candidates": [{"content": {"parts": [{"text": text}]}}]
        }

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Server Error for url: "
                f"https://generativelanguage.googleapis.com/... ",
                response=self,
            )

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self._payload


class GeminiTestCase(unittest.TestCase):
    """Gemeinsame Verdrahtung: kein Netz, kein echtes Warten, fester Key."""

    API_KEY = "geheimer-testschluessel"
    PRIMARY = fn.GEMINI_MODEL
    FALLBACK = fn.GEMINI_FALLBACK_MODEL

    def setUp(self):
        self.sleeps = []
        patches = [
            mock.patch.dict(os.environ, {"GEMINI_API_KEY": self.API_KEY}),
            mock.patch.object(fn.time, "sleep", self.sleeps.append),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def _model_of(url):
        """Liest das Modell aus der Aufruf-URL – so, wie ein Log es dürfte."""
        return url.rsplit("/", 1)[-1].split(":")[0]

    def _run(self, responses):
        """Ruft generate_ai_summary mit vorgegebenen Antworten auf.

        `responses` ist entweder eine Liste in Aufrufreihenfolge (die letzte
        Antwort wiederholt sich) oder ein Dict pro Modell – dann entscheidet die
        Kaskade selbst, wen sie fragt. Einträge dürfen FakeResponse oder
        Exception sein.
        """
        calls = []

        def post(url, **kwargs):
            model = self._model_of(url)
            if isinstance(responses, dict):
                item = responses[model]
            else:
                item = responses[min(len(calls), len(responses) - 1)]
            calls.append({"url": url, "model": model, **kwargs})
            if isinstance(item, Exception):
                raise item
            return item

        buffer = io.StringIO()
        with mock.patch.object(fn.session, "post", post), redirect_stdout(buffer):
            result = fn.generate_ai_summary([{"title": "T", "source": "heise"}])
        self.log = buffer.getvalue()
        self.calls = calls
        self.models = [call["model"] for call in calls]
        return result


class TestRetryHelpers(unittest.TestCase):
    def test_transiente_status_sind_wiederholbar(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                error = requests.HTTPError(response=FakeResponse(status))
                self.assertTrue(fn.is_retryable_error(error))

    def test_dauerhafte_4xx_sind_nicht_wiederholbar(self):
        for status in (400, 401, 403, 404, 422):
            with self.subTest(status=status):
                error = requests.HTTPError(response=FakeResponse(status))
                self.assertFalse(fn.is_retryable_error(error))

    def test_netzwerkfehler_sind_wiederholbar(self):
        for error in (requests.ConnectionError("dns"), requests.Timeout("zu langsam")):
            with self.subTest(error=type(error).__name__):
                self.assertTrue(fn.is_retryable_error(error))

    def test_uebrige_requests_fehler_nicht(self):
        # Eine kaputte URL wird beim nächsten Versuch genauso kaputt sein.
        self.assertFalse(fn.is_retryable_error(requests.exceptions.MissingSchema("x")))

    def test_beschreibung_nennt_status_oder_klasse(self):
        self.assertEqual(fn.describe_error(requests.HTTPError(response=FakeResponse(503))), "HTTP 503")
        self.assertEqual(fn.describe_error(requests.Timeout("x")), "Timeout")

    def test_beschreibung_enthaelt_keine_url(self):
        error = requests.HTTPError("... url: https://…?key=geheim", response=FakeResponse(500))
        described = fn.describe_error(error)
        self.assertNotIn("://", described)
        self.assertNotIn("geheim", described)

    def test_retry_after_in_sekunden(self):
        self.assertEqual(fn.parse_retry_after("7"), 7.0)
        self.assertEqual(fn.parse_retry_after(" 2 "), 2.0)

    def test_retry_after_als_http_datum(self):
        now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        value = "Wed, 01 Jul 2026 12:00:05 GMT"
        self.assertAlmostEqual(fn.parse_retry_after(value, now) or 0.0, 5.0, places=1)

    def test_retry_after_unlesbar_oder_leer(self):
        for value in (None, "", "   ", "bald", "NaN", "inf"):
            with self.subTest(value=value):
                self.assertIsNone(fn.parse_retry_after(value))

    def test_retry_after_vergangenheit_ist_null(self):
        self.assertEqual(fn.parse_retry_after("-5"), 0.0)

    def test_retry_after_wird_gedeckelt(self):
        self.assertEqual(fn.parse_retry_after("3600"), fn.GEMINI_RETRY_MAX_DELAY)

    def test_backoff_waechst_exponentiell(self):
        for attempt, expected in enumerate((1, 2, 4, 8), start=1):
            with self.subTest(attempt=attempt):
                delay = fn.retry_delay(attempt)
                self.assertGreaterEqual(delay, expected)
                self.assertLess(delay, expected + fn.GEMINI_RETRY_JITTER)

    def test_retry_after_schlaegt_backoff(self):
        delay = fn.retry_delay(1, retry_after=7.0)
        self.assertGreaterEqual(delay, 7.0)
        self.assertLess(delay, 7.0 + fn.GEMINI_RETRY_JITTER)

    def test_backoff_wird_gedeckelt(self):
        self.assertLess(fn.retry_delay(20), fn.GEMINI_RETRY_MAX_DELAY + fn.GEMINI_RETRY_JITTER)

    def test_modellwechsel_wartet_nur_kurz(self):
        # Beim Wechsel ist das andere Modell das Mittel, nicht die Wartezeit.
        delay = fn.retry_delay(4, switching=True)
        self.assertGreaterEqual(delay, fn.GEMINI_SWITCH_DELAY)
        self.assertLess(delay, fn.GEMINI_SWITCH_DELAY + fn.GEMINI_RETRY_JITTER)

    def test_retry_after_schlaegt_auch_den_modellwechsel(self):
        delay = fn.retry_delay(2, retry_after=6.0, switching=True)
        self.assertGreaterEqual(delay, 6.0)


class TestModelCascade(unittest.TestCase):
    """Die Kaskade selbst: erst Primärmodell, dann bewährtes Fallback."""

    def test_budget_von_fuenf_aufrufen(self):
        self.assertEqual(len(fn.GEMINI_ATTEMPT_MODELS), 5)
        self.assertEqual(fn.GEMINI_MAX_ATTEMPTS, 5)

    def test_reihenfolge_ist_primaer_dann_fallback(self):
        self.assertEqual(
            fn.GEMINI_ATTEMPT_MODELS,
            (fn.GEMINI_MODEL,) * 2 + (fn.GEMINI_FALLBACK_MODEL,) * 3,
        )

    def test_modelle_sind_verschieden(self):
        self.assertEqual(fn.GEMINI_MODEL, "gemini-3.8-flash")
        self.assertEqual(fn.GEMINI_FALLBACK_MODEL, "gemini-2.5-flash")

    def test_url_enthaelt_das_modell(self):
        url = fn.gemini_url(fn.GEMINI_FALLBACK_MODEL)
        self.assertTrue(url.endswith(f"/{fn.GEMINI_FALLBACK_MODEL}:generateContent"))
        self.assertNotIn("key", url)

    def test_fatale_status_sind_nie_wiederholbar(self):
        self.assertFalse(fn.GEMINI_FATAL_STATUSES & fn.GEMINI_RETRY_STATUSES)
        for status in fn.GEMINI_FATAL_STATUSES:
            with self.subTest(status=status):
                self.assertFalse(fn.is_retryable_error(requests.HTTPError(response=FakeResponse(status))))


class TestErrorDetails(unittest.TestCase):
    """Nur Kennungen aus dem Fehlerbody – nie Prosa, URL oder Key."""

    SECRET = "AIzaSyGEHEIMERKEY"
    MESSAGE = (
        f"Quota exceeded for project 'news-aggregator' with key {SECRET}; "
        "prompt: Fasse die wichtigsten Nachrichten-Themen ... "
        "see https://console.cloud.google.com/iam-admin/quotas"
    )

    @staticmethod
    def _error(status_code=503, **body):
        return requests.HTTPError(response=FakeResponse(status_code, payload={"error": body}))

    def test_sichere_felder_werden_geloggt(self):
        error = self._error(
            503,
            code=503,
            status="UNAVAILABLE",
            message=self.MESSAGE,
            details=[{
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "SERVICE_UNAVAILABLE",
                "domain": "generativelanguage.googleapis.com",
                "metadata": {"model": "gemini-3.8-flash"},
            }],
        )
        self.assertEqual(
            fn.describe_error(error),
            "HTTP 503 api_status=UNAVAILABLE api_code=503 reason=SERVICE_UNAVAILABLE",
        )

    def test_message_url_und_key_niemals(self):
        described = fn.describe_error(self._error(
            429,
            code=429,
            status="RESOURCE_EXHAUSTED",
            message=self.MESSAGE,
            details=[{"reason": "RATE_LIMIT_EXCEEDED", "domain": "googleapis.com"}],
        ))
        self.assertIn("api_status=RESOURCE_EXHAUSTED", described)
        self.assertNotIn(self.SECRET, described)
        self.assertNotIn("Quota exceeded", described)
        self.assertNotIn("prompt", described)
        self.assertNotIn("://", described)
        self.assertNotIn("googleapis.com", described)

    def test_fehlende_felder_fallen_einfach_weg(self):
        self.assertEqual(fn.describe_error(self._error(429, code=429)), "HTTP 429 api_code=429")
        self.assertEqual(fn.describe_error(self._error(500, status="INTERNAL")),
                         "HTTP 500 api_status=INTERNAL")

    def test_ohne_fehlerbody_bleibt_das_bisherige_log(self):
        # Kein error-Objekt, kein Gewinn an Information: alles wie vorher.
        error = requests.HTTPError(response=FakeResponse(500, payload={"candidates": []}))
        self.assertEqual(fn.describe_error(error), "HTTP 500")

    def test_prosa_in_kennungsfeldern_wird_verworfen(self):
        described = fn.describe_error(self._error(
            403,
            status=self.MESSAGE,
            code="403 Forbidden: siehe https://example.com",
            details=[{"reason": "Der Schlüssel gilt nicht mehr"}],
        ))
        self.assertEqual(described, "HTTP 403")

    def test_erstes_brauchbares_reason_gewinnt(self):
        described = fn.describe_error(self._error(
            429,
            details=[
                {"@type": "type.googleapis.com/google.rpc.Help"},
                {"reason": "RATE_LIMIT_EXCEEDED"},
                {"reason": "ZWEITER_GRUND"},
            ],
        ))
        self.assertEqual(described, "HTTP 429 reason=RATE_LIMIT_EXCEEDED")

    def test_kaputte_strukturen_crashen_nicht(self):
        bodies = (
            {"error": "kaputt"},
            {"error": {"details": "keine Liste"}},
            {"error": {"details": ["Text", 5, None]}},
            {"error": {"status": {"nested": "dict"}, "code": [503]}},
            ["ganz anderes JSON"],
            None,
        )
        for body in bodies:
            with self.subTest(body=body):
                error = requests.HTTPError(response=FakeResponse(500, payload=body))
                self.assertEqual(fn.describe_error(error), "HTTP 500")

    def test_ungueltiges_json_wird_still_ignoriert(self):
        for problem in (ValueError("kein JSON"), TypeError("x"), requests.RequestException("weg")):
            with self.subTest(problem=type(problem).__name__):
                error = requests.HTTPError(response=FakeResponse(500, json_error=problem))
                self.assertEqual(fn.describe_error(error), "HTTP 500")

    def test_netzwerkfehler_ohne_antwort_unveraendert(self):
        self.assertEqual(fn.describe_error(requests.Timeout("zu langsam")), "Timeout")
        self.assertEqual(fn.error_details(requests.Timeout("zu langsam")), "")

    def test_safe_token_laesst_nur_kennungen_durch(self):
        for value in ("UNAVAILABLE", "RATE_LIMIT_EXCEEDED", "gemini-2.5-flash", 503, " 503 "):
            with self.subTest(value=value):
                self.assertIsNotNone(fn.safe_token(value))
        for value in (None, True, False, "", "   ", "mit Leerzeichen", "a" * 65,
                      "https://x.de", {"a": 1}, ["a"], 5.5, "key=abc:def"):
            with self.subTest(value=value):
                self.assertIsNone(fn.safe_token(value))


class TestAiPrompt(unittest.TestCase):
    """Der Prompt muss das Format erzwingen, das die App darstellen kann."""

    def _prompt(self, count=3):
        articles = [{"title": f"Schlagzeile {i}", "source": "heise"} for i in range(count)]
        return fn.build_ai_prompt(articles)

    def test_fordert_drei_bis_fuenf_zeilen(self):
        self.assertIn("3 bis 5 Zeilen", self._prompt())

    def test_fordert_bullet_prefix_und_einen_satz(self):
        prompt = self._prompt()
        self.assertIn('beginnt mit "- "', prompt)
        self.assertIn("genau einen Satz", prompt)

    def test_erlaubt_kurztitel(self):
        self.assertIn("**Kurztitel:**", self._prompt())

    def test_verbietet_rahmenwerk(self):
        prompt = self._prompt()
        for verbot in ("Keine Einleitung", "keine Überschrift", "kein Schlusswort",
                       "Keine Leerzeilen", "keine Nummerierung", "keine weiteren Absätze"):
            with self.subTest(verbot=verbot):
                self.assertIn(verbot, prompt)

    def test_schlagzeilen_stehen_am_ende(self):
        prompt = self._prompt(2)
        self.assertTrue(prompt.rstrip().endswith("- Schlagzeile 1 (heise)"))
        self.assertIn("Schlagzeilen:", prompt)

    def test_begrenzt_die_schlagzeilen(self):
        prompt = self._prompt(50)
        self.assertEqual(prompt.count("(heise)"), fn.AI_SUMMARY_MAX_HEADLINES)

    def test_ohne_artikel_kein_absturz(self):
        self.assertTrue(fn.build_ai_prompt([]).endswith("Schlagzeilen:\n"))


class TestNormalizeSummary(unittest.TestCase):
    """Minimale Kosmetik: Artefakte weg, Inhalt bleibt."""

    def test_einleitungszeile_faellt_weg(self):
        text = "Hier sind die wichtigsten Themen des Tages:\n- Erster Punkt.\n- Zweiter Punkt."
        self.assertEqual(fn.normalize_summary(text), "- Erster Punkt.\n- Zweiter Punkt.")

    def test_doppelpunkt_nach_der_aufzaehlung_bleibt(self):
        # Nach dem ersten Punkt ist ein Doppelpunkt am Zeilenende womöglich Inhalt.
        text = "- Erster Punkt.\nOffene Frage bleibt:"
        self.assertEqual(fn.normalize_summary(text), text)

    def test_sternchen_und_bullet_werden_zu_strich(self):
        text = "* Erster Punkt.\n• Zweiter Punkt.\n•Dritter Punkt."
        self.assertEqual(
            fn.normalize_summary(text),
            "- Erster Punkt.\n- Zweiter Punkt.\n- Dritter Punkt.",
        )

    def test_kurztitel_bleibt_unangetastet(self):
        text = "- **Ukraine:** Die Front bewegt sich.\n**Börse:** Der Dax steigt."
        self.assertEqual(fn.normalize_summary(text), text)

    def test_html_bleibt_text(self):
        text = "- <b>Fett</b> & <script>alert(1)</script> bleiben stehen."
        self.assertEqual(fn.normalize_summary(text), text)

    def test_leerzeilen_und_einrueckung_verschwinden(self):
        text = "\n   - Erster Punkt.  \n\n\t*   Zweiter Punkt.\n\n"
        self.assertEqual(fn.normalize_summary(text), "- Erster Punkt.\n- Zweiter Punkt.")

    def test_inhalt_wird_nie_abgeschnitten(self):
        text = "- Ein Satz mit - Strich und * Stern und : Doppelpunkt mittendrin."
        self.assertEqual(fn.normalize_summary(text), text)

    def test_fliesstext_ohne_aufzaehlung_bleibt_erhalten(self):
        # Lieber unschön als weg: das Modell hat sich nicht gehalten, aber der
        # Inhalt ist das Einzige, was der Lauf hat.
        text = "Der Tag war ruhig.\nEs gab wenig Neues."
        self.assertEqual(fn.normalize_summary(text), text)

    def test_leere_eingaben(self):
        for value in ("", None, "\n\n   \n", "Nur eine Anmoderation:"):
            with self.subTest(value=value):
                self.assertEqual(fn.normalize_summary(value), "")

    def test_ergebnis_hat_keine_leerzeile(self):
        text = "- Erster Punkt.\n\n- Zweiter Punkt.\n\n- Dritter Punkt."
        result = fn.normalize_summary(text)
        self.assertEqual(len(result.splitlines()), 3)
        self.assertTrue(all(line.startswith("- ") for line in result.splitlines()))


class TestGenerateAiSummary(GeminiTestCase):
    def test_erfolg_direkt_nennt_das_primaermodell(self):
        result = self._run([FakeResponse(200, text="Punkt eins")])
        self.assertEqual((result or {}).get("summary"), "Punkt eins")
        self.assertEqual((result or {}).get("model"), self.PRIMARY)
        self.assertEqual(self.models, [self.PRIMARY])
        self.assertEqual(self.sleeps, [])

    def test_metadaten_bleiben_schlank(self):
        # Die App liest summary und generatedAt; model kommt nur dazu.
        result = self._run([FakeResponse(200)])
        self.assertEqual(set(result or {}), {"summary", "generatedAt", "model"})
        self.assertIsInstance((result or {})["generatedAt"], str)

    def test_primaermodell_erholt_sich_beim_zweiten_versuch(self):
        result = self._run([FakeResponse(503), FakeResponse(200, text="Nach Retry")])
        self.assertEqual((result or {}).get("summary"), "Nach Retry")
        self.assertEqual((result or {}).get("model"), self.PRIMARY)
        self.assertEqual(self.models, [self.PRIMARY, self.PRIMARY])
        self.assertEqual(len(self.sleeps), 1)
        self.assertIn("HTTP 503", self.log)

    def test_503_auf_primaer_fuehrt_zum_fallback(self):
        result = self._run({self.PRIMARY: FakeResponse(503), self.FALLBACK: FakeResponse(200)})
        self.assertEqual((result or {}).get("model"), self.FALLBACK)
        self.assertEqual(self.models, [self.PRIMARY, self.PRIMARY, self.FALLBACK])
        self.assertEqual(len(self.sleeps), 2)
        self.assertIn(f"erzeugt mit {self.FALLBACK}", self.log)

    def test_erst_retry_primaer_dann_wechsel(self):
        self._run([FakeResponse(500), requests.Timeout("zu langsam"), FakeResponse(200)])
        self.assertEqual(self.models, [self.PRIMARY, self.PRIMARY, self.FALLBACK])
        # Warten beim selben Modell, kurzer Sprung beim Wechsel.
        self.assertGreaterEqual(self.sleeps[0], fn.GEMINI_RETRY_BASE_DELAY)
        self.assertLess(self.sleeps[1], fn.GEMINI_SWITCH_DELAY + fn.GEMINI_RETRY_JITTER)
        self.assertIn(f"nächster Versuch mit {self.FALLBACK}", self.log)

    def test_429_wechselt_ebenfalls_auf_das_fallbackmodell(self):
        result = self._run({
            self.PRIMARY: FakeResponse(429, headers={"Retry-After": "2"}),
            self.FALLBACK: FakeResponse(200, text="Ohne Drosselung"),
        })
        self.assertEqual((result or {}).get("model"), self.FALLBACK)
        self.assertIn("HTTP 429", self.log)

    def test_mehrere_transiente_fehler_bis_zum_erfolg(self):
        result = self._run([
            FakeResponse(500),
            requests.ConnectionError("Leitung weg"),
            FakeResponse(502),
            FakeResponse(200, text="Endlich"),
        ])
        self.assertEqual((result or {}).get("summary"), "Endlich")
        self.assertEqual((result or {}).get("model"), self.FALLBACK)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(len(self.sleeps), 3)
        self.assertIn("ConnectionError", self.log)

    def test_fallback_scheitert_bis_zum_budget(self):
        self.assertIsNone(
            self._run({self.PRIMARY: FakeResponse(503), self.FALLBACK: FakeResponse(503)})
        )
        self.assertEqual(self.models, list(fn.GEMINI_ATTEMPT_MODELS))
        self.assertEqual(len(self.calls), fn.GEMINI_MAX_ATTEMPTS)
        self.assertEqual(len(self.sleeps), fn.GEMINI_MAX_ATTEMPTS - 1)
        self.assertIn(f"{fn.GEMINI_MAX_ATTEMPTS} Versuchen", self.log)

    def test_dauerhafte_fehler_ohne_retry_und_ohne_fallback(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                self.sleeps.clear()
                self.assertIsNone(self._run([FakeResponse(status)]))
                self.assertEqual(self.models, [self.PRIMARY])
                self.assertNotIn(self.FALLBACK, self.log)
                self.assertEqual(self.sleeps, [])
                self.assertIn(f"HTTP {status}", self.log)

    def test_retry_after_bestimmt_die_wartezeit(self):
        result = self._run([
            FakeResponse(429, headers={"Retry-After": "7"}),
            FakeResponse(200, text="Nach Drosselung"),
        ])
        self.assertEqual((result or {}).get("summary"), "Nach Drosselung")
        self.assertEqual(len(self.sleeps), 1)
        self.assertGreaterEqual(self.sleeps[0], 7.0)
        self.assertLess(self.sleeps[0], 7.0 + fn.GEMINI_RETRY_JITTER)

    def test_unlesbares_retry_after_faellt_auf_backoff_zurueck(self):
        self._run([FakeResponse(429, headers={"Retry-After": "gleich"}), FakeResponse(200)])
        self.assertGreaterEqual(self.sleeps[0], fn.GEMINI_RETRY_BASE_DELAY)
        self.assertLess(self.sleeps[0], fn.GEMINI_RETRY_BASE_DELAY + fn.GEMINI_RETRY_JITTER)

    def test_dauertimeout_endet_nach_maximalversuchen(self):
        self.assertIsNone(self._run([requests.Timeout("zu langsam")]))
        self.assertEqual(len(self.calls), fn.GEMINI_MAX_ATTEMPTS)
        self.assertEqual(self.models, list(fn.GEMINI_ATTEMPT_MODELS))

    def test_jeder_versuch_nennt_nummer_und_modell(self):
        self._run([FakeResponse(503)])
        for attempt, model in enumerate(fn.GEMINI_ATTEMPT_MODELS, start=1):
            self.assertIn(f"Versuch {attempt}/{fn.GEMINI_MAX_ATTEMPTS} ({model})", self.log)

    def test_log_verraet_weder_key_noch_url(self):
        self._run([FakeResponse(503)])
        self.assertNotIn(self.API_KEY, self.log)
        self.assertNotIn("x-goog-api-key", self.log)
        self.assertNotIn("://", self.log)

    def test_fehlerdetails_stehen_neben_status_und_modell(self):
        error_body = {"error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": f"Quota exceeded with key {self.API_KEY}, prompt: Fasse die ...",
            "details": [{"reason": "RATE_LIMIT_EXCEEDED", "domain": "googleapis.com"}],
        }}
        self._run({
            self.PRIMARY: FakeResponse(429, payload=error_body),
            self.FALLBACK: FakeResponse(200),
        })
        self.assertIn(
            f"Versuch 1/{fn.GEMINI_MAX_ATTEMPTS} ({self.PRIMARY}) fehlgeschlagen: "
            "HTTP 429 api_status=RESOURCE_EXHAUSTED api_code=429 reason=RATE_LIMIT_EXCEEDED",
            self.log,
        )
        # Retry und Fallback bleiben davon unberührt.
        self.assertEqual(self.models, [self.PRIMARY, self.PRIMARY, self.FALLBACK])
        self.assertNotIn(self.API_KEY, self.log)
        self.assertNotIn("Quota exceeded", self.log)
        self.assertNotIn("googleapis.com", self.log)

    def test_kaputter_fehlerbody_aendert_nichts_am_ablauf(self):
        self.assertIsNone(self._run([FakeResponse(503, json_error=ValueError("kein JSON"))]))
        self.assertEqual(self.models, list(fn.GEMINI_ATTEMPT_MODELS))
        self.assertIn(f"({self.PRIMARY}) fehlgeschlagen: HTTP 503", self.log)
        self.assertNotIn("api_status", self.log)

    def test_unerwartete_antwort_ohne_wiederholung(self):
        self.assertIsNone(self._run([FakeResponse(200, payload={"candidates": []})]))
        self.assertEqual(self.models, [self.PRIMARY])
        self.assertIn("unerwartete Antwort", self.log)

    def test_leere_antwort_ergibt_none(self):
        self.assertIsNone(self._run([FakeResponse(200, text="   ")]))
        self.assertEqual(len(self.calls), 1)

    def test_antwort_wird_vor_dem_speichern_geglaettet(self):
        markdown = (
            "Hier sind die wichtigsten Themen des Tages:\n\n"
            "* **Ukraine:** Die Front bewegt sich kaum.\n"
            "• Der Dax schließt im Plus.\n"
            "-   Apple zeigt eine neue Uhr.\n"
        )
        result = self._run([FakeResponse(200, text=markdown)])
        self.assertEqual(
            (result or {}).get("summary"),
            "- **Ukraine:** Die Front bewegt sich kaum.\n"
            "- Der Dax schließt im Plus.\n"
            "- Apple zeigt eine neue Uhr.",
        )

    def test_nur_anmoderation_gilt_als_leer(self):
        self.assertIsNone(self._run([FakeResponse(200, text="Hier die Themen des Tages:")]))
        self.assertEqual(self.models, [self.PRIMARY])
        self.assertIn("leere Antwort", self.log)

    def test_prompt_traegt_die_formatregeln(self):
        self._run([FakeResponse(200)])
        sent = self.calls[0]["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn("3 bis 5 Zeilen", sent)
        self.assertIn('beginnt mit "- "', sent)
        self.assertTrue(sent.endswith("- T (heise)"))

    def test_key_steht_im_header_nicht_in_der_url(self):
        self._run([FakeResponse(200)])
        self.assertEqual(self.calls[0]["headers"]["x-goog-api-key"], self.API_KEY)
        self.assertNotIn(self.API_KEY, self.calls[0]["url"])

    def test_ohne_api_key_kein_aufruf(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(fn.session, "post", side_effect=AssertionError("kein Aufruf")):
                with redirect_stdout(io.StringIO()):
                    self.assertIsNone(fn.generate_ai_summary([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
