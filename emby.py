"""Actualisation ciblée d'Emby après un rangement : seules les séries touchées sont relues, pas toute la médiathèque.
Réglages (data/secrets.env, jamais sur GitHub) : EMBY_URL et EMBY_API_KEY.
"""
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 15


def configured():
    return bool(os.getenv("EMBY_URL", "").strip() and os.getenv("EMBY_API_KEY", "").strip())


def _call(method, path, params=None, url=None, cle=None):
    base = (url or os.getenv("EMBY_URL", "")).strip().rstrip("/")
    cle = cle or os.getenv("EMBY_API_KEY", "").strip()
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(base + path + q, method=method, headers={"X-Emby-Token": cle, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read()
    return json.loads(body) if body else {}


def tester(url, cle):
    try:
        info = _call("GET", "/System/Info/Public", url=url, cle=cle)
        _call("GET", "/Users", url=url, cle=cle)          # la clé est-elle acceptée ?
        return True, "Connecté à Emby %s." % info.get("Version", "")
    except urllib.error.HTTPError as e:
        return False, "Emby a refusé la clé (erreur %s)." % e.code if e.code in (401, 403) else "Emby a répondu avec l'erreur %s." % e.code
    except Exception as e:
        return False, "Emby ne répond pas à cette adresse (%s)." % e.__class__.__name__


def _fold(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def actualiser(chemins_series):
    """Demande à Emby de relire les dossiers de ces séries. Ne lève jamais d'erreur : renvoie un texte pour l'écran."""
    if not configured():
        return "Emby n'est pas relié à MouFlossover (⚙️ Réglages → Connexions) : actualise la bibliothèque à la main."
    try:
        items = _call("GET", "/Items", {"IncludeItemTypes": "Series", "Recursive": "true", "Fields": "Path"}).get("Items", [])
        par_dossier = {_fold(os.path.basename((i.get("Path") or "").rstrip("/\\"))): i for i in items if i.get("Path")}
        faits, absents = [], []
        for chemin in chemins_series:
            dossier = os.path.basename(chemin.rstrip("/"))
            it = par_dossier.get(_fold(dossier))
            if not it:
                absents.append(dossier)
                continue
            # « ValidationOnly » : Emby repère seulement les fichiers nouveaux ou modifiés. Surtout PAS « FullRefresh » :
            # il réécrit alors les fiches NFO de toute la série (et peut remplacer des titres par des noms de fichiers).
            _call("POST", "/Items/%s/Refresh" % it["Id"], {"Recursive": "true", "MetadataRefreshMode": "ValidationOnly",
                                                           "ImageRefreshMode": "ValidationOnly", "ReplaceAllMetadata": "false",
                                                           "ReplaceAllImages": "false"})
            faits.append(it.get("Name") or dossier)
        msg = "Emby relit : " + ", ".join(faits) + "." if faits else "Emby : aucune série retrouvée."
        if absents:
            msg += " Introuvable dans Emby : " + ", ".join(absents) + "."
        return msg
    except Exception as e:
        return "Emby n'a pas pu être actualisé (%s). Actualise la bibliothèque à la main." % e.__class__.__name__
