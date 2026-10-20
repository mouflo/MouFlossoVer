#!/usr/bin/env python3
"""MouFlossover : range les crossovers entre séries dans Emby (chaque série contient toutes les parties)."""
import logging
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
_SECRETS_FILE = DATA_DIR / "secrets.env"

try:
    from dotenv import load_dotenv
    for _f in (BASE_DIR / ".env", _SECRETS_FILE):
        if _f.exists():
            load_dotenv(_f, override=True)
except ImportError:
    pass  # python-dotenv absent : variables d'environnement du système seulement

os.environ.setdefault("DATA_DIR", str(DATA_DIR))

import diag
import emby
import engine

BASE_VERSION = "1.0"
DEPART_VERSION = 0            # nombre de commits au passage à la version de base : la version repart de x.y.0


def get_version():
    """Version = base manuelle + nombre de commits (numéro de déploiement) + hash court."""
    try:
        import subprocess
        cwd = str(BASE_DIR)
        count = subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
        short = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
        return f"v{BASE_VERSION}.{max(0, int(count) - DEPART_VERSION)} ({short})"
    except Exception:
        return f"v{BASE_VERSION}"


APP_VERSION = get_version()

diag.setup_logging()
logger = logging.getLogger("mouflossover")

app = Flask(__name__)
app.json.ensure_ascii = False

import auth
auth.init_app(app, APP_VERSION)
import apparence
apparence.init_app(app)


@app.route("/")
def index():
    return render_template("index.html", version=APP_VERSION)


@app.route("/api/version")
def api_version():
    return jsonify({"version": APP_VERSION})


@app.route("/api/occupe")
def api_occupe():
    """Utilisé par deploy.sh : la mise à jour attend la fin d'un rangement ou d'une analyse."""
    raisons = engine.occupe()
    return jsonify({"occupe": bool(raisons), "raisons": raisons})


@app.route("/api/crossovers")
def api_crossovers():
    c = engine.CACHE
    return jsonify({"items": c["items"], "updated": c["updated"], "error": c["error"]})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    engine.refresh()
    return jsonify({"ok": True, "updated": engine.CACHE["updated"]})


@app.route("/api/crossovers/<cid>/plan")
def api_plan(cid):
    with engine.LOCK:
        c = engine.find_crossover(cid)
        if not c:
            return jsonify({"error": "Crossover introuvable (la liste a peut-être changé : actualise)."}), 404
        return jsonify(engine.make_plan(c))


def _dossiers(c):
    """Dossiers des séries touchées par ce crossover, sans doublon."""
    return list(dict.fromkeys(p["path"] for p in engine.part_list(c) if not engine.est_invite(p["path"])))     # une série invitée n est pas dans Emby


import threading as _threading
_DEMARRAGE = _threading.Lock()       # un seul rangement ou annulation à la fois, même si on appuie deux fois très vite


@app.route("/api/crossovers/<cid>/apply", methods=["POST"])
def api_apply(cid):
    with _DEMARRAGE:
        return _api_apply(cid)


def _api_apply(cid):
    if engine.JOB.get("state") == "running":
        return jsonify({"ok": False, "error": "Un traitement est déjà en cours : attends qu'il se termine."}), 409
    with engine.LOCK:
        c = engine.find_crossover(cid)
        if not c:
            return jsonify({"ok": False, "error": "Crossover introuvable (la liste a peut-être changé : actualise)."}), 404
        plan = engine.make_plan(c)
        if plan["blockers"]:
            return jsonify({"ok": False, "error": "Rangement impossible : " + " ; ".join(plan["blockers"])}), 409
    engine.JOB.update(state="running", kind="apply", name=c["name"], total=len(plan["ops"]), done=0, sub_i=0, sub_n=1, ff=0, pct=0,
                      label="Préparation…", message="", emby="", ok=None)

    def travail():
        try:
            with engine.LOCK:
                res = engine.execute(c, plan, engine.load_state())
                logger.info("Crossover « %s » : %s", c["name"], "rangé" if res.get("ok") else "échec (%s)" % res.get("error"))
                engine.refresh()
            if res.get("ok"):
                engine.JOB.update(label="Actualisation d'Emby…", pct=100)
                res["emby"] = emby.actualiser(_dossiers(c))
                logger.info("%s", res["emby"])
            engine.JOB.update(state="fini", ok=bool(res.get("ok")), pct=100 if res.get("ok") else engine.JOB.get("pct", 0),
                              message=("Crossover organisé (%d actions)." % res.get("actions", 0)) if res.get("ok") else res.get("error", "Échec"),
                              emby=res.get("emby", ""))
        except Exception as ex:
            logger.exception("Rangement en erreur")
            engine.JOB.update(state="fini", ok=False, message=str(ex))

    import threading
    threading.Thread(target=travail, daemon=True).start()
    return jsonify({"ok": True, "started": True})


