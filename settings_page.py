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

    def _enreg(cle, valeur):
        write_secret(secrets, cle, valeur)
        os.environ[cle] = valeur

    @app.route("/api/settings/telegram")
    def telegram_state():
        import alerts, notifier
        return jsonify({
            "configured": notifier.configure(),
            "token_hint": notifier.indice_token(),      # jamais le jeton complet
            "chat_id": os.getenv("TELEGRAM_CHAT_ID", "") or notifier._chat(),
            "thread_id": notifier._thread(),
            "app_url": os.getenv("APP_URL", ""),
            "app_url_local": os.getenv("APP_URL_LOCAL", ""),
            "actif": alerts.actif(),
            "source": notifier.source_reprise(),     # nom de l'autre appli dont les réglages sont repris
            "diagnostic": notifier.diagnostic(),     # où l'appli a cherché (noms seulement, aucune valeur secrète)
        })

    @app.route("/api/settings/telegram", methods=["POST"])
    def telegram_save():
        import alerts, notifier
        body = request.get_json(silent=True) or {}
        action = body.get("action", "save")
        token_saisi = str(body.get("token", "")).strip()
        chat_id = str(body.get("chat_id", "")).strip()
        thread_id = str(body.get("thread_id", "")).strip()
        app_url = str(body.get("app_url", "")).strip().rstrip("/")
        app_url_local = str(body.get("app_url_local", "")).strip().rstrip("/")
        token = token_saisi or notifier._token()

        for valeur in (token_saisi, chat_id, thread_id, app_url, app_url_local):
            if _BAD_CHARS & set(valeur) or " " in valeur:
                return jsonify({"ok": False, "error": "Caractère interdit (espace, guillemet, $ ou \\) dans un champ."}), 400
        if token_saisi and not notifier.token_valide(token_saisi):
            return jsonify({"ok": False, "error": "Ce jeton n'a pas la bonne forme (il ressemble à 123456789:ABC…, donné par @BotFather)."}), 400

        if action == "lien":
            ok, chat, sujet, erreur = notifier.lire_lien(str(body.get("lien", "")))
            return jsonify({"ok": ok, "chat_id": chat, "thread_id": sujet, "error": erreur,
                            "message": "Groupe et sujet lus dans le lien." if ok else ""}), (200 if ok else 400)

        if action == "detect_groupe":
            if not token:
                return jsonify({"ok": False, "error": "Colle d'abord le jeton du bot."}), 400
            ok, chat, sujet, erreur = notifier.detecter_groupe(token)
            return jsonify({"ok": ok, "chat_id": chat, "thread_id": sujet, "error": erreur,
                            "message": "Groupe et sujet trouvés." if ok else ""}), (200 if ok else 400)

        if action == "detect":
            if not token:
                return jsonify({"ok": False, "error": "Colle d'abord le jeton du bot."}), 400
            ok, rep = notifier.detecter_chat(token)
            return jsonify({"ok": ok, "chat_id": rep if ok else "", "error": "" if ok else rep,
                            "message": "Identifiant trouvé." if ok else ""}), (200 if ok else 400)

        if chat_id and not notifier.chat_valide(chat_id):
            return jsonify({"ok": False, "error": "L'identifiant Telegram est un nombre (ex. 123456789)."}), 400
        if thread_id and not notifier.thread_valide(thread_id):
            return jsonify({"ok": False, "error": "Le numéro du sujet est un nombre (ex. 4). Laisse vide si tu n'utilises pas de sujets."}), 400
        if any(u and not u.startswith(("http://", "https://")) for u in (app_url, app_url_local)):
            return jsonify({"ok": False, "error": "L'adresse de l'appli doit commencer par http:// ou https://"}), 400

        if action == "test":
            ok, msg = alerts.test(token or None, chat_id or None, thread_id)
            logger.info("Test Telegram : %s", "réussi" if ok else "échoué (%s)" % msg)
            return jsonify({"ok": ok, "message": "Message de test envoyé : regarde ton Telegram." if ok else "", "error": "" if ok else msg}), (200 if ok else 400)

        if token_saisi:
            _enreg("TELEGRAM_BOT_TOKEN", token_saisi)
        if chat_id:
            _enreg("TELEGRAM_CHAT_ID", chat_id)
        _enreg("TELEGRAM_THREAD_ID", thread_id)
        _enreg("APP_URL", app_url)
        _enreg("APP_URL_LOCAL", app_url_local)
        alerts.regler(bool(body.get("actif", True)))
        notifier.vider_cache()
        logger.info("Réglages Telegram mis à jour depuis la page web")
        return jsonify({"ok": True, "message": "Enregistré. Les notifications sont actives tout de suite."})

    @app.route("/api/settings/prowlarr")
    def prowlarr_state():
        cle = os.getenv("PROWLARR_KEY", "")
        return jsonify({"url": os.getenv("PROWLARR_URL", ""), "configured": bool(cle), "hint": ("…" + cle[-4:]) if len(cle) >= 8 else ""})

    @app.route("/api/settings/prowlarr", methods=["POST"])
    def prowlarr_save():
        body = request.get_json(silent=True) or {}
        url = str(body.get("url", "")).strip().rstrip("/")
        cle = str(body.get("api_key", "")).strip() or os.getenv("PROWLARR_KEY", "")
        if not url.startswith(("http://", "https://")) or _BAD_CHARS & set(url) or " " in url:
            return jsonify({"ok": False, "error": "L'adresse doit commencer par http:// ou https:// (ex. http://192.168.1.133:9696)."}), 400
        if not cle or _BAD_CHARS & set(cle) or " " in cle:
            return jsonify({"ok": False, "error": "Colle une clé API Prowlarr valide (Prowlarr → Réglages → Général → Clé API)."}), 400
        try:
            req = urllib.request.Request(url + "/api/v1/system/status", headers={"X-Api-Key": cle})
            info = json.loads(urllib.request.urlopen(req, timeout=10).read())
            ok, msg = True, "Connecté à Prowlarr %s." % info.get("version", "")
        except urllib.error.HTTPError as e:
            ok, msg = False, ("Prowlarr a refusé la clé (erreur %s)." % e.code) if e.code in (401, 403) else "Prowlarr a répondu avec l'erreur %s." % e.code
        except Exception as e:
            ok, msg = False, "Prowlarr ne répond pas à cette adresse (%s)." % e.__class__.__name__
        if body.get("test"):
            return jsonify({"ok": ok, "message": msg if ok else "", "error": "" if ok else msg}), (200 if ok else 400)
        if not ok:
            return jsonify({"ok": False, "error": msg + " Rien n'a été enregistré."}), 400
        _enreg("PROWLARR_URL", url)
        _enreg("PROWLARR_KEY", cle)
        return jsonify({"ok": True, "message": msg + " Enregistré."})

    @app.route("/api/settings/paths")
    def paths_state():
        d = engine.series_dir()
        p = engine.staging_dir()
        return jsonify({"series": d, "series_ok": Path(d).is_dir(), "writable": Path(d).is_dir() and os.access(d, os.W_OK),
                        "passage": p, "passage_ok": Path(p).is_dir(), "passage_writable": Path(p).is_dir() and os.access(p, os.W_OK)})

    @app.route("/api/settings/paths", methods=["POST"])
    def paths_save():
        body = request.get_json(silent=True) or {}
        d = str(body.get("series", "")).strip().rstrip("/")
        if not d.startswith("/") or _BAD_CHARS & set(d):
            return jsonify({"ok": False, "error": f"Chemin invalide : « {d} » (chemin complet commençant par /, sans guillemets ni $)."}), 400
        if not Path(d).is_dir():
            return jsonify({"ok": False, "error": f"Dossier introuvable sur le serveur : « {d} » (le partage est-il monté ?). Rien n'a été enregistré."}), 400
        passage = str(body.get("passage", "")).strip().rstrip("/")
        ancien_passage = engine.staging_dir()
        if passage and passage != ancien_passage:
            if not passage.startswith("/") or _BAD_CHARS & set(passage) or passage.startswith(d.rstrip("/") + "/") or passage == d:
                return jsonify({"ok": False, "error": "Le dossier de passage doit être un chemin complet, en dehors du dossier des séries (sinon Emby le verrait). Rien n'a été enregistré."}), 400
            try:
                os.makedirs(passage, exist_ok=True)
            except OSError as e:
                return jsonify({"ok": False, "error": "Impossible de créer ou d'écrire ce dossier de passage (%s). Rien n'a été enregistré." % e.__class__.__name__}), 400
            if not os.access(passage, os.W_OK):
                return jsonify({"ok": False, "error": "Ce dossier de passage est en lecture seule. Rien n'a été enregistré."}), 400
        write_secret(secrets, "SERIES_DIR", d)
        os.environ["SERIES_DIR"] = d
        msg = "Enregistré. L'état des crossovers se recalcule."
        if passage and passage != ancien_passage:
            write_secret(secrets, "STAGING_DIR", passage)
            os.environ["STAGING_DIR"] = passage
            import invites
            threading.Thread(target=invites.deplacer_passage, args=(ancien_passage, passage), daemon=True).start()
            msg += " Le dossier de passage est déplacé : Sonarr transfère les épisodes invités qu'il contient (quelques minutes)."
        logger.info("Dossiers mis à jour depuis la page web : %s · passage %s", d, passage or ancien_passage)
        threading.Thread(target=engine.refresh, daemon=True).start()
        return jsonify({"ok": True, "message": msg})
