"""Crossovers repérés grâce à TheTVDB : Sonarr reçoit de TheTVDB le titre et le résumé de chaque épisode.
Quand le résumé d'un épisode parle de « crossover » (ou de « suite / conclusion ») ET nomme une autre série de la
bibliothèque, on en tire un indice. L'analyse est faite en arrière-plan, gardée dans data/tvdb_hints.json (une série n est relue que si elle a changé, ou après un mois)
et ne demande que des échanges avec Sonarr, jamais avec le site de TheTVDB.
"""
import json
import logging
import os
import re
import threading
import time
import unicodedata

import engine

logger = logging.getLogger(__name__)
TTL = 30 * 86400          # sans changement dans la série, on ne la relit qu'une fois par mois
VERSION = 2         # change quand les règles de lecture changent : tout est relu
MOTS = re.compile(r"cross-?over|continues? (?:on|in|from|with)|concludes? (?:on|in|with)|conclusion (?:of|to)|"
                  r"part (?:one|two|three|1|2|3)\b|first part|second part|final part|two-part|three-part|picks? up (?:from|after)", re.I)
LOCK = threading.Lock()
ETAT = {"running": False, "done": 0, "total": 0, "hints": 0}


def _chemin():
    return os.path.join(engine.DATA, "tvdb_hints.json")


def _charger():
    try:
        with open(_chemin(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"scanned": {}, "hints": [], "version": VERSION}


def _sauver(d):
    tmp = _chemin() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, _chemin())


def _fold(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def noms_cherchables(series):
    """Titres de série utilisables dans un texte (sans l'année ; les titres d'un seul mot court sont trop ambigus)."""
    out = []
    for s in series:
        t = re.sub(r"\s*\(\d{4}\)\s*$", "", s["title"]).strip()
        if len(t.split()) == 1 and len(t) < 9:
            continue
        out.append((s["id"], t, re.compile(r"(?<![\w])" + re.escape(t).replace(r"\ ", r"\s+") + r"(?![\w])")))
    return out


def extrait(texte):
    return re.sub(r"\s+", " ", texte).strip()[:260]


REF = re.compile(r"\W{0,3}(?:S(\d{1,2})\s?E(\d{1,2})|season (\d+),? episode (\d+))", re.I)


def indices_de(serie, episodes, noms):
    """Indices pour une série : [{sid, season, episode, oid, line, bs, be}] pour les épisodes dont le résumé parle de
    crossover à moins de 200 caractères du nom d'une autre série de la bibliothèque (bs/be : saison et épisode cités)."""
    out = []
    for e in episodes:
        ov = e.get("overview") or ""
        if e["seasonNumber"] < 1 or not ov:
            continue
        mots = [m.start() for m in MOTS.finditer(ov)]
        if not mots:
            continue
        for oid, titre, rx in noms:
            if oid == serie["id"]:
                continue
            m = rx.search(ov)
            if not m or min(abs(m.start() - k) for k in mots) > 200:
                continue
            h = {"sid": serie["id"], "season": e["seasonNumber"], "episode": e["episodeNumber"], "oid": oid, "line": extrait(ov)}
            r = REF.match(ov[m.end():m.end() + 30])
            if r:
                h["bs"] = int(r.group(1) or r.group(3))
                h["be"] = int(r.group(2) or r.group(4))
            out.append(h)
    return out


def scan(force=False):
    if ETAT["running"]:
        return
    ETAT.update(running=True, done=0)
    try:
        series = [s for s in engine.sonarr("GET", "/series") if not engine.est_invite(s.get("path", ""))]
        noms = noms_cherchables(series)
        ETAT["total"] = len(series)
        d = _charger()
        if d.get("version") != VERSION:
            d = {"scanned": {}, "hints": [], "version": VERSION}
        now = time.time()
        for s in series:
            sid = str(s["id"])
            ETAT["done"] += 1
            if not force and s.get("ended") and sid in d["scanned"]:
                continue          # série terminée déjà lue une fois : plus rien de nouveau à attendre
            st_ = s.get("statistics") or {}
            signature = "%s|%s|%s" % (st_.get("episodeCount"), st_.get("episodeFileCount"), s.get("previousAiring") or st_.get("previousAiring"))
            sig = d.setdefault("sig", {})
            if not force and now - d["scanned"].get(sid, 0) < TTL and sig.get(sid, signature) == signature:
                continue          # rien de nouveau dans cette série : on ne la relit pas
            try:
                eps = engine.sonarr("GET", "/episode?seriesId=%d" % s["id"]) or []
            except Exception:
                continue
            with LOCK:
                d["hints"] = [h for h in d["hints"] if h["sid"] != s["id"]] + indices_de(s, eps, noms)
                d["scanned"][sid] = now
                sig[sid] = signature
            if ETAT["done"] % 50 == 0:
                _sauver(d)
        _sauver(d)
        ETAT["hints"] = len(d["hints"])
    except Exception:
        logger.exception("Analyse des résumés TheTVDB")
    finally:
        ETAT["running"] = False


def indices():
    return _charger()["hints"]


def demarrer():
    def boucle():
        time.sleep(40)
        while True:
            scan()
            time.sleep(24 * 3600)
    threading.Thread(target=boucle, daemon=True).start()
