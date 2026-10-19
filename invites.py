"""Épisodes invités : une partie de crossover qui vient d'une série absente de la bibliothèque.

MouFlossover ajoute discrètement la série dans Sonarr (dossier de passage hors d'Emby, aucun épisode surveillé sauf
celui demandé), lance la recherche de cet épisode, attend son arrivée, puis le crossover le range comme les autres
parties. Tout tourne dans l'appli, sans intervention : l'état est gardé dans data/state.json et reprend au redémarrage.

Rien n'est jamais supprimé : le fichier reste dans le dossier de passage (lien dur vers le fichier de qBittorrent, qui
continue donc de partager) et la série invitée reste dans Sonarr, sans épisode surveillé.
"""
import logging
import os
import threading
import time
import urllib.parse
import uuid

import engine

logger = logging.getLogger(__name__)
TAG = "mouflossover-invite"
RECHERCHE_TOUTES_LES = 6 * 3600          # nouvelle recherche si rien n'est arrivé
VERIFIE_TOUTES_LES = 60
ETATS_FINAUX = ("pret", "annule")


def lookup(terme):
    """Recherche de séries par Sonarr (même base que TVDB) : liste courte et lisible."""
    res = engine.sonarr("GET", "/series/lookup?term=" + urllib.parse.quote(terme)) or []
    out = []
    for r in res[:8]:
        out.append({"title": r.get("title"), "year": r.get("year"), "tvdbId": r.get("tvdbId"),
                    "overview": (r.get("overview") or "")[:160], "seasons": len(r.get("seasons") or [])})
    return out


def _maj(gid, **kw):
    with engine.LOCK:
        st = engine.load_state()
        for g in st.get("guests", []):
            if g["id"] == gid:
                g.update(kw)
        engine.save_state(st)


def _guests():
    return list(engine.load_state().get("guests", []))


def creer(c, tvdb_id, titre, saison, episode):
    """Enregistre la demande et la lance tout de suite en arrière-plan."""
    parts = engine.part_list(c)
    numeros = sorted(p["part"] for p in parts)
    trous = [n for n in range(1, numeros[-1] + 1) if n not in numeros]
    g = {"id": uuid.uuid4().hex[:8], "cid": c["id"], "with": [p["key"] for p in parts if not engine.est_invite(p["path"])],
         "tvdbId": int(tvdb_id), "title": titre, "season": int(saison), "episode": int(episode),
         "part": trous[0] if trous else numeros[-1] + 1, "status": "ajout", "message": "Demande enregistrée",
         "sonarrId": None, "episodeId": None, "next_try": 0, "created": int(time.time())}
    with engine.LOCK:
        st = engine.load_state()
        if any(x["tvdbId"] == g["tvdbId"] and x["season"] == g["season"] and x["episode"] == g["episode"] and x["status"] not in ETATS_FINAUX
               and set(x["with"]) & set(g["with"]) for x in st.get("guests", [])):
            return {"ok": False, "error": "Cet épisode est déjà demandé pour ce crossover."}
        st.setdefault("guests", []).append(g)
        engine.save_state(st)
    threading.Thread(target=_un_pas, args=(g["id"],), daemon=True).start()
    return {"ok": True, "id": g["id"]}


def _tag_id():
    for t in engine.sonarr("GET", "/tag") or []:
        if t["label"] == TAG:
            return t["id"]
    return engine.sonarr("POST", "/tag", {"label": TAG})["id"]


def _racine():
    dossier = engine.staging_dir()
    os.makedirs(dossier, exist_ok=True)
    if dossier.rstrip("/") not in [r["path"].rstrip("/") for r in engine.sonarr("GET", "/rootfolder") or []]:
        engine.sonarr("POST", "/rootfolder", {"path": dossier})
    return dossier


def _profil(g):
    """Profil de qualité : celui de la série voisine du crossover (le même rendu que le reste de la bibliothèque)."""
    ids = {int(k.split("-")[0]) for k in g["with"]}
    for s in engine.sonarr("GET", "/series") or []:
        if s["id"] in ids:
            return s["qualityProfileId"]
    return (engine.sonarr("GET", "/qualityprofile") or [{"id": 1}])[0]["id"]


