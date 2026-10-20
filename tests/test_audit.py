"""Tests ajoutés par l'audit : saison que la personne n'a plus, retour arrière automatique, identifiants qui changent."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (DATA_DIR / SERIES_DIR temporaires AVANT le moteur)
import engine  # noqa: E402


def crossover(prefixe, saison_b=11, stats_b=None):
    """Deux séries A (id 41) et B (id 42), un épisode chacune, fichiers et fiches sur disque."""
    parts = {}
    for sid, (serie, saison, ep, titre, n) in enumerate([(prefixe + " A", 14, 13, "Alpha (1)", 1), (prefixe + " B", saison_b, 13, "Beta (2)", 2)], start=41):
        te.fabriquer(serie, saison, ep, titre)
        s = {"id": sid, "title": serie, "path": str(te.TMP / "series" / serie)}
        if sid == 42 and stats_b is not None:
            s["seasons"] = [{"seasonNumber": k, "statistics": {"episodeFileCount": v}} for k, v in stats_b.items()]
        parts[(sid, saison, ep)] = {"s": s, "e": {"id": 900 + sid, "seasonNumber": saison, "episodeNumber": ep, "title": titre,
                                                   "hasFile": True, "monitored": True, "airDate": "2015-01-01"}, "part": n, "src": "sonarr"}
    return {"id": "audit-" + prefixe, "name": "Audit", "date": "2015-01-01", "parts": parts}


class Base(unittest.TestCase):
    def setUp(self):
        self.ancien = engine.sonarr
        engine.sonarr = lambda *a, **k: {"monitored": True}
        engine.save_state({"ignored": [], "done": {}, "history": []})

    def tearDown(self):
        engine.sonarr = self.ancien
        engine.ALLITEMS[:] = []


class TestSaisonAbsente(Base):
    def test_question_puis_choix(self):
        c = crossover("Sais", stats_b={11: 1, 12: 8})       # saison 11 : seulement l'épisode du crossover ; saison 12 gardée
        engine.ALLITEMS[:] = [c]
        p = [x for x in engine.part_list(c) if x["seriesId"] == 42][0]
        self.assertTrue(p["saison_absente"])
        plan = engine.make_plan(c)
        self.assertTrue(any(b.startswith("Saison absente") for b in plan["blockers"]))
        # « seulement dans les autres séries » : B ne reçoit rien, son fichier n'est ni renommé ni mis en « non surveillé »
        st = engine.load_state(); st["saisons"] = {p["key"]: "passer"}; engine.save_state(st)
        plan = engine.make_plan(c)
        self.assertEqual(plan["blockers"], [])
        copies = [o for o in plan["ops"] if o["kind"] == "copy_group"]
        self.assertEqual(len(copies), 1)
        self.assertIn("Sais A", copies[0]["dir"])
        self.assertFalse(any(o["kind"] == "rename_group" and "Sais B" in o["dir"] for o in plan["ops"]))
        self.assertFalse(any(o["kind"] == "unmonitor" and o["episodeId"] == 942 for o in plan["ops"]))
        # « ajouter quand même » : comme d'habitude, chacun reçoit l'autre
        st = engine.load_state(); st["saisons"] = {p["key"]: "garder"}; engine.save_state(st)
        plan = engine.make_plan(c)
        self.assertEqual(len([o for o in plan["ops"] if o["kind"] == "copy_group"]), 2)

    def test_saison_gardee_pas_de_question(self):
        c = crossover("Garde", stats_b={11: 9, 12: 8})       # la saison 11 a d'autres épisodes : la personne la garde
        engine.ALLITEMS[:] = [c]
        self.assertFalse(any(x["saison_absente"] for x in engine.part_list(c)))

    def test_derniere_saison_pas_de_question(self):
        c = crossover("Dern", stats_b={10: 5, 11: 1})        # aucune saison plus récente : saison en cours, pas abandonnée
        engine.ALLITEMS[:] = [c]
        self.assertFalse(any(x["saison_absente"] for x in engine.part_list(c)))


class TestRetourArriere(Base):
    def test_erreur_en_cours_de_route(self):
        c = crossover("Err")
        plan = engine.make_plan(c)
        self.assertEqual(plan["blockers"], [])
        avant = sorted(str(p.relative_to(te.TMP)) for p in (te.TMP / "series").rglob("*") if "Err " in str(p) and p.is_file())
        plan["ops"].append({"kind": "patch_nfo", "path": str(te.TMP / "absent.nfo"), "season": 0, "episode": 1, "airs": [1, 1], "label": "panne"})
        res = engine.execute(c, plan, engine.load_state())
        self.assertFalse(res["ok"])
        self.assertIn("remis comme avant", res["error"])
        apres = sorted(str(p.relative_to(te.TMP)) for p in (te.TMP / "series").rglob("*") if "Err " in str(p) and p.is_file())
        self.assertEqual(avant, apres)                         # copies retirées, noms d'origine rendus
        self.assertNotIn(c["id"], engine.load_state()["done"])

    def test_copie_jamais_a_moitie(self):
        src = te.TMP / "src.bin"
        src.write_bytes(b"x" * 1000)
        dst = te.TMP / "dst.bin"
        dst.write_bytes(b"deja")
        with self.assertRaises(RuntimeError):
            engine.copie_suivie(str(src), str(dst))
        self.assertFalse((te.TMP / "dst.bin.mfpart").exists())
        self.assertEqual(dst.read_bytes(), b"deja")


class TestIdentifiants(Base):
    def test_ancien_identifiant_repris(self):
        items = [{"id": "nouveau", "cid_ancien": "ancien", "cles": ["1-1-1", "2-1-1"]}]
        st = {"ignored": ["ancien"], "done": {"ancien": "m.json"}, "fixes": {"ancien": {"order": []}}, "history": [],
              "guests": [{"cid": "ancien"}]}
        self.assertTrue(engine.recoller(items, st))
        self.assertEqual(st["done"], {"nouveau": "m.json"})
        self.assertEqual(st["ignored"], ["nouveau"])
        self.assertIn("nouveau", st["fixes"])
        self.assertEqual(st["guests"][0]["cid"], "nouveau")

    def test_retrouve_par_les_episodes(self):
        # une partie de plus a été détectée : nouvel identifiant, mais mêmes épisodes qu'avant + un
        items = [{"id": "x2", "cid_ancien": "x2", "cles": ["1-1-1", "2-1-1", "3-1-1"]}, {"id": "y", "cid_ancien": "y", "cles": ["7-1-1"]}]
        st = {"ignored": [], "done": {"x1": "m.json"}, "history": [], "cles": {"x1": ["1-1-1", "2-1-1"]}}
        engine.recoller(items, st)
        self.assertEqual(st["done"], {"x2": "m.json"})
        self.assertEqual(st["cles"]["x2"], ["1-1-1", "2-1-1", "3-1-1"])


class TestAnnulationIncomplete(Base):
    def test_erreur_garde_le_rangement(self):
        c = crossover("Und")
        res = engine.execute(c, engine.make_plan(c), engine.load_state())
        self.assertTrue(res["ok"], res)
        st = engine.load_state()
        # le nom d'origine a été repris par un autre fichier : l'annulation ne doit rien écraser et garder le rangement
        renomme = next(a for a in __import__("json").load(open(st["done"][c["id"]]))["actions"] if a["type"] == "rename")
        Path(renomme["from"]).write_bytes(b"intrus")
        res = engine.undo(c["id"], st)
        self.assertFalse(res["ok"])
        self.assertIn(c["id"], engine.load_state()["done"])
        os.remove(renomme["from"])
        res = engine.undo(c["id"], engine.load_state())           # on peut la relancer
        self.assertTrue(res["ok"], res)


if __name__ == "__main__":
    unittest.main()
