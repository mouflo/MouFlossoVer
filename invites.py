"""Épisodes invités : une partie de crossover qui vient d'une série absente de la bibliothèque.

MouFlossover ajoute discrètement la série dans Sonarr (dossier de passage hors d'Emby, aucun épisode surveillé sauf
celui demandé), lance la recherche de cet épisode, attend son arrivée, puis le crossover le range comme les autres
parties. Tout tourne dans l'appli, sans intervention : l'état est gardé dans data/state.json et reprend au redémarrage.

Rien n'est jamais supprimé : le fichier reste dans le dossier de passage (lien dur vers le fichier de qBittorrent, qui
continue donc de partager) et la série invitée reste dans Sonarr, sans épisode surveillé.
"""
import logging
import os
import re
import shutil
import threading
import time
import urllib.parse
import uuid

import engine

logger = logging.getLogger(__name__)
TAG = "mouflossover-invite"
RECHERCHE_TOUTES_LES = 6 * 3600          # nouvelle recherche si rien n'est arrivé
VERIFIE_TOUTES_LES = 60
ETATS_FINAUX = ("pret", "annule", "range")


VIDEO = (".mkv", ".mp4", ".avi", ".m4v")
RX_SE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})(?!\d)")
BLOQUE_APRES = 4 * 3600          # source sans seed : on la remplace après 4 h
MAX_REMPLACEMENTS = 5
_VUS = {}                          # fichier déposé -> taille vue au passage précédent (pour attendre la fin de la copie)


def _mots(texte):
    import unicodedata
    t = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().lower()
    return {m for m in re.findall(r"[a-z0-9]+", t) if len(m) > 2}


def _cibles():
    """Épisodes qu'on attend et qu'un fichier déposé peut compléter : demandes d'épisodes invités, et épisodes manquants
    (pas de fichier) d'un crossover, dans une série de la bibliothèque."""
    out = []
    for g in _guests():
        if g["status"] in ("recherche", "attente") and g.get("sonarrId"):
            out.append({"type": "invite", "gid": g["id"], "sid": g["sonarrId"], "titre": g["title"], "season": g["season"],
                        "ep": g["episode"], "eid": g.get("episodeId")})
    vus = set()
    for c in engine.CACHE.get("items", []):
        for p in c["parts"]:
            if not p["hasFile"] and p["source"] != "invite" and (p["seriesId"], p["season"], p["ep"]) not in vus:
                vus.add((p["seriesId"], p["season"], p["ep"]))
                out.append({"type": "biblio", "sid": p["seriesId"], "titre": p["series"], "season": p["season"], "ep": p["ep"], "eid": p["episodeId"]})
    return out


