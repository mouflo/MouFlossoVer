"""Petites vérifications : skins, lecture de Wikipédia, numéro de version."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
import apparence  # noqa: E402
import engine  # noqa: E402
import wiki  # noqa: E402


class TestSkins(unittest.TestCase):
    def test_skins_fournis_valides(self):
        dossiers = [d for d in (RACINE / "ui" / "skins").iterdir() if d.is_dir()]
        self.assertTrue(dossiers)
        for d in dossiers:
            css = (d / "skin.css").read_text(encoding="utf-8")
            self.assertIsNone(apparence.verifier_css(css), d.name)
            self.assertTrue((d / "skin.json").is_file(), d.name)

    def test_css_dangereux_refuse(self):
        self.assertIsNotNone(apparence.verifier_css("@import url(http://x/y.css);"))
        self.assertIsNotNone(apparence.verifier_css("a{background:url(http://x/y.png)}"))


class TestWikipedia(unittest.TestCase):
    def test_renvois_formats(self):
        l1 = "→ [[Crossover (fiction)|crossover]] avec ''{{Langue|en|[[FBI: Most Wanted]]}}'' (saison 1, épisode 9)"
        self.assertEqual(wiki.extract_refs(l1), [("FBI: Most Wanted", 1, 9)])
        l2 = "se poursuit avec l'épisode 13 de la [[Saison 11 de Chicago Med|saison 11 de ''Chicago Med'']]"
        self.assertEqual(wiki.extract_refs(l2), [("Chicago Med", 11, 13)])
        l3 = "qui se termine dans ''Grey's Anatomy'', [[Saison 16 de Grey's Anatomy#Épisode 1|S16E10]]"
        self.assertEqual(wiki.extract_refs(l3), [("Grey's Anatomy", 16, 10)])

    def test_noms_de_series(self):
        self.assertEqual(wiki.norm("Chicago P.D."), wiki.norm("Chicago PD"))
        self.assertEqual(wiki.norm("Chicago Police Department"), wiki.norm("Chicago P.D."))


class TestLiens(unittest.TestCase):
    def test_lien_wikipedia(self):
        self.assertEqual(engine.wiki_url("Saison 14 de Chicago Fire"), "https://fr.wikipedia.org/wiki/Saison_14_de_Chicago_Fire")
        self.assertEqual(engine.wiki_url("FBI (série télévisée)"), "https://fr.wikipedia.org/wiki/FBI_(s%C3%A9rie_t%C3%A9l%C3%A9vis%C3%A9e)")

    def test_passage_lisible(self):
        brut = "  | commentaire = Première partie d'un [[crossover (fiction)|crossover]] qui se poursuit avec [[Saison 11 de Chicago Med|la saison 11]]."
        self.assertEqual(engine.lisible(brut), "Première partie d'un crossover qui se poursuit avec la saison 11.")
        self.assertEqual(engine.lisible("#: → Début d'un ''crossover''"), "Début d'un crossover")
        self.assertEqual(engine.lisible("avec la [[Saison 13 de Chica"), "avec la")

    def test_sources_dedoublonnees(self):
        c = {"wiki": [{"page": "P", "line": "a", "series": "X"}, {"page": "P", "line": "b", "series": "Y"}], "parts": {}}
        self.assertEqual([w["page"] for w in engine.wiki_sources(c)], ["P"])


if __name__ == "__main__":
    unittest.main()


class TestNCIS(unittest.TestCase):
    def test_refs_ncis(self):
        import wiki, engine
        a = wiki.extract_claims("X", {"p": "Épisode crossover avec ''[[NCIS : Nouvelle-Orléans]]'' qui se termine dans le douzième épisode de la deuxième saison."})
        self.assertEqual((a[0]["season"], a[0]["episode"]), (2, 12))
        b = wiki.extract_claims("X", {"p": "| 12 || T (Deuxième partie<ref group=\"N\">conclusion de l'épisode 13x12 de la série ''[[NCIS : Enquêtes spéciales]]''.</ref>) ||"})
        self.assertEqual((b[0]["season"], b[0]["episode"]), (13, 12))
        self.assertEqual(engine.tag_of("Foo: Bar"), "BAR")
        self.assertEqual(engine.tag_of("Law & Order: Special Victims Unit"), "SVU")
        self.assertEqual(engine.franchise_key("NCIS: Sydney"), "ncis")


class TestPosition(unittest.TestCase):
    def test_position_emby_ou_nous(self):
        self.assertTrue(engine.position_ok("<airsbefore_season>7</airsbefore_season><airsbefore_episode>10</airsbefore_episode>", (7, 10)))
        self.assertTrue(engine.position_ok("<displayepisode>10</displayepisode>\n<displayseason>7</displayseason>", (7, 10)))
        self.assertFalse(engine.position_ok("<displayepisode>11</displayepisode><displayseason>7</displayseason>", (7, 10)))
        self.assertFalse(engine.position_ok("<title>x</title>", (7, 10)))


class TestPartieDite(unittest.TestCase):
    def test_partie_dite(self):
        self.assertEqual(engine.partie_dite("Il s'agit de la deuxième partie de l'épisode 7 de la saison 6 de Grey's Anatomy station 19."), 2)
        self.assertEqual(engine.partie_dite("Première partie d'un crossover qui se poursuit avec l'épisode 13"), 1)
        self.assertIsNone(engine.partie_dite("L'épisode 12 marque un crossover avec NCIS"))


class TestTvdb(unittest.TestCase):
    def test_indices_de_resume(self):
        import tvdb
        series = [{"id": 1, "title": "Chicago Fire"}, {"id": 2, "title": "Chicago Med"}, {"id": 3, "title": "Intelligence (2020)"}]
        noms = tvdb.noms_cherchables(series)
        eps = [{"seasonNumber": 14, "episodeNumber": 13, "overview": "The crossover continues on Chicago Med S11E13 Reckoning (2) and Chicago P.D."},
               {"seasonNumber": 1, "episodeNumber": 1, "overview": "An airfield emergency involving the Intelligence Unit."},
               {"seasonNumber": 0, "episodeNumber": 1, "overview": "Crossover with Chicago Med."}]
        h = tvdb.indices_de(series[0], eps, noms)
        self.assertEqual(len(h), 1)
        self.assertEqual((h[0]["oid"], h[0]["bs"], h[0]["be"]), (2, 11, 13))
        h2 = tvdb.indices_de({"id": 9, "title": "NCIS"}, [{"seasonNumber": 14, "episodeNumber": 15, "overview": "Crossover with Chicago Med season 3 episode 14."}], noms)
        self.assertEqual((h2[0]["bs"], h2[0]["be"]), (3, 14))

    def test_partie_en_anglais(self):
        self.assertEqual(engine.partie_dite("Part 2 of a crossover with Station 19."), 2)
        self.assertEqual(engine.partie_dite("This is the third part of a major crossover"), 3)
        self.assertIsNone(engine.partie_dite("The crossover continues on Chicago Med"))


class TestEnwiki(unittest.TestCase):
    def test_modele_infobox(self):
        import enwiki
        texte = """{{Infobox television crossover episode
| series1         = [[NCIS (TV series)|NCIS]]
| episode_title1  = Too Many Cooks
| season1         = 20
| episode_no1     = 10
| series2         = [[NCIS: Hawaiʻi]]
| episode_title2  = Hana
| season2         = 2
| episode_no2     = 10
| airdate_overall = {{start date|2023|1|9}}
}}
Texte."""
        p = enwiki.analyser("NCIS crossover event", texte)
        self.assertEqual([(x["n"], x["series"], x["season"], x["episode"]) for x in p], [(1, "NCIS", 20, 10), (2, "NCIS: Hawaiʻi", 2, 10)])



class TestEpisodeUnique(unittest.TestCase):
    """Un épisode où apparaissent des personnages d'une autre série qui n'a pas de suite : une seule partie,
    copiée dans l'autre série juste avant son épisode repère (rien n'est copié en sens inverse)."""

    def test_plan(self):
        import test_engine as te
        da, sa = te.fabriquer("Magnum P.I.", 1, 5, "Visite")
        db, sb = te.fabriquer("Hawaii Five-0", 7, 19, "Puka ana")
        mk = lambda sid, titre, sdir, ep: {"id": sid, "title": titre, "path": str(te.TMP / "series" / titre)}
        parts = {
            (21, 1, 5): {"s": mk(21, "Magnum P.I.", da, 5), "e": {"id": 501, "seasonNumber": 1, "episodeNumber": 5, "title": "Visite", "hasFile": True,
                                                                  "monitored": True, "airDate": "2020-01-03"}, "part": 1, "src": "wikipedia"},
            (22, 7, 19): {"s": mk(22, "Hawaii Five-0", db, 19), "e": {"id": 502, "seasonNumber": 7, "episodeNumber": 19, "title": "Puka ana", "hasFile": True,
                                                                     "monitored": True, "airDate": "2020-01-03"}, "part": 0, "ref": True, "src": "wikipedia"}}
        c = {"id": "unique", "name": "Visite", "date": "2020-01-03", "parts": parts}
        te.engine.sonarr = lambda *a, **k: {"monitored": True}
        plan = te.engine.make_plan(c)
        self.assertEqual(plan["blockers"], [])
        copies = [o for o in plan["ops"] if o["kind"] == "copy_group"]
        self.assertEqual(len(copies), 1)
        self.assertIn("Hawaii Five-0", copies[0]["dir"])
        patch = [o for o in plan["ops"] if o["kind"] == "patch_nfo"]
        self.assertEqual([o["airs"] for o in patch], [[7, 19]])
        self.assertEqual(len([o for o in plan["ops"] if o["kind"] == "rename_group"]), 1)      # seule la série qui a la partie est renommée
