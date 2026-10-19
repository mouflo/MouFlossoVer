"""
Alertes Telegram de MouFlossover.

Réglages lus à chaque envoi (donc pris en compte sans redémarrer) :
  TELEGRAM_BOT_TOKEN : le jeton du bot (créé avec @BotFather)
  TELEGRAM_CHAT_ID   : ton identifiant Telegram ou celui de ton groupe (l'appli sait le détecter)
  TELEGRAM_THREAD_ID : facultatif, le numéro du « sujet » d'un groupe Telegram à sujets (propre à MouFlossover)
Ils sont enregistrés dans data/secrets.env (jamais sur GitHub) depuis la page ⚙️ Réglages.
Si MouFlossover n'a pas les siens, il reprend (en lecture seule) ceux d'une autre appli du serveur sous /opt.
Le jeton n'apparaît jamais dans les journaux ni dans les réponses de l'appli.
"""
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests

logger = logging.getLogger("mouflossover.notifier")

_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,50}$")
_CHAT_RE = re.compile(r"^-?\d{3,20}$")
_THREAD_RE = re.compile(r"^\d{1,12}$")
API = "https://api.telegram.org"
RELAIS = []


def token_valide(token: str) -> bool:
    return bool(_TOKEN_RE.match(token or ""))


def chat_valide(chat_id: str) -> bool:
    return bool(_CHAT_RE.match(chat_id or ""))


def thread_valide(thread_id: str) -> bool:
    return bool(_THREAD_RE.match(thread_id or ""))


# ----------------------------------------------------------------------
# Reprise automatique des réglages Telegram des autres applis du serveur
# ----------------------------------------------------------------------
RACINE_APPLIS = Path("/opt")          # là où vivent MouFloster, MouFlanimeXer, MouFlopening…
_DOSSIER_ICI = Path(__file__).resolve().parent
_cache = {"quand": 0.0, "resultat": None, "details": []}
_IGNORER = {"venv", ".venv", "node_modules", ".git", "__pycache__", "site-packages", "icons", "docs", "ui",
            "templates", "tests", "covers", "cache", "logs"}
_SUFFIXES = {".env", ".json", ".ini", ".conf", ".cfg", ".yml", ".yaml", ".toml"}
_CLE_TELEGRAM = re.compile(r"telegram|(^|_)tg(_|$)|chat|bot", re.I)
_CLE_CHAT = re.compile(r"chat|((telegram|(^|_)tg)[a-z0-9_]*(id|user|dest))", re.I)
_SEPARATEUR = re.compile(r"\s*[=:]\s*")


def _paires_texte(fichier: Path):
    """Paires clé/valeur d'un fichier de type .env, .ini ou .yml (une par ligne)."""
    for ligne in fichier.read_text(encoding="utf-8", errors="ignore").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith(("#", ";", "//")):
            continue
        morceaux = _SEPARATEUR.split(ligne, maxsplit=1)
        if len(morceaux) == 2:
            yield morceaux[0].replace("export ", "").strip(" -\"'"), morceaux[1].strip().strip("\"',")


def _paires_json(fichier: Path):
    try:
        donnees = json.loads(fichier.read_text(encoding="utf-8", errors="ignore"))
    except (ValueError, OSError):
        return
    # On recolle le chemin complet : {"telegram": {"bot_token": …}} devient « telegram_bot_token »
    pile = [("", donnees)] if isinstance(donnees, dict) else []
    while pile:
        prefixe, d = pile.pop()
        for cle, val in d.items():
            nom = f"{prefixe}_{cle}" if prefixe else str(cle)
            if isinstance(val, dict):
                pile.append((nom, val))
            elif isinstance(val, (str, int)) and not isinstance(val, bool):
                yield nom, str(val)


def _fichiers_reglages(appli: Path):
    """Fichiers de réglages probables d'une appli (3 niveaux de dossiers au plus, taille raisonnable)."""
    n = 0
    for racine, dossiers, fichiers in os.walk(appli):
        profondeur = len(Path(racine).relative_to(appli).parts)
        dossiers[:] = [d for d in dossiers if d not in _IGNORER] if profondeur < 3 else []
        for nom in fichiers:
            p = Path(racine) / nom
            bas = nom.lower()
            if p.suffix.lower() in _SUFFIXES or bas.startswith(".env") or "secret" in bas:
                try:
                    if p.suffix.lower() != ".py" and p.stat().st_size <= 262144:
                        n += 1
                        if n > 200:
                            return
                        yield p
                except OSError:
                    continue


