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

    def test_sources_dedoublonnees(self):
        c = {"wiki": [{"page": "P", "line": "a", "series": "X"}, {"page": "P", "line": "b", "series": "Y"}], "parts": {}}
        self.assertEqual([w["page"] for w in engine.wiki_sources(c)], ["P"])


if __name__ == "__main__":
    unittest.main()
