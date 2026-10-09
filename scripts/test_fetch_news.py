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
from pathlib import Path
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
        words = ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa",
                 "Lambda", "Mysa", "Nyra", "Xena", "Omikron", "Pira", "Rhoda", "Sigma", "Taura", "Vesta")
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

    def test_keine_nachricht_faellt_wegen_quellenbalance_heraus(self):
        # Früher warf eine harte Obergrenze pro Quelle Themen aus der Liste.
        # Jetzt bleiben alle Themen drin, vertreten von ihrer besten Quelle.
        articles = []
        for i in range(8):
            articles.append(
                self._distinct(i, id=f"ts{i}", source="tagesschau", sourceWeight=1.0)
            )

        top = fn.generate_top_news(articles)
        self.assertEqual(len(top), 8)
        self.assertEqual(sum(1 for a in top if a["source"] == "tagesschau"), 8)


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

    def test_vielfalt_entscheidet_im_fenster(self):
        # Mehr starke Themen derselben Quelle als Plätze, dazu ein knapp
        # schwächeres einer anderen Quelle: im Fenster zieht die bisher
        # seltenere Quelle vor, sonst fiele sie ganz heraus.
        now = datetime.now(timezone.utc)
        articles = [
            self._distinct(
                i,
                id=f"h{i}",
                source="heise",
                sourceWeight=0.9,
                published=(now - timedelta(minutes=i)).isoformat(),
            )
            for i in range(fn.TOP_NEWS_LIMIT + 1)
        ]
        outsider = self._distinct(
            19, id="g", source="golem", sourceWeight=0.85, published=now.isoformat()
        )

        top = fn.generate_top_news(articles + [outsider])
        self.assertLess(abs(outsider["score"] - articles[0]["score"]), fn.DIVERSITY_WINDOW)
        self.assertIn("g", [a["id"] for a in top])

    def test_score_schlaegt_vielfalt_ausserhalb_des_fensters(self):
        # Dasselbe Bild, nur liegt das Thema der seltenen Quelle weit zurück –
        # dann darf Vielfalt es nicht nach vorne ziehen.
        now = datetime.now(timezone.utc)
        articles = [
            self._distinct(
                i,
                id=f"h{i}",
                source="heise",
                sourceWeight=0.9,
                published=(now - timedelta(minutes=i)).isoformat(),
            )
            for i in range(fn.TOP_NEWS_LIMIT)
        ]
        outsider = self._distinct(
            19,
            id="g",
            source="golem",
            sourceWeight=0.85,
            published=(now - timedelta(hours=30)).isoformat(),
        )

        top = fn.generate_top_news(articles + [outsider])
        self.assertGreater(articles[0]["score"] - outsider["score"], fn.DIVERSITY_WINDOW)
        self.assertEqual(len(top), fn.TOP_NEWS_LIMIT)
        self.assertNotIn("g", [a["id"] for a in top])

    def test_auswahl_ist_unabhaengig_von_der_eingabereihenfolge(self):
        now = datetime.now(timezone.utc)

        def build():
            items = []
            for i in range(fn.TOP_NEWS_LIMIT + 5):
                source, weight = ("heise", 0.9) if i % 2 else ("golem", 0.85)
                items.append(
                    self._distinct(
                        i,
                        id=f"a{i}",
                        source=source,
                        sourceWeight=weight,
                        published=(now - timedelta(minutes=i)).isoformat(),
                    )
                )
            return items

        forward = [a["id"] for a in fn.generate_top_news(build())]
        backward = [a["id"] for a in fn.generate_top_news(list(reversed(build())))]
        self.assertEqual(forward, backward)

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

    def test_mehrquellenbonus_nimmt_ab(self):
        # Die zweite Quelle bestätigt eine Meldung, die vierte wiederholt sie
        # nur noch: der Bonus wächst logarithmisch.
        title = "Bundestag beschliesst Klimapaket mit Milliardenhilfen"
        sources = ("tagesschau", "spiegel", "heise", "golem")

        def score_with(count):
            articles = [
                self._article(id=f"{source}{count}", source=source, sourceWeight=1.0, title=title)
                for source in sources[:count]
            ]
            fn.generate_top_news(articles)
            return articles[0]["score"]

        one, two, three, four = (score_with(n) for n in (1, 2, 3, 4))
        self.assertAlmostEqual(two - one, 8, places=1)
        self.assertAlmostEqual(three - one, 12.7, places=1)
        self.assertAlmostEqual(four - one, 16, places=1)
        self.assertGreater(two - one, three - two)
        self.assertGreater(three - two, four - three)

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
    """Der Abstand entscheidet, nicht die Uhrzeit: zwölf Stunden oder mehr."""

    NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    def _existing(self, age=None, generated_at=None):
        if age is not None:
            generated_at = (self.NOW - age).isoformat()
        return {"summary": "x", "generatedAt": generated_at}

    def _auto(self, existing):
        return fn.should_generate_ai_summary("auto", self.NOW, existing)

    def test_ohne_bisherigen_stand_wird_erzeugt(self):
        self.assertTrue(self._auto(None))

    def test_ohne_zeitstempel_wird_erzeugt(self):
        self.assertTrue(self._auto({"summary": "x"}))

    def test_unlesbarer_zeitstempel_wird_erzeugt(self):
        # Ohne Zeitzone ist der Vergleich ein TypeError, Unsinn ein ValueError –
        # ein Stand, dessen Alter niemand kennt, zählt als keiner.
        for value in (None, "", "Unsinn", "2026-07-01T10:00:00", 1751371200, {"a": 1}):
            with self.subTest(value=value):
                self.assertTrue(self._auto(self._existing(generated_at=value)))

    def test_kaputte_datei_wird_erzeugt(self):
        # read_json kann auch etwas liefern, das kein Objekt ist.
        for existing in ([], "kein objekt", 5):
            with self.subTest(existing=existing):
                self.assertTrue(self._auto(existing))

    def test_unter_zwoelf_stunden_wird_nicht_erzeugt(self):
        for hours in (0, 1, 6, 11):
            with self.subTest(hours=hours):
                self.assertFalse(self._auto(self._existing(timedelta(hours=hours))))
        self.assertFalse(
            self._auto(self._existing(timedelta(hours=12) - timedelta(minutes=1)))
        )

    def test_genau_zwoelf_stunden_wird_erzeugt(self):
        self.assertTrue(self._auto(self._existing(timedelta(hours=12))))

    def test_ueber_zwoelf_stunden_wird_erzeugt(self):
        for hours in (13, 24, 100):
            with self.subTest(hours=hours):
                self.assertTrue(self._auto(self._existing(timedelta(hours=hours))))

    def test_zeitzone_des_zeitstempels_ist_egal(self):
        # Derselbe Moment, nur anders notiert: 11:00+02:00 ist 09:00 UTC.
        self.assertFalse(self._auto({"generatedAt": "2026-07-01T11:00:00+02:00"}))
        self.assertTrue(self._auto({"generatedAt": "2026-07-01T02:00:00+02:00"}))

    def test_keine_uhrzeitabhaengigkeit(self):
        # Früher entschied die Berliner Ortszeit; jetzt zählt nur der Abstand.
        stale = self._existing(timedelta(hours=13))
        fresh = self._existing(timedelta(hours=2))
        for hour in range(24):
            moment = datetime(2026, 1, 15, hour, 23, tzinfo=timezone.utc)
            with self.subTest(hour=hour):
                self.assertTrue(
                    fn.should_generate_ai_summary(
                        "auto", moment, {"generatedAt": (moment - timedelta(hours=13)).isoformat()}
                    )
                )
                self.assertFalse(
                    fn.should_generate_ai_summary(
                        "auto", moment, {"generatedAt": (moment - timedelta(hours=2)).isoformat()}
                    )
                )
        self.assertTrue(self._auto(stale))
        self.assertFalse(self._auto(fresh))

    def test_verpasster_lauf_wird_nachgeholt(self):
        # Fällt ein Lauf aus, wartet der nächste nicht auf ein festes Fenster.
        self.assertTrue(self._auto(self._existing(timedelta(hours=12, minutes=1))))

    def test_force_und_skip(self):
        fresh = self._existing(timedelta(hours=1))
        stale = self._existing(timedelta(hours=30))
        self.assertTrue(fn.should_generate_ai_summary("force", self.NOW, fresh))
        self.assertFalse(fn.should_generate_ai_summary("skip", self.NOW, stale))
        self.assertFalse(fn.should_generate_ai_summary("skip", self.NOW, None))

    def test_schreibweisen_von_force_und_skip(self):
        fresh = self._existing(timedelta(hours=1))
        for mode in ("force", "FORCE", " Force ", "always", "true", "1", "yes", "on"):
            with self.subTest(mode=mode):
                self.assertTrue(fn.should_generate_ai_summary(mode, self.NOW, fresh))
        for mode in ("skip", "SKIP", " Skip ", "never", "false", "0", "no", "off"):
            with self.subTest(mode=mode):
                self.assertFalse(fn.should_generate_ai_summary(mode, self.NOW, None))

    def test_unbekannter_modus_verhaelt_sich_wie_auto(self):
        fresh = self._existing(timedelta(hours=1))
        stale = self._existing(timedelta(hours=13))
        for mode in ("", None, "auto", "vielleicht"):
            with self.subTest(mode=mode):
                self.assertFalse(fn.should_generate_ai_summary(mode, self.NOW, fresh))
                self.assertTrue(fn.should_generate_ai_summary(mode, self.NOW, stale))

    def test_intervall_bleibt_unter_der_verfallsgrenze(self):
        # Sonst verschwände die Zusammenfassung, bevor eine neue fällig wäre.
        self.assertEqual(fn.AI_SUMMARY_INTERVAL_HOURS, 12)
        self.assertLess(fn.AI_SUMMARY_INTERVAL_HOURS, fn.AI_SUMMARY_MAX_AGE_HOURS)

    def test_zeitfenster_sind_restlos_verschwunden(self):
        for name in ("ai_summary_slot", "AI_SUMMARY_HOURS", "berlin_tz", "BERLIN_TZ_NAME"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(fn, name))


