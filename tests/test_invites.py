"""Épisode invité : machine à états avec un faux Sonarr, et rangement d'une partie venue du dossier de passage."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine  # noqa: E402  (fabrique le faux dossier de séries et règle DATA_DIR / SERIES_DIR)
TMP = test_engine.TMP
os.environ["STAGING_DIR"] = str(TMP / "passage")
import engine  # noqa: E402
import invites  # noqa: E402


class FauxSonarr:
    def __init__(self):
        self.appels = []
        self.series = [{"id": 1, "title": "Chicago Fire", "tvdbId": 1, "qualityProfileId": 4}]
        self.episode = {"id": 900, "seasonNumber": 2, "episodeNumber": 10, "monitored": False, "hasFile": False}
        self.file = []

    def __call__(self, method, path, body=None):
        self.appels.append((method, path))
        if path == "/series" and method == "GET":
            return self.series
        if path == "/rootfolder":
            return []
        if path == "/tag":
            return [{"label": "mouflossover-invite", "id": 5}] if method == "GET" else {"id": 5}
        if path.startswith("/series/lookup"):
            return [{"title": "Invitée", "tvdbId": 77, "id": 0}]
        if path == "/series" and method == "POST":
            assert body["addOptions"]["monitor"] == "none" and body["addOptions"]["searchForMissingEpisodes"] is False
            assert body["rootFolderPath"] == str(TMP / "passage") and body["qualityProfileId"] == 4
            self.series.append({"id": 50, "title": "Invitée", "tvdbId": 77, "path": str(TMP / "passage" / "Invitée")})
            return {"id": 50}
        if path.startswith("/episode?"):
            return [self.episode]
        if path.startswith("/episode/") and method == "PUT":
            self.episode = body
            return body
        if path == "/command":
            return {}
        if path.startswith("/queue/details"):
            return self.file
        return []


class TestInvites(unittest.TestCase):
    def test_de_la_demande_a_l_episode_recu(self):
        faux = FauxSonarr()
        vrai_sonarr, vrai_refresh = engine.sonarr, engine.refresh
        self.addCleanup(lambda: (setattr(engine, "sonarr", vrai_sonarr), setattr(engine, "refresh", vrai_refresh)))
        engine.sonarr = faux
        c = test_engine.crossover()
        c["id"] = "inv"
        c["parts"][(1, 14, 13)]["part"] = 1
        c["parts"][(2, 11, 13)]["part"] = 3                         # la partie 2 manque
        engine.refresh = lambda: None
        st = engine.load_state()
        engine.save_state(st)
        res = invites.creer(c, 77, "Invitée", 2, 10)
        self.assertTrue(res["ok"])
        gid = res["id"]
        import time
        time.sleep(0.5)                                              # le premier pas tourne dans un fil
        g = lambda: next(x for x in engine.load_state()["guests"] if x["id"] == gid)
        self.assertEqual(g()["part"], 2)
        self.assertIn(g()["status"], ("recherche", "ajout"))
        invites._un_pas(gid) if g()["status"] == "ajout" else None
        invites._un_pas(gid)                                         # recherche -> attente
        self.assertEqual(g()["status"], "attente")
        self.assertTrue(faux.episode["monitored"])
        self.assertIn(("POST", "/command"), faux.appels)
        faux.file = [{"size": 100, "sizeleft": 40}]
        invites._un_pas(gid)
        self.assertIn("60 %", g()["message"])
        faux.episode["hasFile"] = True
        invites._un_pas(gid)
        self.assertEqual(g()["status"], "pret")
        self.assertEqual(len([a for a in faux.appels if a == ("POST", "/series")]), 1)   # la série n'est ajoutée qu'une fois


class TestRangementInvite(unittest.TestCase):
    def test_plan_avec_partie_du_dossier_de_passage(self):
        c = test_engine.crossover()
        c["id"] = "avec-invite"
        c["parts"][(1, 14, 13)]["part"] = 1
        c["parts"][(2, 11, 13)]["part"] = 3
        d = TMP / "passage" / "Invitée" / "Season 2"
        d.mkdir(parents=True)
        stem = "Invitée.S02E10.1080p.WEB-DL-[TEST]"
        (d / (stem + ".mkv")).write_bytes(b"video-invitee")
        c["parts"][(50, 2, 10)] = {
            "s": {"id": 50, "title": "Invitée", "path": str(TMP / "passage" / "Invitée")},
            "e": {"id": 950, "seasonNumber": 2, "episodeNumber": 10, "title": "Titre invité", "overview": "Résumé & <test>",
                  "hasFile": True, "monitored": True, "airDate": "2026-03-04"},
            "part": 2, "src": "invite"}
        plan = engine.make_plan(c)
        self.assertEqual(plan["blockers"], [])
        genres = [o["kind"] for o in plan["ops"]]
        self.assertIn("make_nfo", genres)
        self.assertEqual(genres.count("copy_group"), 2 + 2)        # 2 copies croisées Fire/Med + 2 copies de la partie invitée
        self.assertFalse(any(o["kind"] == "rename_group" and "Invitée" in o["from_stem"] for o in plan["ops"]))
        self.assertFalse(any(o["kind"] == "unmonitor" and o["episodeId"] == 950 for o in plan["ops"]))
        etat = engine.load_state()
        res = engine.execute(c, plan, etat)
        self.assertTrue(res["ok"], res)
        self.assertTrue((d / (stem + ".mkv")).exists())             # le fichier de passage n'est jamais touché
        nfo = [p for p in (TMP / "series" / "Chicago Fire").rglob("*.nfo") if "(INVITEE)" in p.name.upper() or "INVIT" in p.name.upper()]
        self.assertTrue(nfo)
        self.assertIn("Résumé &amp; &lt;test&gt;", nfo[0].read_text(encoding="utf-8"))
        back = engine.undo("avec-invite", engine.load_state())
        self.assertTrue(back["ok"])
        self.assertTrue((d / (stem + ".mkv")).exists())
        self.assertFalse([p for p in (TMP / "series" / "Chicago Fire").rglob("*.nfo") if "INVIT" in p.name.upper()])


class TestDepot(unittest.TestCase):
    """Fichiers déposés à la main à la racine du dossier de passage."""

    def test_fichier_depose_reconnu(self):
        passage = Path(engine.staging_dir())
        passage.mkdir(parents=True, exist_ok=True)
        serie = passage / "Law & Order - Special Victims Unit"
        (passage / "New.York.Unité.Spéciale.S16E07b.1080p.MULTi.WEBRip.AvALoN.mkv").write_bytes(b"x" * 10)
        (passage / "Autre.S01E01.mkv").write_bytes(b"y")
        engine.save_state({"ignored": [], "done": {}, "history": [], "guests": [
            {"id": "g1", "cid": "c", "with": [], "tvdbId": 1, "title": "Law & Order: Special Victims Unit", "season": 16, "episode": 7,
             "part": 2, "status": "attente", "message": "", "sonarrId": 9, "episodeId": 70, "next_try": 0, "created": 0}]})
        appels = []

        def faux(methode, chemin, corps=None):
            appels.append((methode, chemin, corps))
            return {"path": str(serie)} if chemin == "/series/9" else {"hasFile": False, "seasonNumber": 16, "episodeNumber": 7, "monitored": True, "id": 70}
        ancien = engine.sonarr
        engine.sonarr = faux
        try:
            self.assertEqual(invites.deposer(maintenant=True), 1)
        finally:
            engine.sonarr = ancien
        rangé = serie / "Season 16" / "Law & Order - Special Victims Unit.S16E07.1080p.MULTi.WEBRip.AvALoN.mkv"
        self.assertTrue(rangé.exists())
        self.assertTrue((passage / "Autre.S01E01.mkv").exists())      # sans demande correspondante : on n'y touche pas
        self.assertIn(("POST", "/command", {"name": "RescanSeries", "seriesId": 9}), appels)


class TestNettoyage(unittest.TestCase):
    """Épisode invité déjà copié partout : son fichier est retiré du dossier de passage (via Sonarr), sans retélécharger."""

    def test_nettoyage_puis_reactivation(self):
        engine.save_state({"ignored": [], "done": {"cx": "m"}, "history": [], "guests": [
            {"id": "g9", "cid": "cx", "with": ["1-1-1", "2-1-1"], "tvdbId": 5, "title": "Invitée", "season": 3, "episode": 4,
             "part": 3, "status": "pret", "message": "", "sonarrId": 9, "episodeId": 90, "next_try": 0, "created": 0}]})
        ancien_cache = dict(engine.CACHE)
        engine.CACHE["items"] = [{"id": "cx", "status": "done", "applied": True,
                                  "parts": [{"key": "1-1-1", "source": "sonarr", "season": 1, "ep": 1}, {"key": "2-1-1", "source": "sonarr", "season": 1, "ep": 1},
                                            {"key": "9-3-4", "source": "invite", "season": 3, "ep": 4}]}]
        appels = []
        passage = Path(engine.staging_dir()) / "Invitee"
        (passage / "Season 03").mkdir(parents=True, exist_ok=True)

        def faux(methode, chemin, corps=None):
            appels.append((methode, chemin))
            if chemin == "/series/9":
                return {"path": str(passage)}
            ep = {"id": 90, "seasonNumber": 3, "episodeNumber": 4, "hasFile": True, "episodeFileId": 777, "monitored": True}
            return [ep] if chemin.startswith("/episode?") else ep
        ancien = engine.sonarr
        engine.sonarr = faux
        try:
            invites.nettoyer_prets()
            etat = next(g for g in engine.load_state()["guests"] if g["id"] == "g9")
            self.assertEqual(etat["status"], "range")
            self.assertIn(("PUT", "/episode/90"), appels)
            self.assertIn(("DELETE", "/episodefile/777"), appels)
            self.assertLess(appels.index(("PUT", "/episode/90")), appels.index(("DELETE", "/episodefile/777")))   # d'abord « non surveillé »
            self.assertFalse(passage.exists())                                                                  # dossiers vides retirés
            invites.reactiver(["1-1-1", "2-1-1", "9-3-4"])
            self.assertEqual(next(g for g in engine.load_state()["guests"] if g["id"] == "g9")["status"], "recherche")
        finally:
            engine.sonarr = ancien
            engine.CACHE.clear()
            engine.CACHE.update(ancien_cache)


class TestDepotBibliotheque(unittest.TestCase):
    """Épisode manquant d'une série de la bibliothèque, déposé à la main dans le dossier de passage."""

    def test_depot_serie_bibliotheque(self):
        passage = Path(engine.staging_dir())
        passage.mkdir(parents=True, exist_ok=True)
        serie = test_engine.TMP / "series" / "NCIS - Los Angeles"
        (serie / "s3").mkdir(parents=True, exist_ok=True)
        (serie / "s3" / "NCIS.Los.Angeles.S03E20.1080p.mkv").write_bytes(b"deja")
        (passage / "NCIS_Los_Angeles_S03E21.mkv").write_bytes(b"x" * 10)
        (passage / "Autre.Serie.S03E21.mkv").write_bytes(b"y")             # même saison/épisode, autre série : on n'y touche pas
        engine.save_state({"ignored": [], "done": {}, "history": [], "guests": []})
        ancien_cache = dict(engine.CACHE)
        engine.CACHE["items"] = [{"id": "c", "parts": [{"seriesId": 7, "series": "NCIS: Los Angeles", "season": 3, "ep": 21, "episodeId": 70,
                                                         "hasFile": False, "source": "sonarr"}]}]
        appels = []

        def faux(methode, chemin, corps=None):
            appels.append((methode, chemin))
            return {"path": str(serie)} if chemin == "/series/7" else {}
        ancien = engine.sonarr
        engine.sonarr = faux
        try:
            self.assertEqual(invites.deposer(maintenant=True), 1)
        finally:
            engine.sonarr = ancien
            engine.CACHE.clear()
            engine.CACHE.update(ancien_cache)
        self.assertTrue((serie / "s3" / "NCIS.Los.Angeles.S03E21.mkv").exists())
        self.assertTrue((passage / "Autre.Serie.S03E21.mkv").exists())
        self.assertIn(("POST", "/command"), appels)