def deposer(maintenant=False):
    """Fichiers déposés à la main à la racine du dossier de passage (ex. « Serie.S16E20c.1080p…mkv ») : si la saison et l'épisode
    correspondent à un épisode attendu, le fichier est rangé au bon endroit (dossier de la série invitée, ou saison de la série
    de la bibliothèque), puis Sonarr relit la série. Pour une série de la bibliothèque, le nom du fichier doit aussi ressembler
    au titre de la série. Renvoie le nombre de fichiers pris en charge."""
    dossier = engine.staging_dir()
    cibles = _cibles()
    if not cibles or not os.path.isdir(dossier):
        return 0
    pris, a_relire = 0, {}
    for f in sorted(os.listdir(dossier)):
        p = os.path.join(dossier, f)
        m = RX_SE.search(f)
        if not (os.path.isfile(p) and f.lower().endswith(VIDEO) and m):
            continue
        sa, ep = int(m.group(1)), int(m.group(2))
        fm = _mots(f)
        cands = [c for c in cibles if c["season"] == sa and c["ep"] == ep]
        score = lambda c: len(_mots(c["titre"]) & fm)
        cands = [c for c in cands if c["type"] == "invite" or score(c) >= 1]
        if len(cands) > 1:
            cands.sort(key=lambda c: -score(c))
            if score(cands[0]) == score(cands[1]):
                continue
        if not cands:
            continue
        c = cands[0]
        taille = os.path.getsize(p)
        if not maintenant and (time.time() - os.path.getmtime(p) < 90 or _VUS.get(p) != taille):
            _VUS[p] = taille                       # copie peut-être encore en cours : on revérifie au prochain passage
            continue
        try:
            serie = engine.sonarr("GET", "/series/%d" % c["sid"])
            reste = re.sub(r"^[a-zA-Z](?=[.\s_-])", "", f[m.end():])
            if c["type"] == "invite":
                cible = os.path.join(serie["path"], "Season %02d" % sa)
                prefixe = os.path.basename(serie["path"].rstrip("/"))
            else:                                   # série de la bibliothèque : on suit ses habitudes de rangement
                cible = engine.season_dir(serie["path"], sa) or os.path.join(serie["path"], "s%d" % sa)
                prefixe = engine.file_prefix(serie["path"])
            os.makedirs(cible, exist_ok=True)
            nom = "%s.S%02dE%02d%s" % (prefixe, sa, ep, reste)
            if os.path.exists(os.path.join(cible, nom)):
                logger.warning("Fichier déposé %s : %s existe déjà, on n'y touche pas", f, nom)
                continue
            shutil.move(p, os.path.join(cible, nom))
            a_relire[c["sid"]] = c
            if c.get("eid"):
                q = engine.file_dl().get(c["eid"])
                if q and q.get("id"):               # plus besoin de télécharger : on retire la demande en attente chez Sonarr
                    engine.sonarr("DELETE", "/queue/%d?removeFromClient=false&blocklist=false" % q["id"])
            if c["type"] == "invite":
                _maj(c["gid"], message="Fichier déposé reconnu et rangé : Sonarr le relit", next_try=0)
                threading.Thread(target=_un_pas, args=(c["gid"],), daemon=True).start()
            logger.info("Fichier déposé %s rangé dans %s", f, cible)
            _VUS.pop(p, None)
            pris += 1
        except Exception as ex:
            logger.warning("Fichier déposé %s : %s", f, ex)
    for sid in a_relire:        # une seule relecture par série, une fois tous les fichiers rangés (Sonarr ignore une relecture demandée pendant une autre)
        engine.sonarr("POST", "/command", {"name": "RescanSeries", "seriesId": sid})
    if a_relire:
        threading.Thread(target=lambda: (time.sleep(25), engine.refresh()), daemon=True).start()
    return pris


def nettoyer_prets():
    """Quand un crossover avec épisode invité est rangé (l'épisode est copié dans toutes les autres séries), le fichier du
    dossier de passage ne sert plus : on le retire (via Sonarr, qui garde sa base à jour) pour ne pas garder de la place
    pour rien. L'épisode est d'abord mis en « non surveillé » pour que Sonarr ne le retélécharge pas. Le torrent de
    qBittorrent n'est pas touché : il continue de partager (le fichier du dossier de passage n'en était qu'un lien)."""
    for g in _guests():
        if g["status"] != "pret":
            continue
        c = next((i for i in engine.CACHE.get("items", []) if i["status"] == "done" and i["applied"]
                  and set(g["with"]) <= {p["key"] for p in i["parts"]}
                  and any(p["source"] == "invite" and p["season"] == g["season"] and p["ep"] == g["episode"] for p in i["parts"])), None)
        if not c:
            continue
        try:
            ep = _episode(g)
            if ep and ep.get("hasFile") and ep.get("episodeFileId"):
                if ep.get("monitored"):
                    ep["monitored"] = False
                    engine.sonarr("PUT", "/episode/%d" % ep["id"], ep)
                engine.sonarr("DELETE", "/episodefile/%d" % ep["episodeFileId"])
            serie = engine.sonarr("GET", "/series/%d" % g["sonarrId"]) or {}
            for dp, dn, fn in sorted(os.walk(serie.get("path", "/nonexistent"), topdown=False), key=lambda t: -len(t[0])):
                try:
                    os.rmdir(dp)            # seulement si le dossier est vide
                except OSError:
                    pass
            _maj(g["id"], status="range", message="Copié dans les autres séries : fichier retiré du dossier de passage")
            logger.info("Épisode invité %s S%02dE%02d retiré du dossier de passage", g["title"], g["season"], g["episode"])
        except Exception as ex:
            logger.warning("Nettoyage de l'épisode invité %s : %s", g["id"], ex)


