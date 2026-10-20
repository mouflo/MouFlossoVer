"""Crossovers décrits par Wikipédia en anglais : les pages qui utilisent le modèle « Infobox television crossover episode »
donnent, pour chaque partie, la série, la saison, l'épisode et le titre. Données très fiables, gardées dans
data/enwiki_crossovers.json (rafraîchies chaque semaine) ; aucune dépendance à la bibliothèque pour les lire.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request

import engine
import wiki as wikimod

logger = logging.getLogger(__name__)
UA = "MouFlossover/1.0 (outil personnel de médiathèque)"
TEMPLATE = "Infobox television crossover episode"
TTL = 30 * 86400
ETAT = {"running": False, "pages": 0}
LOCK = threading.Lock()


def _api(params):
    u = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(dict(params, format="json"))
    for essai in range(4):
        time.sleep(1.0)
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=40).read())
        except Exception:
            if essai == 3:
                raise
            time.sleep(8 * (essai + 1))


def _chemin():
    return os.path.join(engine.DATA, "enwiki_crossovers.json")


def charger():
    try:
        with open(_chemin(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"fetched": 0, "pages": {}}


def _sauver(d):
    tmp = _chemin() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, _chemin())


def _bloc(texte):
    """Texte du modèle, accolades équilibrées (les champs contiennent d'autres modèles)."""
    i = texte.find("{{" + TEMPLATE)
    if i < 0:
        return ""
    prof = 0
    for j in range(i, len(texte) - 1):
        d = texte[j:j + 2]
        if d == "{{":
            prof += 1
        elif d == "}}":
            prof -= 1
            if prof == 0:
                return texte[i:j + 2]
    return texte[i:]


def _lien(v):
    """« [[Hawaii Five-0 (2010 TV series)|Hawaii Five-0]] » -> (page, nom affiché)."""
    m = re.search(r"\[\[([^\]|#]+)(?:\|([^\]]*))?\]\]", v)
    if m:
        return m.group(1).strip(), (m.group(2) or m.group(1)).strip()
    t = re.sub(r"[\[\]']", "", v).strip()
    return t, t


def analyser(titre, texte):
    """Parties du crossover décrit par la page : [{n, series, page, season, episode, title}] (n = rang de la partie)."""
    champs = {}
    for ligne in _bloc(texte).split("\n"):
        m = re.match(r"^\s*\|\s*([a-z_]+?)(\d+)\s*=\s*(.*)$", ligne)
        if m:
            champs.setdefault(int(m.group(2)), {})[m.group(1)] = m.group(3).strip()
    parts = []
    for n in sorted(champs):
        c = champs[n]
        if "series" not in c:
            continue
        page, nom = _lien(c["series"])
        s = re.search(r"\d+", c.get("season", ""))
        e = re.search(r"\d+", c.get("episode_no", ""))
        parts.append({"n": n, "series": nom, "page": re.sub(r"\s*\([^)]*\)\s*$", "", page).strip(),
                      "season": int(s.group()) if s else None, "episode": int(e.group()) if e else None,
                      "title": re.sub(r"[\[\]']|<[^>]*>", "", c.get("episode_title", "")).strip()})
    return parts


def scan(force=False):
    if ETAT["running"]:
        return
    ETAT["running"] = True
    try:
        d = charger()
        if not force and time.time() - d.get("fetched", 0) < TTL:
            return
        titres = [x["title"] for x in _api({"action": "query", "list": "embeddedin", "eititle": "Template:" + TEMPLATE,
                                            "einamespace": 0, "eilimit": 500}).get("query", {}).get("embeddedin", [])]
        pages = {}
        for i in range(0, len(titres), 15):
            r = _api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                      "titles": "|".join(titres[i:i + 15]), "redirects": 1})
            for p in r.get("query", {}).get("pages", {}).values():
                txt = (p.get("revisions") or [{}])[0].get("slots", {}).get("main", {}).get("*", "")
                parts = analyser(p["title"], txt)
                if len(parts) >= 2:
                    pages[p["title"]] = parts
        with LOCK:
            _sauver({"fetched": time.time(), "pages": pages})
        ETAT["pages"] = len(pages)
    except Exception:
        logger.exception("Analyse de Wikipédia en anglais")
    finally:
        ETAT["running"] = False


def resoudre(parts, par_nom, episodes_de):
    """Rapproche les parties d'une page de la bibliothèque : (trouvées, absentes).
    trouvées : [(série Sonarr, épisode Sonarr, rang n)] ; absentes : parties dont la série ou l'épisode manque."""
    ok, ko = [], []
    for p in parts:
        sr = par_nom.get(wikimod.norm(p["series"])) or par_nom.get(wikimod.norm(p["page"]))
        if not sr or not p["season"] or not p["episode"]:
            ko.append(p)
            continue
        ep = next((e for e in episodes_de(sr["id"]) if e["seasonNumber"] == p["season"] and e["episodeNumber"] == p["episode"]), None)
        if ep:
            ok.append((sr, ep, p["n"]))
        else:
            ko.append(p)
    return ok, ko


def lien(titre):
    return "https://en.wikipedia.org/wiki/" + urllib.parse.quote(titre.replace(" ", "_"), safe="()',:!&")


def crossovers():
    return charger()["pages"]


def demarrer():
    def boucle():
        time.sleep(60)
        while True:
            scan()
            time.sleep(24 * 3600)
    threading.Thread(target=boucle, daemon=True).start()