class TestAiSummaryAge(unittest.TestCase):
    """Gemeinsame Altersbestimmung für Erzeugung und Verfall."""

    NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    def test_alter_wird_berechnet(self):
        existing = {"generatedAt": (self.NOW - timedelta(hours=5)).isoformat()}
        self.assertEqual(fn.ai_summary_age(existing, self.NOW), timedelta(hours=5))

    def test_unbrauchbare_eingaben_ergeben_none(self):
        for existing in (None, {}, [], "x", {"generatedAt": None}, {"generatedAt": "Unsinn"},
                         {"generatedAt": "2026-07-01T10:00:00"}, {"generatedAt": 5}):
            with self.subTest(existing=existing):
                self.assertIsNone(fn.ai_summary_age(existing, self.NOW))


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
        # Zwölf Stunden machen eine neue Zusammenfassung fällig, die alte aber
        # noch nicht wertlos: Verfall und Erzeugung sind zwei Schwellen.
        payload = self._write((self.now - timedelta(hours=12)).isoformat())
        self.assertFalse(fn.drop_stale_ai_summary(self.path, payload, self.now))
        self.assertTrue(os.path.exists(self.path))
        self.assertTrue(fn.should_generate_ai_summary("auto", self.now, payload))

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


class TestLoadFeeds(unittest.TestCase):
    """Die Quellenliste ist eine Datei – Pfad und Prüfung müssen sitzen."""

    VALID = {"url": "https://example.com/feed", "weight": 0.5, "category": "Nachrichten"}

    def _write(self, content):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "sources.json"
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(content, str):
                f.write(content)
            else:
                json.dump(content, f)
        return path

    def _entry(self, **changes):
        entry = dict(self.VALID)
        entry.update(changes)
        return {"beispiel": entry}

    def test_standardpfad_haengt_am_skript_nicht_am_cwd(self):
        self.assertEqual(fn.SOURCES_PATH, fn.REPO_ROOT / "config" / "sources.json")
        self.assertTrue(fn.SOURCES_PATH.is_file())

    def test_laedt_aus_fremdem_arbeitsverzeichnis(self):
        # Der Import darf nicht davon abhängen, von wo aus gestartet wurde.
        with tempfile.TemporaryDirectory() as elsewhere:
            cwd = os.getcwd()
            os.chdir(elsewhere)
            try:
                self.assertEqual(fn.load_feeds(), fn.FEEDS)
            finally:
                os.chdir(cwd)

    def test_datei_und_modulzustand_stimmen_ueberein(self):
        with open(fn.SOURCES_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(set(raw), set(fn.FEEDS))
        for name, entry in raw.items():
            with self.subTest(source=name):
                self.assertEqual(fn.FEEDS[name]["url"], entry["url"])
                self.assertEqual(fn.FEEDS[name]["weight"], float(entry["weight"]))
                self.assertEqual(fn.FEEDS[name]["category"], entry["category"])

    def test_gewichte_kommen_als_float_an(self):
        feeds = fn.load_feeds(self._write(self._entry(weight=1)))
        self.assertIsInstance(feeds["beispiel"]["weight"], float)
        self.assertEqual(feeds["beispiel"]["weight"], 1.0)

    def test_gueltige_datei_wird_unveraendert_uebernommen(self):
        self.assertEqual(
            fn.load_feeds(self._write(self._entry())),
            {"beispiel": dict(self.VALID)},
        )

    def test_fehlende_datei_scheitert_verstaendlich(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "gibtsnicht.json"
            with self.assertRaises(ValueError) as caught:
                fn.load_feeds(missing)
        self.assertIn("nicht lesbar", str(caught.exception))

    def test_kaputtes_json_scheitert_verstaendlich(self):
        with self.assertRaises(ValueError) as caught:
            fn.load_feeds(self._write("{kein json"))
        self.assertIn("kein gültiges JSON", str(caught.exception))

    def test_wurzel_muss_ein_objekt_sein(self):
        # Rohes JSON, weil sich ein nackter String sonst nicht schreiben lässt.
        for content in ("[]", '["tagesschau"]', '"Text"', "5", "null"):
            with self.subTest(content=content):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(content))
                self.assertIn("JSON-Objekt", str(caught.exception))

    def test_leere_quellenliste_scheitert(self):
        with self.assertRaises(ValueError) as caught:
            fn.load_feeds(self._write({}))
        self.assertIn("keine Quelle", str(caught.exception))

    def test_leerer_name_scheitert(self):
        for name in ("", "   "):
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write({name: dict(self.VALID)}))
                self.assertIn("Quellenname", str(caught.exception))

    def test_eintrag_muss_ein_objekt_sein(self):
        for entry in ("https://example.com/feed", ["https://example.com/feed"], 1, None):
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write({"beispiel": entry}))
                self.assertIn("JSON-Objekt", str(caught.exception))

    def test_fehlendes_feld_scheitert(self):
        for field in ("url", "weight", "category"):
            with self.subTest(field=field):
                entry = dict(self.VALID)
                del entry[field]
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write({"beispiel": entry}))
                self.assertIn("fehlende Felder", str(caught.exception))
                self.assertIn(field, str(caught.exception))

    def test_zusaetzliches_feld_scheitert(self):
        # Ein Tippfehler im Schlüssel soll auffallen, nicht still durchgehen.
        with self.assertRaises(ValueError) as caught:
            fn.load_feeds(self._write(self._entry(weigth=0.5)))
        self.assertIn("unbekannte Felder", str(caught.exception))
        self.assertIn("weigth", str(caught.exception))

    def test_url_muss_http_sein(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "/relativ", "", None, 5):
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(self._entry(url=url)))
                self.assertIn("http(s)-Adresse", str(caught.exception))

    def test_weight_muss_eine_zahl_sein(self):
        # True ist in Python eine 1 – als Gewicht ist es ein Tippfehler.
        for weight in (True, False, "0.9", None, [0.9]):
            with self.subTest(weight=weight):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(self._entry(weight=weight)))
                self.assertIn("muss eine Zahl sein", str(caught.exception))

    def test_weight_muss_im_bereich_liegen(self):
        for weight in (-0.1, 1.5, 100):
            with self.subTest(weight=weight):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(self._entry(weight=weight)))
                self.assertIn("zwischen 0 und 1", str(caught.exception))

    def test_category_muss_text_sein(self):
        for category in ("", "   ", None, 5, ["Politik"]):
            with self.subTest(category=category):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(self._entry(category=category)))
                self.assertIn("category", str(caught.exception))

    def test_filter_ist_optional(self):
        # Quellen ohne Filter behalten exakt ihre drei Felder.
        feeds = fn.load_feeds(self._write(self._entry()))
        self.assertNotIn("filter", feeds["beispiel"])

    def test_filter_wird_uebernommen_und_normalisiert(self):
        feeds = fn.load_feeds(self._write(self._entry(
            filter={"keywords": ["  Fantasy ", "Start/Sit"], "maxItems": 5}
        )))
        self.assertEqual(
            feeds["beispiel"]["filter"],
            {"keywords": ["fantasy", "start/sit"], "maxItems": 5},
        )

    def test_filter_mit_nur_einem_feld_ist_gueltig(self):
        for raw, expected in (
            ({"keywords": ["fantasy"]}, {"keywords": ["fantasy"]}),
            ({"maxItems": 3}, {"maxItems": 3}),
        ):
            with self.subTest(filter=raw):
                feeds = fn.load_feeds(self._write(self._entry(filter=raw)))
                self.assertEqual(feeds["beispiel"]["filter"], expected)

    def test_leerer_filter_scheitert(self):
        with self.assertRaises(ValueError) as caught:
            fn.load_feeds(self._write(self._entry(filter={})))
        self.assertIn("mindestens eines der Felder", str(caught.exception))

    def test_filter_muss_ein_objekt_sein(self):
        for raw in ("fantasy", ["fantasy"], 5, None):
            with self.subTest(filter=raw):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(self._entry(filter=raw)))
                self.assertIn("filter muss ein JSON-Objekt sein", str(caught.exception))

    def test_zusaetzliches_filterfeld_scheitert(self):
        # Ein Tippfehler im Filter soll auffallen, nicht still durchgehen.
        with self.assertRaises(ValueError) as caught:
            fn.load_feeds(self._write(self._entry(filter={"maxitems": 5})))
        self.assertIn("unbekannte filter-Felder", str(caught.exception))
        self.assertIn("maxitems", str(caught.exception))

    def test_keywords_muessen_eine_nicht_leere_liste_sein(self):
        for keywords in ([], "fantasy", {"a": 1}, None, 5):
            with self.subTest(keywords=keywords):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(self._entry(filter={"keywords": keywords})))
                self.assertIn("nicht leere Liste", str(caught.exception))

    def test_keywords_duerfen_nur_texte_enthalten(self):
        for keywords in (["fantasy", ""], ["fantasy", "   "], ["fantasy", 5], [None]):
            with self.subTest(keywords=keywords):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(self._entry(filter={"keywords": keywords})))
                self.assertIn("nicht leere Texte", str(caught.exception))

    def test_maxitems_muss_eine_ganze_zahl_sein(self):
        # True ist in Python eine 1 – als Obergrenze ist es ein Tippfehler.
        for max_items in (True, False, 2.5, "5", None, [5]):
            with self.subTest(maxItems=max_items):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(self._entry(filter={"maxItems": max_items})))
                self.assertIn("ganze Zahl", str(caught.exception))

    def test_maxitems_muss_positiv_sein(self):
        for max_items in (0, -1):
            with self.subTest(maxItems=max_items):
                with self.assertRaises(ValueError) as caught:
                    fn.load_feeds(self._write(self._entry(filter={"maxItems": max_items})))
                self.assertIn("mindestens 1", str(caught.exception))


