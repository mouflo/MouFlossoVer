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


_SPECIAL = re.compile(r"\.S00E(\d+)\.part(\d+)\(")


def ordonner(chemins_series):
    """Impose l'ordre des parties dans Emby pour les spéciaux d'un même endroit (titre de tri « 01 … », « 02 … »,
    verrouillé pour qu'Emby ne le remplace pas). Renvoie le nombre d'épisodes mis à jour ; ne lève jamais d'erreur."""
    if not configured():
        return 0
    n = 0
    try:
        uid = next(u["Id"] for u in _call("GET", "/Users") if u.get("Policy", {}).get("IsAdministrator"))
        for dossier in chemins_series:
            for dp, dn, fn in os.walk(dossier):
                dn[:] = [d for d in dn if d != "@eaDir"]
                for f in fn:
                    m = _SPECIAL.search(f)
                    if not m or not f.endswith(".nfo") or int(m.group(1)) < 100:
                        continue
                    with open(os.path.join(dp, f), encoding="utf-8-sig", errors="replace") as fh:
                        t = fh.read()
                    tv = re.search(r'<uniqueid type="tvdb"[^>]*>(\d+)</uniqueid>', t)
                    mt = re.search(r"<title>(.*?)</title>", t, re.S)
                    if not tv or not mt:
                        continue
                    veut = "%02d %s" % (int(m.group(2)), mt.group(1).strip().replace("&amp;", "&"))
                    r = _call("GET", "/Items", {"Recursive": "true", "IncludeItemTypes": "Episode", "AnyProviderIdEquals": "tvdb." + tv.group(1), "Fields": "Path"})
                    for it in r.get("Items", []):
                        if not os.path.basename(it.get("Path", "")).startswith(f[:-4]):
                            continue
                        full = _call("GET", "/Users/%s/Items/%s" % (uid, it["Id"]))
                        # Garde-fou : enregistrer un épisode fait réécrire sa fiche NFO d'après ce qu'Emby a en mémoire. Si Emby a
                        # lu le fichier avant que la fiche soit prête (titre = nom du fichier, résumé vide), on n'y touche pas.
                        titre_nfo = mt.group(1).strip().replace("&amp;", "&")
                        plot = re.search(r"<plot>(.*?)</plot>", t, re.S)
                        if (full.get("Name") or "").strip() != titre_nfo or (plot and plot.group(1).strip() and not (full.get("Overview") or "").strip()):
                            continue
                        if full.get("SortName") == veut and "SortName" in (full.get("LockedFields") or []):
                            continue
                        # la place du spécial (juste avant tel épisode) : si Emby ne l'a pas en mémoire, il l'effacerait de la fiche
                        for cs, ce in (("airsbefore_season", "airsbefore_episode"), ("displayseason", "displayepisode")):
                            ms, me = re.search(r"<%s>(\d+)</%s>" % (cs, cs), t), re.search(r"<%s>(\d+)</%s>" % (ce, ce), t)
                            if ms and me:
                                full["AirsBeforeSeasonNumber"], full["AirsBeforeEpisodeNumber"] = int(ms.group(1)), int(me.group(1))
                                break
                        full["ForcedSortName"] = full["SortName"] = veut
                        full["LockedFields"] = sorted(set((full.get("LockedFields") or []) + ["SortName"]))
                        req = urllib.request.Request(os.getenv("EMBY_URL").strip().rstrip("/") + "/Items/" + it["Id"], method="POST",
                                                     data=json.dumps(full).encode(),
                                                     headers={"X-Emby-Token": os.getenv("EMBY_API_KEY").strip(), "Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=TIMEOUT).read()
                        n += 1
    except Exception:
        pass
    return n


def ordonner_plus_tard(chemins_series, delais=(40, 150, 400)):
    """Les nouveaux fichiers n'apparaissent dans Emby qu'après son scan : on repasse quelques fois."""
    def boucle():
        for d in delais:
            time.sleep(d)
            ordonner(chemins_series)
    threading.Thread(target=boucle, daemon=True).start()