@app.route("/api/crossovers/<cid>/undo", methods=["POST"])
def api_undo(cid):
    with _DEMARRAGE:
        return _api_undo(cid)


def _api_undo(cid):
    if engine.JOB.get("state") == "running":
        return jsonify({"ok": False, "error": "Un traitement est déjà en cours : attends qu'il se termine."}), 409
    engine.JOB.update(state="running", kind="undo", name="", total=1, done=0, sub_i=0, sub_n=1, ff=0, pct=0,
                      label="Remise en état…", message="", emby="", ok=None)

    def travail():
        try:
            with engine.LOCK:
                c = engine.find_crossover(cid)
                st0 = engine.load_state()
                cles = [p["key"] for p in engine.part_list(c)] if c else st0.get("cles", {}).get(cid, [])
                res = engine.undo(cid, st0)
                logger.info("Annulation du crossover %s : %s", cid, "faite" if res.get("ok") else res.get("error"))
                if res.get("ok"):
                    import invites
                    invites.reactiver(cles)
                engine.refresh()
            if res.get("ok") and c:
                engine.JOB.update(label="Actualisation d'Emby…", pct=100)
                res["emby"] = emby.actualiser(_dossiers(c))
            engine.JOB.update(state="fini", ok=bool(res.get("ok")), pct=100, message="Traitement annulé." if res.get("ok") else res.get("error", "Échec"),
                              emby=res.get("emby", ""))
        except Exception as ex:
            logger.exception("Annulation en erreur")
            engine.JOB.update(state="fini", ok=False, message=str(ex))

    import threading
    threading.Thread(target=travail, daemon=True).start()
    return jsonify({"ok": True, "started": True})


@app.route("/api/job")
def api_job():
    return jsonify(dict(engine.JOB, pct=engine.job_pct() if engine.JOB.get("state") == "running" else engine.JOB.get("pct", 0)))


@app.route("/api/lookup")
def api_lookup():
    import invites
    terme = (request.args.get("term") or "").strip()
    if len(terme) < 2:
        return jsonify({"items": []})
    try:
        return jsonify({"items": invites.lookup(terme)})
    except Exception as ex:
        return jsonify({"items": [], "error": "Sonarr n'a pas répondu (%s)" % ex.__class__.__name__}), 502


@app.route("/api/crossovers/<cid>/suggestions")
def api_suggestions(cid):
    import invites
    with engine.LOCK:
        c = engine.find_crossover(cid)
    if not c:
        return jsonify({"items": []}), 404
    try:
        return jsonify({"items": invites.suggestions(c)})
    except Exception:
        return jsonify({"items": []})