class TestFeedConfig(unittest.TestCase):
    """Die Quellenliste selbst: ein Tippfehler hier kostet einen ganzen Feed."""

    EXPECTED_URLS = {
        "tagesschau": "https://www.tagesschau.de/xml/rss2/",
        "heise": "https://www.heise.de/rss/heise-top-atom.xml",
        "golem": "https://rss.golem.de/rss.php?feed=RSS2.0",
        "spiegel": "https://www.spiegel.de/schlagzeilen/index.rss",
        "deutschlandfunk": "https://www.deutschlandfunk.de/nachrichten-100.rss",
        "deutsche-welle": "https://rss.dw.com/rdf/rss-de-all",
        "handelsblatt": "https://feeds.cms.handelsblatt.com/schlagzeilen",
        "netzpolitik": "https://netzpolitik.org/feed/",
        "espn-nfl": "https://www.espn.com/espn/rss/nfl/news",
    }

    def test_quellen_und_urls_stimmen(self):
        self.assertEqual({name: cfg["url"] for name, cfg in fn.FEEDS.items()}, self.EXPECTED_URLS)
        self.assertEqual(len(fn.FEEDS), 9)

    def test_jede_quelle_ist_vollstaendig(self):
        erlaubt = fn.FEED_FIELDS | fn.FEED_OPTIONAL_FIELDS
        for name, config in fn.FEEDS.items():
            with self.subTest(source=name):
                self.assertTrue(fn.FEED_FIELDS <= set(config))
                self.assertTrue(set(config) <= erlaubt)
                self.assertTrue(fn.is_http_url(config["url"]))
                self.assertIsInstance(config["weight"], float)
                self.assertTrue(0 < config["weight"] <= 1.0)
                self.assertTrue(config["category"].strip())

    def test_rangfolge_der_gewichte(self):
        # Breite Nachrichtenquellen vor Fachredaktionen vor Spezialressort.
        order = ["tagesschau", "deutschlandfunk", "spiegel", "deutsche-welle", "handelsblatt",
                 "heise", "golem", "netzpolitik", "espn-nfl"]
        weights = [fn.FEEDS[name]["weight"] for name in order]
        self.assertEqual(weights, sorted(weights, reverse=True))
        self.assertEqual(max(weights), fn.FEEDS["tagesschau"]["weight"])
        self.assertEqual(min(weights), fn.FEEDS["espn-nfl"]["weight"])

    def test_nur_espn_ist_gefiltert(self):
        # Alle anderen Quellen sollen sich verhalten wie vor dem Filter.
        gefiltert = {name for name, cfg in fn.FEEDS.items() if "filter" in cfg}
        self.assertEqual(gefiltert, {"espn-nfl"})

    def test_espn_filtert_auf_fantasy_relevantes(self):
        # Ohne diese Schlagwörter kämen Spielberichte und Wettinhalte durch und
        # würden die allgemeinen Nachrichten verdrängen.
        config = fn.FEEDS["espn-nfl"]["filter"]
        self.assertEqual(config["maxItems"], 5)
        for keyword in ("fantasy", "waiver", "start/sit", "injury", "injuries",
                        "depth chart", "sleeper", "breakout", "trade", "suspension"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, config["keywords"])

    def test_defaultkategorien_sind_bekannt(self):
        # Die Default-Kategorie greift, wenn kein Schlagwort passt – sie muss
        # zum Filter im Frontend passen und darf kein Einzelfall sein.
        erlaubt = set(fn.CATEGORY_KEYWORDS) | {"Nachrichten", "Allgemein", "NFL Fantasy"}
        for name, config in fn.FEEDS.items():
            with self.subTest(source=name):
                self.assertIn(config["category"], erlaubt)

    def test_default_greift_ohne_treffer(self):
        for name, config in fn.FEEDS.items():
            with self.subTest(source=name):
                self.assertEqual(
                    fn.detect_category("Etwas Belangloses", "", config["category"]),
                    config["category"],
                )


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


