"""
Page « ⚙️ Réglages » de MouFlossover (même style que les autres applis) : connexion à Sonarr et dossier des séries.
Les réglages sont enregistrés dans data/secrets.env (jamais sur GitHub) et pris en compte tout de suite.
"""
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from flask import jsonify, render_template, request

from secrets_store import write_secret

logger = logging.getLogger(__name__)
_BAD_CHARS = set('"$`\\\n\r')     # interdits : ils casseraient le fichier data/secrets.env


def _tester_sonarr(url, cle):
    """(ok, message) : interroge Sonarr avec l'adresse et la clé données."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/v3/system/status", headers={"X-Api-Key": cle})
        info = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return True, "Connecté à Sonarr %s." % info.get("version", "")
    except urllib.error.HTTPError as e:
        return False, "Sonarr a refusé la clé (erreur %s)." % e.code if e.code in (401, 403) else "Sonarr a répondu avec l'erreur %s." % e.code
    except Exception as e:
        return False, "Sonarr ne répond pas à cette adresse (%s)." % e.__class__.__name__


def init_app(app, base_dir, version_fn, engine):
    base_dir = Path(base_dir)
    secrets = base_dir / "data" / "secrets.env"
    import fs_browser
    fs_browser.init_app(app)

    @app.route("/reglages")
    def settings_page():
        return render_template("reglages.html", version=version_fn())

    @app.route("/api/settings/sonarr")
    def sonarr_state():
        cle = os.getenv("SONARR_KEY", "")
        return jsonify({"url": os.getenv("SONARR_URL", ""), "configured": bool(cle),
                        "hint": ("…" + cle[-4:]) if len(cle) >= 8 else ""})

    @app.route("/api/settings/sonarr", methods=["POST"])
    def sonarr_save():
        body = request.get_json(silent=True) or {}
        url = str(body.get("url", "")).strip().rstrip("/")
        cle = str(body.get("api_key", "")).strip() or os.getenv("SONARR_KEY", "")
        if not url.startswith(("http://", "https://")) or _BAD_CHARS & set(url) or " " in url:
            return jsonify({"ok": False, "error": "L'adresse doit commencer par http:// ou https:// (ex. http://192.168.1.133:8989/sonarr)."}), 400
        if _BAD_CHARS & set(cle) or " " in cle:
            return jsonify({"ok": False, "error": "La clé contient un caractère interdit (espace, guillemet, $ ou \\)."}), 400
        if not cle:
            return jsonify({"ok": False, "error": "Colle la clé API de Sonarr (Réglages → Général dans Sonarr)."}), 400
        ok, msg = _tester_sonarr(url, cle)
        if body.get("test"):
            return jsonify({"ok": ok, "message": msg if ok else "", "error": "" if ok else msg}), (200 if ok else 400)
        if not ok:
            return jsonify({"ok": False, "error": msg + " Rien n'a été enregistré."}), 400
        write_secret(secrets, "SONARR_URL", url)
        write_secret(secrets, "SONARR_KEY", cle)
        os.environ["SONARR_URL"], os.environ["SONARR_KEY"] = url, cle
        logger.info("Connexion à Sonarr mise à jour depuis la page web : %s", url)
        threading.Thread(target=engine.refresh, daemon=True).start()
        return jsonify({"ok": True, "message": msg + " Enregistré, l'état des crossovers se recalcule."})

    @app.route("/api/settings/emby")
    def emby_state():
        cle = os.getenv("EMBY_API_KEY", "")
        return jsonify({"url": os.getenv("EMBY_URL", ""), "configured": bool(cle),
                        "hint": ("…" + cle[-4:]) if len(cle) >= 8 else ""})

    @app.route("/api/settings/emby", methods=["POST"])
    def emby_save():
        import emby
        body = request.get_json(silent=True) or {}
        url = str(body.get("url", "")).strip().rstrip("/")
        cle = str(body.get("api_key", "")).strip() or os.getenv("EMBY_API_KEY", "")
        if not url.startswith(("http://", "https://")) or _BAD_CHARS & set(url) or " " in url:
            return jsonify({"ok": False, "error": "L'adresse doit commencer par http:// ou https:// (ex. http://192.168.1.134:8096)."}), 400
        if not cle or _BAD_CHARS & set(cle) or " " in cle:
            return jsonify({"ok": False, "error": "Colle une clé API Emby valide (Emby → Tableau de bord → Clés API)."}), 400
        ok, msg = emby.tester(url, cle)
        if body.get("test"):
            return jsonify({"ok": ok, "message": msg if ok else "", "error": "" if ok else msg}), (200 if ok else 400)
        if not ok:
            return jsonify({"ok": False, "error": msg + " Rien n'a été enregistré."}), 400
        write_secret(secrets, "EMBY_URL", url)
        write_secret(secrets, "EMBY_API_KEY", cle)
        os.environ["EMBY_URL"], os.environ["EMBY_API_KEY"] = url, cle
        return jsonify({"ok": True, "message": msg + " Enregistré."})

    @app.route("/api/settings/alertes")
    def alertes_state():
        import alerts, notifier
        return jsonify({"actif": alerts.actif(), "configure": notifier.configure(), "source": notifier.source_reprise(),
                        "sujet": bool(notifier._thread()), "url": alerts.lien()})

    @app.route("/api/settings/alertes", methods=["POST"])
    def alertes_save():
        import alerts, notifier
        body = request.get_json(silent=True) or {}
        if body.get("test"):
            ok, msg = alerts.test()
            return jsonify({"ok": ok, "message": "Message envoyé : regarde ton Telegram." if ok else "", "error": "" if ok else msg}), (200 if ok else 400)
        url = str(body.get("url", "")).strip().rstrip("/")
        if url and (not url.startswith(("http://", "https://")) or _BAD_CHARS & set(url) or " " in url):
            return jsonify({"ok": False, "error": "L'adresse doit commencer par http:// ou https:// (ex. https://mouflossover.mon-domaine.fr)."}), 400
        alerts.regler(bool(body.get("actif", True)))
        write_secret(secrets, "APP_URL", url)
        os.environ["APP_URL"] = url
        return jsonify({"ok": True, "message": "Enregistré."})

    @app.route("/api/settings/paths")
    def paths_state():
        d = engine.series_dir()
        return jsonify({"series": d, "series_ok": Path(d).is_dir(), "writable": Path(d).is_dir() and os.access(d, os.W_OK)})

    @app.route("/api/settings/paths", methods=["POST"])
    def paths_save():
        body = request.get_json(silent=True) or {}
        d = str(body.get("series", "")).strip().rstrip("/")
        if not d.startswith("/") or _BAD_CHARS & set(d):
            return jsonify({"ok": False, "error": f"Chemin invalide : « {d} » (chemin complet commençant par /, sans guillemets ni $)."}), 400
        if not Path(d).is_dir():
            return jsonify({"ok": False, "error": f"Dossier introuvable sur le serveur : « {d} » (le partage est-il monté ?). Rien n'a été enregistré."}), 400
        write_secret(secrets, "SERIES_DIR", d)
        os.environ["SERIES_DIR"] = d
        logger.info("Dossier des séries mis à jour depuis la page web : %s", d)
        threading.Thread(target=engine.refresh, daemon=True).start()
        return jsonify({"ok": True, "message": "Enregistré. L'état des crossovers se recalcule."})