def _ajouter_serie(g):
    for s in engine.sonarr("GET", "/series") or []:
        if s.get("tvdbId") == g["tvdbId"]:
            return s["id"]
    racine = _racine()
    trouve = next((r for r in engine.sonarr("GET", "/series/lookup?term=tvdb:%d" % g["tvdbId"]) or [] if r.get("tvdbId") == g["tvdbId"]), None)
    if not trouve:
        raise RuntimeError("Série introuvable chez Sonarr (TVDB %d)" % g["tvdbId"])
    trouve.pop("id", None)
    trouve.update(rootFolderPath=racine, qualityProfileId=_profil(g), monitored=True, seasonFolder=True, tags=[_tag_id()],
                  monitorNewItems="none",
                  addOptions={"monitor": "none", "searchForMissingEpisodes": False, "searchForCutoffUnmetEpisodes": False})
    return engine.sonarr("POST", "/series", trouve)["id"]


def _episode(g):
    for e in engine.sonarr("GET", "/episode?seriesId=%d" % g["sonarrId"]) or []:
        if e["seasonNumber"] == g["season"] and e["episodeNumber"] == g["episode"]:
            return e
    return None


def _un_pas(gid):
    g = next((x for x in _guests() if x["id"] == gid), None)
    if not g or g["status"] in ETATS_FINAUX:
        return
    changement = False
    try:
        if g["status"] == "ajout":
            sid = _ajouter_serie(g)
            _maj(gid, sonarrId=sid, status="recherche", message="Série ajoutée dans le dossier de passage", next_try=time.time() + 20)
            changement = True
        elif g["status"] == "recherche":
            ep = _episode(g)
            if not ep:
                _maj(gid, message="Sonarr prépare la liste des épisodes…", next_try=time.time() + 30)
            else:
                if not ep.get("monitored"):
                    ep["monitored"] = True
                    engine.sonarr("PUT", "/episode/%d" % ep["id"], ep)
                engine.sonarr("POST", "/command", {"name": "EpisodeSearch", "episodeIds": [ep["id"]]})
                _maj(gid, episodeId=ep["id"], status="attente", message="Recherche lancée chez vos indexeurs",
                     next_try=time.time() + VERIFIE_TOUTES_LES)
                _maj(gid, last_search=time.time())
                changement = True
        elif g["status"] == "attente":
            ep = _episode(g)
            if ep and ep.get("hasFile"):
                _maj(gid, status="pret", message="Épisode reçu")
                changement = True
            else:
                msg = "En attente d'une version de l'épisode"
                file_ = engine.sonarr("GET", "/queue/details?episodeIds=%d" % g["episodeId"]) or []
                if file_:
                    q = file_[0]
                    taille = q.get("size") or 0
                    pct = int(100 * (taille - (q.get("sizeleft") or 0)) / taille) if taille else 0
                    msg = "Téléchargement en cours : %d %%" % pct
                elif time.time() - g.get("last_search", 0) >= RECHERCHE_TOUTES_LES:
                    engine.sonarr("POST", "/command", {"name": "EpisodeSearch", "episodeIds": [g["episodeId"]]})
                    _maj(gid, last_search=time.time())
                    msg = "Aucune version trouvée pour l'instant : nouvelle recherche lancée"
                _maj(gid, message=msg, next_try=time.time() + VERIFIE_TOUTES_LES)
    except Exception as ex:
        logger.warning("Épisode invité %s : %s", gid, ex)
        _maj(gid, message="Erreur : %s (nouvel essai dans 5 min)" % ex, next_try=time.time() + 300)
    if changement:
        threading.Thread(target=engine.refresh, daemon=True).start()