class TestFetchFeedFilter(unittest.TestCase):
    """Der quellenbezogene Filter – ohne Netzwerk, gegen einen festen Feed.

    Der Feed bildet nach, was ESPN liefert: ein paar fantasy-relevante Artikel
    zwischen Spielberichten und Wettvorschauen.
    """

    ITEMS = [
        ("Waiver wire targets for Week 6", "Pickups to consider."),
        ("Chiefs beat Raiders 24-10", "Nuechterner Spielbericht ohne Bezug."),
        ("Roster notes from Sunday", "The starter suffered a knee injury late."),
        ("Best bets and odds for Sunday", "Our betting preview."),
        ("Fantasy sleeper picks", "Deep league options."),
        ("Depth chart shuffle in Denver", "New order behind center."),
        ("Blockbuster trade sends receiver east", "Deadline move."),
        ("Start/Sit calls for Week 6", "Tough lineup decisions."),
        ("Suspension lifted for lineman", "Back on the field."),
    ]

    KEYWORDS = ["fantasy", "waiver", "start/sit", "injury", "injuries",
                "depth chart", "sleeper", "breakout", "trade", "suspension"]

    # Alles, was mindestens ein Schlagwort in Titel oder Beschreibung trägt.
    PASSING = [
        "Waiver wire targets for Week 6",
        "Roster notes from Sunday",
        "Fantasy sleeper picks",
        "Depth chart shuffle in Denver",
        "Blockbuster trade sends receiver east",
        "Start/Sit calls for Week 6",
        "Suspension lifted for lineman",
    ]

    BLOCKED = ["Chiefs beat Raiders 24-10", "Best bets and odds for Sunday"]

    def setUp(self):
        recent = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
        items = "".join(
            f"<item><title>{title}</title>"
            f"<link>https://example.com/{index}</link>"
            f"<description>{description}</description>"
            f"<pubDate>{recent}</pubDate></item>"
            for index, (title, description) in enumerate(self.ITEMS)
        )
        raw = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<rss version="2.0"><channel>{items}</channel></rss>'
        ).encode("utf-8")
        self._original = fn.download_feed
        fn.download_feed = lambda name, url: raw

    def tearDown(self):
        fn.download_feed = self._original

    def _titles(self, **filter_fields):
        config = {"url": "x", "weight": 0.65, "category": "NFL Fantasy"}
        if filter_fields:
            config["filter"] = filter_fields
        return [article["title"] for article in fn.fetch_feed("espn-nfl", config)]

    def test_ohne_filter_bleibt_alles_wie_bisher(self):
        self.assertEqual(self._titles(), [title for title, _ in self.ITEMS])

    def test_passende_artikel_kommen_durch(self):
        self.assertEqual(self._titles(keywords=self.KEYWORDS), self.PASSING)

    def test_unpassende_artikel_bleiben_draussen(self):
        titles = self._titles(keywords=self.KEYWORDS)
        for title in self.BLOCKED:
            with self.subTest(title=title):
                self.assertNotIn(title, titles)

    def test_schlagwort_wird_auch_in_der_beschreibung_gefunden(self):
        # "injury" steht nur im Beschreibungstext, nicht im Titel.
        self.assertEqual(self._titles(keywords=["injury"]), ["Roster notes from Sunday"])

    def test_schreibweise_ist_egal(self):
        # "Start/Sit" steht so im Titel, das Schlagwort kommt klein aus dem
        # Loader – gefunden wird es trotzdem.
        self.assertEqual(self._titles(keywords=["start/sit"]), ["Start/Sit calls for Week 6"])

    def test_grenze_greift_nach_dem_filtern(self):
        # Erst filtern, dann deckeln: die fünf ersten passenden in Feed-Reihenfolge.
        self.assertEqual(
            self._titles(keywords=self.KEYWORDS, maxItems=5),
            self.PASSING[:5],
        )

    def test_grenze_wirkt_auch_ohne_schlagwoerter(self):
        self.assertEqual(self._titles(maxItems=2), [title for title, _ in self.ITEMS[:2]])

    def test_ausgabefelder_bleiben_unveraendert(self):
        config = {
            "url": "x", "weight": 0.65, "category": "NFL Fantasy",
            "filter": {"keywords": self.KEYWORDS, "maxItems": 5},
        }
        article = fn.fetch_feed("espn-nfl", config)[0]
        self.assertEqual(
            set(article),
            {"id", "source", "sourceWeight", "title", "link", "summary", "image",
             "published", "category"},
        )


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