def _analyser():
    """Cherche les réglages Telegram d'une autre appli. Renvoie (résultat, détails lisibles).

    résultat = (jeton, identifiant ou "", nom de l'appli, fichier) ou None.
    Un jeton est reconnu à sa FORME (123456789:ABC…), quel que soit le nom de la clé.
    Les détails ne contiennent que des noms de fichiers et de clés, jamais de valeurs.
    """
    if time.time() - _cache["quand"] < 60 and _cache["details"]:
        return _cache["resultat"], _cache["details"]
    details, resultat = [], None
    try:
        applis = sorted(p for p in RACINE_APPLIS.glob("*") if p.is_dir() and p.resolve() != _DOSSIER_ICI)
    except OSError:
        applis = []
    details.append(f"Applis cherchées sous {RACINE_APPLIS} : " + (", ".join(a.name for a in applis) or "aucune"))
    for appli in applis:
        analyses, cles_vues = 0, set()
        for f in _fichiers_reglages(appli):
            try:
                paires = list(_paires_json(f) if f.suffix.lower() == ".json" else _paires_texte(f))
            except OSError:
                continue
            analyses += 1
            jeton = next((v for _, v in paires if token_valide(v)), "")
            chat = next((v for k, v in paires if _CLE_CHAT.search(k) and chat_valide(v) and not token_valide(v)), "")
            cles_vues.update(k for k, _ in paires if _CLE_TELEGRAM.search(k))
            if jeton and not resultat:
                resultat = (jeton, chat, appli.name, str(f.relative_to(appli)))
                details.append(f"  ✅ {appli.name}/{f.relative_to(appli)} : jeton trouvé ({'…' + jeton[-4:]}), "
                               f"identifiant {'trouvé' if chat else 'NON trouvé (détection automatique à l’envoi)'}")
        details.append(f"  {appli.name} : {analyses} fichier(s) de réglages lu(s)"
                       + (f" ; clés évoquant Telegram : {', '.join(sorted(cles_vues)[:12])}" if cles_vues else " ; aucune clé évoquant Telegram"))
    if not resultat:
        details.append("  ❌ Aucun jeton Telegram trouvé dans les fichiers .env/.json/.ini/.yml des autres applis")
    _cache.update(quand=time.time(), resultat=resultat, details=details)
    return resultat, details


def diagnostic() -> list[str]:
    """Lignes pour le rapport de l'appli (aucune valeur secrète)."""
    perso = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    lignes = [f"Telegram : réglages propres à MouFlossover : {'oui' if perso else 'non'}"]
    lignes += _analyser()[1]
    return lignes


def _chercher_ailleurs():
    r = _analyser()[0]
    return r[:3] if r else None


def vider_cache():
    _cache.update(quand=0.0, resultat=None, details=[])


def source_reprise() -> str:
    """Nom de l'appli dont on reprend les réglages (vide si MouFlossover a les siens)."""
    if os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip():
        return ""
    t = _chercher_ailleurs()
    return t[2] if t else ""


def _token() -> str:
    perso = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if perso:
        return perso
    t = _chercher_ailleurs()
    return t[0] if t else ""


def _chat() -> str:
    perso = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if perso:
        return perso
    t = _chercher_ailleurs()
    return t[1] if t else ""


def _thread() -> str:
    """Sujet du groupe (propre à MouFlossover, jamais repris d'une autre appli : chaque appli a le sien)."""
    t = os.getenv("TELEGRAM_THREAD_ID", "").strip()
    return t if thread_valide(t) else ""


def configure() -> bool:
    """Prêt à envoyer : un jeton suffit (l'identifiant se détecte tout seul s'il manque)."""
    return bool(_token())


def indice_token() -> str:
    """Ce qu'on peut afficher du jeton : seulement les 4 derniers caractères."""
    t = _token()
    return ("…" + t[-4:]) if t else ""


def _propre(texte: str, token: str) -> str:
    """Retire le jeton d'un message d'erreur (les erreurs réseau contiennent l'adresse complète)."""
    return str(texte).replace(token, "***") if token else str(texte)


def _en_test() -> bool:
    """Lancé par unittest / pytest : aucun message ne doit partir pour de vrai (les tests utilisent de vrais réglages)."""
    a0 = (sys.argv[0] if sys.argv else "") or ""
    return "unittest" in a0 or "pytest" in a0


def _appel(token: str, methode: str, donnees: dict | None = None, timeout: int = 15) -> tuple[bool, dict | str]:
    if _en_test():
        return False, "Tests en cours : aucun message envoyé."
    try:
        r = requests.post(f"{API}/bot{token}/{methode}", json=donnees or {}, timeout=timeout)
        try:
            corps = r.json()
        except ValueError:
            return False, f"Réponse inattendue de Telegram (code {r.status_code})"
        if r.ok and corps.get("ok"):
            return True, corps
        return False, corps.get("description") or f"Erreur Telegram (code {r.status_code})"
    except requests.RequestException as e:
        return False, "Telegram injoignable : " + _propre(e.__class__.__name__ + " " + str(e), token)[:150]