class TestImportManuel(unittest.TestCase):
    """Fichier choisi à la main dans les dossiers du serveur : lien (ou copie), jamais de déplacement hors du dossier de passage."""

    def test_import(self):
        import time
        serie = test_engine.TMP / "series" / "Serie Import"
        (serie / "s2").mkdir(parents=True, exist_ok=True)
        (serie / "s2" / "Serie.Import.S02E01.1080p.mkv").write_bytes(b"deja")
        source = test_engine.TMP / "telechargements" / "Serie.Import.2x02.FRENCH.mkv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"contenu" * 100)
        invites.RACINES = (str(test_engine.TMP) + "/",)
        appels = []

        def faux(methode, chemin, corps=None):
            appels.append((methode, chemin))
            if chemin.startswith("/episode/"):
                return {"seriesId": 5, "seasonNumber": 2, "episodeNumber": 2}
            return {"path": str(serie)} if chemin == "/series/5" else {}
        ancien = engine.sonarr
        engine.sonarr = faux
        try:
            nom = invites.importer(70, str(source))
            for _ in range(50):
                if (serie / "s2" / nom).exists() and ("POST", "/command") in appels:
                    break
                time.sleep(0.1)
            with self.assertRaises(ValueError):
                invites.importer(70, str(source))                                    # déjà un fichier à cet endroit
            with self.assertRaises(ValueError):
                invites.importer(70, "/etc/passwd")                                  # hors des dossiers autorisés
        finally:
            engine.sonarr = ancien
        self.assertTrue((serie / "s2" / nom).exists())
        self.assertTrue(source.exists())                                              # l'original reste en place
        self.assertEqual((serie / "s2" / nom).read_bytes(), source.read_bytes())
        self.assertIn(("POST", "/command"), appels)


if __name__ == "__main__":
    unittest.main()
