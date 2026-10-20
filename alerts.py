"""Notifications Telegram : prévient quand un crossover est détecté, ou quand il devient prêt à être rangé
(par exemple parce que l'épisode qui manquait vient d'être téléchargé).

Au tout premier passage, on mémorise l'existant sans rien envoyer (sinon vous recevriez un message pour chaque
crossover déjà connu). Ensuite, seuls les changements sont annoncés, regroupés en un seul message.
Les réglages Telegram (jeton, groupe, sujet) viennent de data/secrets.env ou, pour le jeton, d'une autre appli.
"""
import datetime
import html
import logging
import os
import threading

import engine
import notifier

logger = logging.getLogger(__name__)
MAX_PAR_MESSAGE = 6
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def actif():
    return engine.load_state().get("alertes", {}).get("actif", True)


def regler(actif_):
    with engine.LOCK:
        st = engine.load_state()
        st.setdefault("alertes", {})["actif"] = bool(actif_)
        engine.save_state(st)


def lien():
    return os.getenv("APP_URL", "").strip().rstrip("/")


def _date(d):
    try:
        x = datetime.date.fromisoformat(d)
        return "%d %s %d" % (x.day, MOIS[x.month - 1], x.year)
    except (TypeError, ValueError):
        return d or ""


def _manque(c):
    """Ce qui bloque, en une phrase simple."""
    out = []
    for b in c.get("blockers", []):
        if b.startswith("Partie") and "manquante" in b:
            out.append("une partie vient d'une série absente de la bibliothèque")
        elif "pas encore téléchargé" in b:
            out.append(b.split(" : ")[0] + " n'est pas encore téléchargé")
        else:
            out.append(b[:110])
    return " ; ".join(dict.fromkeys(out))[:260] or "quelque chose manque"


def _titre(c):
    if c.get("name"):
        return c["name"]
    p = c["parts"][0] if c.get("parts") else {}
    return p.get("title") or "Crossover"


def _bloc(ev, c):
    séries = " · ".join(dict.fromkeys(p["series"] for p in list(c.get("parts", [])) + list(c.get("refs", []))))
    tete = "<b>" + html.escape(_titre(c)) + "</b>"
    if c.get("date"):
        tete += " · " + html.escape(_date(c["date"]))
    recu = any(g.get("status") == "pret" for g in c.get("invites", []))
    if ev == "nouveau-pret":
        titre, corps = "🆕 Nouveau crossover détecté", "✅ Toutes les parties sont là : il est prêt à être rangé."
    elif ev == "nouveau-bloque":
        titre, corps = "🆕 Nouveau crossover détecté", "⏳ Pas encore rangeable : " + html.escape(_manque(c)) + "."
    else:
        titre = "🎉 Un crossover est prêt"
        corps = ("📥 L'épisode demandé est arrivé : " if recu else "✅ Ce qui manquait est arrivé : ") + "il est prêt à être rangé."
    return "%s\n%s\n<i>%s</i>\n%s" % (titre, tete, html.escape(séries), corps)


def composer(events):
    """events : liste de (type, carte). Renvoie le texte du message (HTML Telegram)."""
    blocs = [_bloc(ev, c) for ev, c in events[:MAX_PAR_MESSAGE]]
    reste = len(events) - MAX_PAR_MESSAGE
    texte = "🎬 <b>MouFlossover</b>\n\n" + "\n\n".join(blocs)
    if reste > 0:
        texte += "\n\n… et %d autre(s) dans l'appli." % reste
    prets = any(ev != "nouveau-bloque" for ev, _ in events)
    texte += "\n\n" + ("👉 Ouvre l'appli et touche « Organiser » sur la carte." if prets else "👉 Ouvre l'appli pour voir ce qui manque.")
    if lien():
        texte += '\n<a href="%s">Ouvrir MouFlossover →</a>' % html.escape(lien(), quote=True)
    return texte


def reperer(items, st):
    """Compare avec ce qu'on connaît déjà. Renvoie (événements, nouvel état) ; événements = [] au premier passage."""
    al = st.setdefault("alertes", {})
    premier = "vus" not in al
    vus = al.setdefault("vus", [])
    events = []
    for c in items:
        cles = {p["key"] for p in c.get("parts", [])}
        connu = next((v for v in vus if cles & set(v["keys"])), None)
        if connu is None:
            vus.append({"keys": sorted(cles), "status": c["status"]})
            if not premier and c["status"] in ("todo", "blocked"):
                events.append(("nouveau-pret" if c["status"] == "todo" else "nouveau-bloque", c))
            continue
        if connu["status"] == "blocked" and c["status"] == "todo":
            events.append(("pret", c))
        connu["keys"] = sorted(set(connu["keys"]) | cles)
        connu["status"] = c["status"]
    return events


def veiller(items):
    """Appelée après chaque actualisation de la bibliothèque."""
    with engine.LOCK:
        st = engine.load_state()
        events = reperer(items, st)
        engine.save_state(st)
    if events and actif():
        threading.Thread(target=envoyer, args=(composer(events),), daemon=True).start()


def envoyer(texte):
    ok, msg = notifier.envoyer(texte, html=True)
    if not ok:
        logger.warning("Notification non envoyée : %s", msg)
    return ok, msg


def test():
    c = {"name": "Message d'essai", "date": datetime.date.today().isoformat(), "parts": [{"series": "MouFlossover", "title": ""}], "invites": []}
    return envoyer("🎬 <b>MouFlossover</b>\n\n🔔 <b>Les notifications fonctionnent</b>\nVous recevrez un message ici dès qu'un crossover est détecté ou devient prêt à être rangé."
                   + ('\n<a href="%s">Ouvrir MouFlossover →</a>' % html.escape(lien(), quote=True) if lien() else ""))
