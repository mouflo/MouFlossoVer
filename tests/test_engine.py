"""Rangement d'un crossover de bout en bout, sur un faux dossier de séries (aucun accès à Sonarr ni au NAS)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
TMP = Path(tempfile.mkdtemp())
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["SERIES_DIR"] = str(TMP / "series")
import engine  # noqa: E402

NFO = '<?xml version="1.0" encoding="utf-8"?>\n<episodedetails>\n  <title>%s</title>\n  <episode>%d</episode>\n  <season>%d</season>\n</episodedetails>\n'
RELEASE = ".[FR+EN].WEBDL-1080p.h264.AAC 2.0-[TEST]"


def fabriquer(serie, saison, ep, titre):
    d = TMP / "series" / serie / ("s%d" % saison)
    d.mkdir(parents=True, exist_ok=True)
    stem = "%s.S%02dE%02d%s" % (serie, saison, ep, RELEASE)
    (d / (stem + ".mkv")).write_bytes(b"video-" + serie.encode())
    (d / (stem + ".nfo")).write_text(NFO % (titre, ep, saison), encoding="utf-8")
    (d / (stem + "-thumb.jpg")).write_bytes(b"jpg")
    return d, stem


def crossover():
    parts = {}
    for sid, (serie, saison, ep, titre, n) in enumerate([("Chicago Fire", 14, 13, "Contamination (1)", 1), ("Chicago Med", 11, 13, "Contamination (2)", 2)], start=1):
        fabriquer(serie, saison, ep, titre)
        parts[(sid, saison, ep)] = {
            "s": {"id": sid, "title": serie, "path": str(TMP / "series" / serie)},
            "e": {"id": 100 + sid, "seasonNumber": saison, "episodeNumber": ep, "title": "Reckoning (%d)" % n,
                  "hasFile": True, "monitored": True, "airDate": "2026-03-04"},
            "part": n, "src": "sonarr"}
    return {"id": "test", "name": "Reckoning", "parts": parts}


class TestRangement(unittest.TestCase):
    def setUp(self):
        self.appels = []

        def faux_sonarr(methode, chemin, corps=None):
            self.appels.append((methode, chemin))
            return {"monitored": True} if methode == "GET" else None
        engine.sonarr = faux_sonarr

    def test_plan_rangement_et_annulation(self):
        c = crossover()
        plan = engine.make_plan(c)
        self.assertEqual(plan["blockers"], [])
        genres = sorted(o["kind"] for o in plan["ops"])
        self.assertEqual(genres, ["copy_group"] * 2 + ["patch_nfo"] * 2 + ["rename_group"] * 2 + ["unmonitor"] * 2)

        etat = {"ignored": [], "done": {}, "history": []}
        res = engine.execute(c, plan, etat)
        self.assertTrue(res["ok"], res)
        fire = TMP / "series" / "Chicago Fire" / "s14"
        med = TMP / "series" / "Chicago Med" / "s11"
        noms_fire = sorted(p.name for p in fire.iterdir() if p.suffix == ".mkv")
        noms_med = sorted(p.name for p in med.iterdir() if p.suffix == ".mkv")
        self.assertIn("Chicago Fire.S14E13.part1(FIRE)" + RELEASE + ".mkv", noms_fire)
        self.assertIn("Chicago Fire.S00E21.part2(MED)" + RELEASE + ".mkv", noms_fire)
        self.assertIn("Chicago Med.S11E13.part2(MED)" + RELEASE + ".mkv", noms_med)
        self.assertIn("Chicago Med.S00E21.part1(FIRE)" + RELEASE + ".mkv", noms_med)

        nfo = (fire / ("Chicago Fire.S00E21.part2(MED)" + RELEASE + ".nfo")).read_text(encoding="utf-8")
        self.assertIn("<season>0</season>", nfo)
        self.assertIn("<episode>21</episode>", nfo)
        self.assertIn("<airsbefore_season>14</airsbefore_season>", nfo)
        self.assertIn("<airsbefore_episode>14</airsbefore_episode>", nfo)
        nfo_med = (med / ("Chicago Med.S00E21.part1(FIRE)" + RELEASE + ".nfo")).read_text(encoding="utf-8")
        self.assertIn("<airsbefore_episode>13</airsbefore_episode>", nfo_med)     # la partie 1 passe avant S11E13

        self.assertEqual(engine.make_plan(c)["ops"], [])                           # tout est en place : rien à refaire
        self.assertIn(("PUT", "/episode/101"), self.appels)                        # épisodes d'origine non surveillés

        res = engine.undo("test", engine.load_state())
        self.assertTrue(res["ok"], res)
        self.assertEqual(sorted(p.name for p in fire.iterdir() if p.suffix == ".mkv"), ["Chicago Fire.S14E13" + RELEASE + ".mkv"])
        self.assertEqual(sorted(p.name for p in med.iterdir() if p.suffix == ".mkv"), ["Chicago Med.S11E13" + RELEASE + ".mkv"])

    def test_refuse_un_dossier_hors_des_series(self):
        c = crossover()
        for v in c["parts"].values():
            v["s"]["path"] = "/ailleurs/" + v["s"]["title"]
        plan = engine.make_plan(c)
        self.assertTrue(plan["blockers"])
        self.assertEqual(plan["ops"], [])

    def test_episode_pas_telecharge_bloque(self):
        c = crossover()
        next(iter(c["parts"].values()))["e"]["hasFile"] = False
        self.assertTrue(any("pas encore téléchargé" in b for b in engine.make_plan(c)["blockers"]))


if __name__ == "__main__":
    unittest.main()
