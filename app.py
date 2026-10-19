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
    return list(dict.fromkeys(p["path"] for p in engine.part_list(c)))


@app.route("/api/crossovers/<cid>/apply", methods=["POST"])
def api_apply(cid):
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
    if engine.JOB.get("state") == "running":
        return jsonify({"ok": False, "error": "Un traitement est déjà en cours : attends qu'il se termine."}), 409
    engine.JOB.update(state="running", kind="undo", name="", total=1, done=0, sub_i=0, sub_n=1, ff=0, pct=0,
                      label="Remise en état…", message="", emby="", ok=None)

    def travail():
        try:
            with engine.LOCK:
                c = engine.find_crossover(cid)
                res = engine.undo(cid, engine.load_state())
                logger.info("Annulation du crossover %s : %s", cid, "faite" if res.get("ok") else res.get("error"))
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
            fixes[cid] = {"order": order, "exclude": exclude}
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