def envoyer(texte: str, token: str | None = None, chat_id: str | None = None,
            thread_id: str | None = None, html: bool = False) -> tuple[bool, str]:
    """Envoie un message. Renvoie (réussi, message lisible).
    thread_id : None = celui des réglages ; "" = aucun sujet ; sinon le numéro du sujet.
    Chaque alerte part aussi sur les téléphones abonnés (RELAIS : notifications web), Telegram gardant l'historique."""
    for relais in ([] if _en_test() else RELAIS):
        try:
            relais(texte)
        except Exception as e:
            logger.info("Relais de notification : %s", e.__class__.__name__)
    token = token or _token()
    chat_id = chat_id or _chat()
    thread_id = _thread() if thread_id is None else thread_id
    if token and not chat_id:
        # Jeton connu mais pas d'identifiant : on le déduit du dernier message reçu par le bot
        ok, trouve = detecter_chat(token)
        if ok:
            chat_id = trouve
            logger.info("Identifiant Telegram détecté automatiquement")
        else:
            return False, "Identifiant Telegram introuvable : " + trouve
    if not token:
        return False, "Aucun jeton Telegram : MouFlossover n'en a pas et n'en a trouvé chez aucune autre appli (voir le rapport, section Telegram)."
    donnees = {"chat_id": chat_id, "text": texte, "disable_web_page_preview": True}
    if html:
        donnees["parse_mode"] = "HTML"
    if thread_valide(thread_id) and int(thread_id) != 1:        # 1 = sujet « Général » : Telegram veut qu'on ne précise rien
        donnees["message_thread_id"] = int(thread_id)
    ok, rep = _appel(token, "sendMessage", donnees)
    if ok:
        return True, "Message envoyé."
    logger.warning("Envoi Telegram échoué : %s", _propre(rep, token))
    return False, str(rep)


def detecter_chat(token: str) -> tuple[bool, str]:
    """Trouve ton identifiant à partir du dernier message que tu as envoyé au bot."""
    ok, rep = _appel(token, "getUpdates", {"limit": 20, "timeout": 0})
    if not ok:
        return False, _sans_webhook(rep)
    for m in reversed(rep.get("result", [])):
        msg = m.get("message") or m.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id"):
            return True, str(chat["id"])
    return False, "Aucun message reçu : ouvre ton bot dans Telegram, envoie-lui « bonjour », puis réessaie."


WEBHOOK = ("Ce bot est déjà branché sur une autre application (par exemple Jeedom) : Telegram lui envoie directement "
           "les messages, l'appli ne peut donc pas les lire pour détecter quoi que ce soit. Utilise plutôt « Lien d'un message » "
           "(appui long sur un message du sujet → Copier le lien), ou crée un bot réservé à tes applis avec @BotFather.")


def _sans_webhook(rep) -> str:
    texte = str(rep)
    return WEBHOOK if "webhook" in texte.lower() else texte


def lire_lien(lien: str) -> tuple[bool, str, str, str]:
    """Lien d'un message de groupe privé (appui long → « Copier le lien ») → (réussi, identifiant du groupe, sujet, erreur).
    https://t.me/c/1234567890/45/678 : groupe -1001234567890, sujet 45 ; https://t.me/c/1234567890/5 : lien du sujet 5 (1 = « Général »)."""
    m = re.search(r"t\.me/c/(\d{5,})/(\d+)(?:/(\d+))?", lien or "")
    if not m:
        return False, "", "", ("Ce lien ne ressemble pas à un lien de message de groupe (https://t.me/c/…). "
                               "Dans le sujet, appui long sur un message → « Copier le lien ».")
    groupe = "-100" + m.group(1)
    sujet = m.group(2)                 # …/<groupe>/<sujet> (lien du sujet) ou …/<groupe>/<sujet>/<message>
    return True, groupe, sujet, ""


def detecter_groupe(token: str) -> tuple[bool, str, str, str]:
    """Cherche un groupe à sujets : écris un message dans le sujet de MouFlossover, puis clique sur « Détecter ».
    Renvoie (réussi, identifiant du groupe, numéro du sujet, message d'erreur)."""
    ok, rep = _appel(token, "getUpdates", {"limit": 50, "timeout": 0})
    if not ok:
        return False, "", "", _sans_webhook(rep)
    for m in reversed(rep.get("result", [])):
        msg = m.get("message") or m.get("channel_post") or {}
        chat = msg.get("chat") or {}
        sujet = msg.get("message_thread_id")
        if chat.get("type") == "supergroup" and chat.get("id") and sujet and msg.get("is_topic_message"):
            return True, str(chat["id"]), str(sujet), ""
    return False, "", "", ("Aucun message de sujet reçu : dans ton groupe, ouvre le sujet de cette appli, écris « bonjour », "
                            "vérifie que le bot est administrateur du groupe, puis réessaie.")