def reactiver(cid):
    """Crossover annulé : les épisodes invités déjà retirés sont redemandés (l'appli a besoin de leur fichier pour re-ranger)."""
    for g in _guests():
        if g["status"] == "range" and g["cid"] == cid:
            _maj(g["id"], status="recherche", message="Crossover annulé : épisode redemandé", next_try=0)
            threading.Thread(target=_un_pas, args=(g["id"],), daemon=True).start()


def debloquer(eid):
    """Téléchargement bloqué (aucune source) depuis plus de 4 h : on le retire en l'ajoutant à la liste noire, et Sonarr
    cherche alors la version suivante. Renvoie True si une autre source a été demandée."""
    q = engine.file_dl().get(eid)
    if not q or not q.get("id") or not (q["status"] == "warning" or q["avert"]):
        return False
    try:
        import calendar
        depuis = time.time() - calendar.timegm(time.strptime(q["added"][:19], "%Y-%m-%dT%H:%M:%S")) if q["added"] else 0
    except ValueError:
        depuis = 0
    if depuis < BLOQUE_APRES:
        return False
    with engine.LOCK:
        st = engine.load_state()
        n = st.setdefault("remplacements", {}).get(str(eid), 0)
        if n >= MAX_REMPLACEMENTS:
            return False
        st["remplacements"][str(eid)] = n + 1
        engine.save_state(st)
    engine.sonarr("DELETE", "/queue/%d?removeFromClient=true&blocklist=true" % q["id"])
    engine.sonarr("POST", "/command", {"name": "EpisodeSearch", "episodeIds": [eid]})
    return True


def sources(eid):
    """Versions disponibles chez vos indexeurs pour un épisode (recherche manuelle de Sonarr), les mieux partagées d'abord."""
    res = engine.sonarr("GET", "/release?episodeId=%d" % eid) or []
    out = []
    for r in res:
        out.append({"guid": r.get("guid"), "indexerId": r.get("indexerId"), "title": r.get("title"), "indexer": r.get("indexer"),
                    "seeders": r.get("seeders"), "leechers": r.get("leechers"), "size": r.get("size") or 0,
                    "quality": ((r.get("quality") or {}).get("quality") or {}).get("name", ""),
                    "langues": ", ".join(l.get("name", "") for l in r.get("languages") or []),
                    "protocol": r.get("protocol"), "rejected": bool(r.get("rejected")),
                    "raison": "; ".join(r.get("rejections") or [])[:140]})
    out.sort(key=lambda x: (x["rejected"], -(x["seeders"] or 0)))
    return out[:25]


def choisir_source(guid, indexer_id):
    return engine.sonarr("POST", "/release", {"guid": guid, "indexerId": indexer_id})


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
            if ep and not ep.get("hasFile") and time.time() - g.get("last_scan", 0) > 90:
                serie = engine.sonarr("GET", "/series/%d" % g["sonarrId"]) or {}
                if any(f.lower().endswith(VIDEO) for _, _, fs in os.walk(serie.get("path", "/nonexistent")) for f in fs):
                    engine.sonarr("POST", "/command", {"name": "RescanSeries", "seriesId": g["sonarrId"]})
                    _maj(gid, last_scan=time.time())
            if ep and not ep.get("hasFile") and g.get("episodeId") and debloquer(g["episodeId"]):
                _maj(gid, message="Source sans partage remplacée : Sonarr cherche une autre version")
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
        try:
            debloquer(int(eid))
        except Exception:
            logger.exception("Remplacement d'une source bloquée")
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
            deposer()
            nettoyer_prets()
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
    return [{"id": g["id"], "episodeId": g.get("episodeId"), "title": g["title"], "season": g["season"], "episode": g["episode"], "status": g["status"],
             "message": g.get("message", "")} for g in _guests() if g["status"] != "annule" and set(g["with"]) <= c_keys]
