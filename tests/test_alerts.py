import unittest

import alerts


def carte(cles, statut, nom="Mon crossover", blockers=()):
    return {"name": nom, "date": "2015-04-28", "status": statut, "blockers": list(blockers), "invites": [],
            "parts": [{"key": k, "series": "Série " + k[0], "title": "Titre"} for k in cles]}


class TestAlertes(unittest.TestCase):
    def test_premier_passage_silencieux(self):
        st = {}
        self.assertEqual(alerts.reperer([carte(["1-1-1", "2-1-1"], "todo")], st), [])
        self.assertEqual(len(st["alertes"]["vus"]), 1)

    def test_nouveau_puis_prêt(self):
        st = {}
        alerts.reperer([carte(["1-1-1", "2-1-1"], "done")], st)
        ev = alerts.reperer([carte(["1-1-1", "2-1-1"], "done"), carte(["3-1-1", "4-1-1"], "blocked", blockers=["Partie 3 manquante : x"])], st)
        self.assertEqual([e[0] for e in ev], ["nouveau-bloque"])
        self.assertEqual(alerts.reperer([carte(["3-1-1", "4-1-1"], "blocked")], st), [])           # pas de répétition
        ev = alerts.reperer([carte(["3-1-1", "4-1-1", "5-1-1"], "todo")], st)                    # une partie s'ajoute, puis tout est là
        self.assertEqual([e[0] for e in ev], ["pret"])
        self.assertEqual(alerts.reperer([carte(["3-1-1", "4-1-1", "5-1-1"], "todo")], st), [])

    def test_nouveau_pret_et_ignoré(self):
        st = {}
        alerts.reperer([], st)
        ev = alerts.reperer([carte(["1-1-1", "2-1-1"], "todo"), carte(["6-1-1", "7-1-1"], "ignored")], st)
        self.assertEqual([e[0] for e in ev], ["nouveau-pret"])

    def test_message(self):
        txt = alerts.composer([("nouveau-bloque", carte(["1-1-1", "2-1-1"], "blocked", nom="A & B", blockers=["Partie 2 manquante : x"]))])
        self.assertIn("A &amp; B", txt)
        self.assertIn("série absente", txt)
        self.assertIn("28 avril 2015", txt)


if __name__ == "__main__":
    unittest.main()