@app.route("/api/crossovers/<cid>/invite", methods=["POST"])
def api_invite(cid):
    import invites
    body = request.get_json(silent=True) or {}
    try:
        tvdb, saison, episode = int(body.get("tvdbId")), int(body.get("season")), int(body.get("episode"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Choisis une série dans la liste et indique la saison et l'épisode."}), 400
    if saison < 1 or episode < 1:
        return jsonify({"ok": False, "error": "La saison et l'épisode doivent être des numéros à partir de 1."}), 400
    with engine.LOCK:
        c = engine.find_crossover(cid)
    if not c:
        return jsonify({"ok": False, "error": "Crossover introuvable : actualise la liste."}), 404
    res = invites.creer(c, tvdb, str(body.get("title", ""))[:120], saison, episode)
    return jsonify(res), (200 if res.get("ok") else 409)


@app.route("/api/episodes/<int:eid>/telecharger", methods=["POST"])
def api_telecharger(eid):
    """Épisode d'une série déjà dans Sonarr mais pas encore téléchargé : on le surveille et on lance la recherche."""
    try:
        ep = engine.sonarr("GET", "/episode/%d" % eid)
        if not ep:
            return jsonify({"ok": False, "error": "Épisode introuvable chez Sonarr."}), 404
        if not ep.get("monitored"):
            ep["monitored"] = True
            engine.sonarr("PUT", "/episode/%d" % eid, ep)
        engine.sonarr("POST", "/command", {"name": "EpisodeSearch", "episodeIds": [eid]})
        engine.noter_demande(eid)
    except Exception as ex:
        return jsonify({"ok": False, "error": "Sonarr ne répond pas : %s" % ex}), 502
    return jsonify({"ok": True})


@app.route("/api/fs/fichiers")
def api_fs_fichiers():
    """Explorateur de fichiers du serveur (lecture seule, sous /mnt) : dossiers et fichiers vidéo."""
    from pathlib import Path
    raw = request.args.get("path", "") or "/mnt"
    try:
        chemin = Path(raw).resolve()
    except (OSError, RuntimeError):
        return jsonify({"error": "Chemin invalide"}), 400
    if not (str(chemin) == "/mnt" or str(chemin).startswith("/mnt/")) or not chemin.is_dir():
        chemin = Path("/mnt")
    dossiers, fichiers = [], []
    try:
        for sub in sorted(chemin.iterdir(), key=lambda f: f.name.lower()):
            if sub.name.startswith((".", "@", "#")) or sub.name in ("lost+found", "$RECYCLE.BIN", "System Volume Information", "eaDir"):
                continue
            try:
                if sub.is_dir():
                    dossiers.append(sub.name)
                elif sub.suffix.lower() in (".mkv", ".mp4", ".avi", ".m4v"):
                    fichiers.append({"name": sub.name, "size": sub.stat().st_size})
            except OSError:
                pass
    except OSError as e:
        return jsonify({"path": str(chemin), "parent": str(chemin.parent) if str(chemin) != "/mnt" else "", "dirs": [], "files": [],
                        "note": "Dossier illisible : %s" % e.__class__.__name__})
    return jsonify({"path": str(chemin), "parent": str(chemin.parent) if str(chemin) != "/mnt" else "", "dirs": dossiers, "files": fichiers})


@app.route("/api/episodes/<int:eid>/importer", methods=["POST"])
def api_importer(eid):
    import invites
    chemin = str((request.get_json(silent=True) or {}).get("path", ""))
    try:
        nom = invites.importer(eid, chemin)
    except ValueError as ex:
        return jsonify({"ok": False, "error": str(ex)}), 400
    except Exception as ex:
        return jsonify({"ok": False, "error": "Import impossible : %s" % ex}), 502
    return jsonify({"ok": True, "nom": nom})


@app.route("/api/episodes/<int:eid>/sources")
def api_sources(eid):
    import invites
    try:
        return jsonify({"ok": True, "sources": invites.sources(eid)})
    except Exception as ex:
        return jsonify({"ok": False, "error": "Sonarr ne répond pas : %s" % ex}), 502


@app.route("/api/episodes/<int:eid>/source", methods=["POST"])
def api_source(eid):
    import invites
    body = request.get_json(silent=True) or {}
    try:
        invites.choisir_source(str(body.get("guid", "")), int(body.get("indexerId")))
        engine.noter_demande(eid)
    except Exception as ex:
        return jsonify({"ok": False, "error": "Sonarr n'a pas pu lancer cette source : %s" % ex}), 502
    return jsonify({"ok": True})


@app.route("/api/invites/deposer", methods=["POST"])
def api_deposer():
    import invites
    return jsonify({"ok": True, "pris": invites.deposer(maintenant=True), "dossier": engine.staging_dir()})


@app.route("/api/invites/<gid>/<action>", methods=["POST"])
def api_invite_action(gid, action):
    import invites
    if action == "retry":
        invites.relancer(gid)
    elif action == "cancel":
        invites.annuler(gid)
    else:
        return jsonify({"ok": False}), 404
    return jsonify({"ok": True})


@app.route("/api/series")
def api_series():
    """Séries de la bibliothèque (pour choisir à la main les parties d'un crossover)."""
    try:
        res = sorted(({"id": x["id"], "title": x["title"]} for x in engine.sonarr("GET", "/series")), key=lambda x: x["title"].lower())
    except Exception as ex:
        return jsonify({"error": "Sonarr ne répond pas : %s" % ex}), 502
    return jsonify({"series": res})


@app.route("/api/episode")
def api_episode():
    try:
        sid, sa, ep = int(request.args["sid"]), int(request.args["season"]), int(request.args["ep"])
    except (KeyError, ValueError):
        return jsonify({"ok": False}), 400
    e = next((x for x in engine.episodes_of(sid) if x["seasonNumber"] == sa and x["episodeNumber"] == ep), None)
    if not e:
        return jsonify({"ok": False, "error": "Cet épisode n'existe pas dans Sonarr."})
    return jsonify({"ok": True, "title": e.get("title") or "", "airDate": e.get("airDate") or "", "hasFile": bool(e.get("hasFile"))})


def _ligne_manuelle(x):
    try:
        sid, sa, ep = int(x["sid"]), int(x["season"]), int(x["ep"])
    except (KeyError, TypeError, ValueError):
        return None
    if sa < 0 or ep < 1:
        return None
    return {"sid": sid, "season": sa, "ep": ep, "nocopy": bool(x.get("nocopy"))}


@app.route("/api/manuels", methods=["POST"])
def api_manuel_creer():
    """Crossover décrit à la main : une liste de parties (dans l'ordre) et, au besoin, des séries qui le reçoivent sans en avoir la suite."""
    import uuid
    body = request.get_json(silent=True) or {}
    parts = [_ligne_manuelle(x) for x in (body.get("parts") or [])][:12]
    refs = [_ligne_manuelle(x) for x in (body.get("refs") or [])][:6]
    if not parts or None in parts or None in refs:
        return jsonify({"ok": False, "error": "Indique au moins une partie, avec série, saison et épisode."}), 400
    if len({x["sid"] for x in parts + refs}) < 2:
        return jsonify({"ok": False, "error": "Il faut au moins deux séries différentes (une partie dans une série, et soit une autre partie, soit une série qui reçoit l'épisode)."}), 400
    for x in parts + refs:
        if not any(e["seasonNumber"] == x["season"] and e["episodeNumber"] == x["ep"] for e in engine.episodes_of(x["sid"])):
            return jsonify({"ok": False, "error": "Un des épisodes n'existe pas dans Sonarr (S%02dE%02d)." % (x["season"], x["ep"])}), 400
    with engine.LOCK:
        st = engine.load_state()
        st.setdefault("manuels", []).append({"id": uuid.uuid4().hex[:8], "name": str(body.get("name", "")).strip()[:80], "parts": parts, "refs": refs})
        engine.save_state(st)
    engine.refresh()
    return jsonify({"ok": True})


@app.route("/api/manuels/<mid>", methods=["DELETE"])
def api_manuel_supprimer(mid):
    c = next((i for i in engine.CACHE.get("items", []) if i.get("manuel") == mid), None)
    if c and c.get("applied"):
        return jsonify({"ok": False, "error": "Ce crossover est déjà rangé : fais d'abord « Annuler le traitement »."}), 409
    with engine.LOCK:
        st = engine.load_state()
        st["manuels"] = [m for m in st.get("manuels", []) if m["id"] != mid]
        engine.save_state(st)
    engine.refresh()
    return jsonify({"ok": True})


@app.route("/api/crossovers/<cid>/fiches", methods=["POST"])
def api_fiches(cid):
    """Crée les fiches manquantes des épisodes d'origine (jamais d'écrasement), puis demande à Emby de les relire."""
    with engine.LOCK:
        c = engine.find_crossover(cid)
    if not c:
        return jsonify({"ok": False, "error": "Crossover introuvable : actualise la liste."}), 404
    try:
        faits = engine.creer_fiches_manquantes(c, emby)
    except Exception as ex:
        return jsonify({"ok": False, "error": "Impossible de créer la fiche : %s" % ex}), 500
    if faits:
        emby.actualiser(_dossiers(c))
    engine.refresh()
    return jsonify({"ok": True, "faits": faits})


@app.route("/api/parts/<cle>/saison", methods=["POST"])
def api_saison(cle):
    """Saison que la personne n'a plus : « garder » = ajouter quand même le crossover dans cette série ;
    « passer » = seulement dans les autres séries (l'épisode sert de source, puis il est retiré) ; vide = reposer la question."""
    import re as _re
    choix = (request.get_json(silent=True) or {}).get("choix")
    if not _re.match(r"^\d+-\d+-\d+$", cle) or choix not in ("garder", "passer", None, ""):
        return jsonify({"ok": False, "error": "Demande invalide."}), 400
    with engine.LOCK:
        st = engine.load_state()
        rangé = next((i for i in engine.CACHE.get("items", []) if i.get("applied") and any(p["key"] == cle for p in i["parts"])), None)
        if rangé:
            return jsonify({"ok": False, "error": "Ce crossover est déjà rangé : fais d'abord « Annuler le traitement » pour changer d'avis."}), 409
        if choix:
            st.setdefault("saisons", {})[cle] = choix
        else:
            st.setdefault("saisons", {}).pop(cle, None)
        engine.save_state(st)
        engine.refresh()
    return jsonify({"ok": True})


@app.route("/api/crossovers/<cid>/fix", methods=["POST"])
def api_fix(cid):
    """Correction à la main : ordre des parties et parties à retirer (ou retour à la détection automatique)."""
    body = request.get_json(silent=True) or {}
    with engine.LOCK:
        st = engine.load_state()
        if cid in st["done"]:
            return jsonify({"ok": False, "error": "Ce crossover est déjà rangé : annule d'abord le traitement pour le corriger."}), 409
        fixes = st.setdefault("fixes", {})
        if body.get("reset"):
            fixes.pop(cid, None)
        else:
            c = engine.find_crossover(cid)
            if not c:
                return jsonify({"ok": False, "error": "Crossover introuvable : actualise la liste."}), 404
            cles = {p["key"] for p in engine.part_list(c)} | set(fixes.get(cid, {}).get("exclude", []))
            order = [k for k in body.get("order", []) if k in cles]
            exclude = [k for k in body.get("exclude", []) if k in cles]
            restantes = {p["seriesId"] for p in engine.part_list(c) if p["key"] not in exclude}
            if len(restantes) < 2:
                return jsonify({"ok": False, "error": "Il doit rester des parties d'au moins 2 séries. Pour écarter tout le crossover, utilise « Mettre de côté »."}), 400
            nocopy = [k for k in body.get("nocopy", []) if k in cles and k not in exclude]
            fixes[cid] = {"order": order, "exclude": exclude, "nocopy": nocopy}
        engine.save_state(st)
        engine.refresh()
    return jsonify({"ok": True})


@app.route("/api/crossovers/<cid>/ignore", methods=["POST"])
def api_ignore(cid):
    ignorer = bool((request.get_json(silent=True) or {}).get("ignored", True))
    with engine.LOCK:
        st = engine.load_state()
        st["ignored"] = [i for i in st["ignored"] if i != cid] + ([cid] if ignorer else [])
        engine.save_state(st)
        engine.refresh()
    return jsonify({"ok": True})


@app.route("/api/wiki")
def api_wiki():
    return jsonify(dict(engine.WIKISTATE, pages=engine.WIKI.count(), db=engine.WIKI.stats()))


@app.route("/api/wiki/scan", methods=["POST"])
def api_wiki_scan():
    import threading
    if not engine.WIKISTATE["running"]:
        force = bool((request.get_json(silent=True) or {}).get("force"))
        threading.Thread(target=engine.wiki_scan, args=(force,), daemon=True).start()
    return jsonify({"ok": True})


def _diag_extra():
    lignes = ["", "--- Sonarr ---", f"Adresse : {os.getenv('SONARR_URL', '(non réglée)')}"]
    try:
        info = engine.sonarr("GET", "/system/status")
        lignes.append(f"Connexion : OK (Sonarr {info.get('version', '?')})")
    except Exception as e:
        lignes.append(f"Connexion : ÉCHEC ({e.__class__.__name__}: {e})")
    c = engine.CACHE
    par = {}
    for i in c["items"]:
        par[i["status"]] = par.get(i["status"], 0) + 1
    lignes += ["", "--- Crossovers ---", f"Dernière analyse : {c['updated'] or 'jamais'}",
               "Répartition : " + (", ".join(f"{k} {v}" for k, v in sorted(par.items())) or "aucun"),
               f"Erreur : {c['error'] or 'aucune'}"]
    w = engine.WIKISTATE
    lignes += ["", "--- Wikipédia ---",
               f"Analyse : {'en cours (%d/%d)' % (w['done'], w['total']) if w['running'] else 'terminée le ' + str(w['finished'])} · erreurs {w['errors']}",
               "Base : %(series)d séries, %(pages)d pages, %(claims)d renvois" % engine.WIKI.stats()]
    return lignes


diag.init_app(app, APP_VERSION, engine.series_dir, _diag_extra)
import settings_page
settings_page.init_app(app, BASE_DIR, lambda: APP_VERSION, engine)
import redemarrer
redemarrer.init_app(app, engine.occupe)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8002"))
    if not os.getenv("SONARR_KEY"):
        logger.warning("SONARR_KEY absente : ouvre ⚙️ Réglages pour connecter MouFlossover à Sonarr.")
    engine.start_background()
    print(f"MouFlossover {APP_VERSION} : http://0.0.0.0:{port}", file=sys.stderr)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