def relancer_demandes():
    """Épisodes demandés par le bouton « Télécharger » : nouvelle recherche toutes les 6 h tant qu'ils ne sont pas là."""
    maintenant = time.time()
    for eid, d in list(engine.load_state().get("demandes", {}).items()):
        if maintenant - d.get("last", 0) < RECHERCHE_TOUTES_LES:
            continue
        ep = engine.sonarr("GET", "/episode/%s" % eid)
        with engine.LOCK:
            st = engine.load_state()
            if not ep or ep.get("hasFile"):
                st.get("demandes", {}).pop(eid, None)
            elif eid in st.get("demandes", {}):
                st["demandes"][eid]["last"] = int(maintenant)
            engine.save_state(st)
        if ep and not ep.get("hasFile") and int(eid) not in engine.file_dl():
            engine.sonarr("POST", "/command", {"name": "EpisodeSearch", "episodeIds": [int(eid)]})


def boucle():
    """Fait avancer toutes les demandes, toutes les minutes, tant que l'appli tourne."""
    while True:
        time.sleep(30)
        try:
            maintenant = time.time()
            for g in _guests():
                if g["status"] not in ETATS_FINAUX and maintenant >= g.get("next_try", 0):
                    _un_pas(g["id"])
            relancer_demandes()
        except Exception:
            logger.exception("Boucle des épisodes invités")


def demarrer():
    threading.Thread(target=boucle, daemon=True).start()


def relancer(gid):
    _maj(gid, status="ajout" if not next((g for g in _guests() if g["id"] == gid), {}).get("sonarrId") else "recherche",
         message="Nouvel essai demandé", next_try=0)
    threading.Thread(target=_un_pas, args=(gid,), daemon=True).start()


def annuler(gid):
    _maj(gid, status="annule", message="Demande annulée (la série reste dans Sonarr sans épisode surveillé)")
    threading.Thread(target=engine.refresh, daemon=True).start()


def suggestions(c):
    """Épisodes cités par Wikipédia pour ce crossover mais d'une série absente de la bibliothèque."""
    import wiki as wikimod
    series = engine.sonarr("GET", "/series") or []
    connues = {wikimod.norm(s["title"]) for s in series}
    for s in series:
        pg = engine.WIKI.page_of(s["title"])
        if pg:
            connues.add(wikimod.norm(pg))
    saisons = {}
    for p in engine.part_list(c):
        saisons.setdefault(p["series"], set()).add(p["season"])
    out, vus = [], set()
    import enwiki
    par_nom = {wikimod.norm(s["title"]): s for s in series}
    cles = {p["key"] for p in engine.part_list(c)}
    for titre_page, pp in enwiki.crossovers().items():
        ok, ko = enwiki.resoudre(pp, par_nom, engine.episodes_of)
        if len(cles & {"%d-%d-%d" % (sr["id"], ep["seasonNumber"], ep["episodeNumber"]) for sr, ep, _ in ok}) >= 2:
            for x in ko:
                if x["season"] and x["episode"]:
                    out.append({"title": x["series"].replace("ʻ", "'"), "season": x["season"], "episode": x["episode"],
                                "line": "Wikipédia (anglais) : %s, partie %d du crossover" % (titre_page, x["n"])})
    for titre, ss in saisons.items():
        rec = engine.WIKI.get(titre)
        for cl in (rec or {}).get("claims", []):
            if cl["bn"] in connues:
                continue
            import re
            m = re.search(r"Saison\s+(\d+)", cl.get("page") or "")
            if not m or int(m.group(1)) not in ss:      # seulement les pages de la saison concernée (sinon trop de bruit)
                continue
            cle = (cl["bn"], cl["season"], cl["episode"])
            if cle in vus:
                continue
            vus.add(cle)
            out.append({"title": cl["b"], "season": cl["season"], "episode": cl["episode"], "line": engine.lisible(cl.get("line"))})
    return out[:5]


def pour_carte(c_keys):
    """Demandes liées à une carte (celles dont les parties d'origine sont toutes dans le crossover)."""
    return [{"id": g["id"], "title": g["title"], "season": g["season"], "episode": g["episode"], "status": g["status"],
             "message": g.get("message", "")} for g in _guests() if g["status"] != "annule" and set(g["with"]) <= c_keys]
