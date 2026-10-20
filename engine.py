"""Moteur de MouFlossover : repère les crossovers (Sonarr + Wikipédia), prépare et applique le rangement dans Emby.

Aucun serveur web ici : app.py expose ces fonctions. Les réglages (adresse et clé de Sonarr, dossier des séries)
sont relus à chaque appel dans l'environnement, qui vient de data/secrets.env.
"""
import datetime, glob, hashlib, json, os, re, shutil, sys, threading, time, traceback, uuid
import urllib.parse
import urllib.request
from pathlib import Path

import wiki as wikimod

BASE_DIR = Path(__file__).resolve().parent
DATA = os.environ.get("DATA_DIR", str(BASE_DIR / "data"))
if "unittest" in (sys.argv[0] if sys.argv else "") and not os.environ.get("DATA_DIR"):
    raise RuntimeError("Tests lancés sans DATA_DIR : refus de toucher aux vraies données (importe test_engine en premier).")
REFRESH = int(os.environ.get("REFRESH_INTERVAL", "900"))

SLOT_START = 100
WINDOW_DAYS = 1

FRANCHISES = {
    "Chicago": r"^chicago (fire|med|p\.?d|justice)",
    "FBI": r"^fbi",
    "NCIS": r"^ncis",
    "Grey's / Station 19": r"^(grey's anatomy|station 19)",
}
TAGS = {"chicago fire": "FIRE", "chicago med": "MED", "chicago p.d.": "PD", "chicago justice": "JUSTICE",
        "fbi": "FBI", "fbi: most wanted": "MW", "fbi: international": "INTL",
        "ncis": "NCIS", "ncis: los angeles": "LA", "ncis: new orleans": "NOLA", "ncis: hawai'i": "HAWAII",
        "ncis: sydney": "SYDNEY", "ncis: origins": "ORIGINS",
        "grey's anatomy": "GREYS", "station 19": "S19"}
STOP = {"the", "les", "le", "la", "des", "une", "a", "an", "my", "our", "one", "new", "star", "dr", "doctor"}


def tag_of(title):
    t = TAGS.get(title.lower())
    if t:
        return t
    if ":" in title:
        base = title.split(":", 1)[1]
    elif " - " in title:
        base = title.split(" - ", 1)[1]
    else:
        base = title
    import unicodedata
    words = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode())
    t = "".join(words) if len(words) == 1 else "".join(w[0] for w in words)
    return (t or "X").upper()[:8]


def franchise_key(title):
    t = title.lower().strip()
    for sep in (":", " - "):
        if sep in t:
            return t.split(sep, 1)[0].strip()
    w = re.findall(r"[a-z0-9']+", t)
    return w[0] if w and w[0] not in STOP and len(w[0]) >= 3 else None


PART_RX = re.compile(r"\((\d)\)\s*$")
TAG_RX = re.compile(r"\.part(\d)\.?\(([A-Za-z0-9]+)\)")
PARTANY_RX = re.compile(r"[.-]part(?:ie)?(\d)\.?(?:\(([A-Za-z0-9]+)\))?")
SEEDS = [
    {"name": "American Dreams", "date": "2020-03-24", "page": "FBI (série télévisée)",
     "parts": [("fbi", 2, 18, 1), ("fbi: most wanted", 1, 9, 2)]},
    {"name": "Budapest / Exposed", "date": "2021-09-21", "page": "FBI (série télévisée)",
     "parts": [("fbi", 4, 1, 1), ("fbi: most wanted", 3, 1, 2), ("fbi: international", 1, 1, 3)]},
    {"name": "Imminent Threat", "date": "2023-04-04", "page": "FBI (série télévisée)",
     "parts": [("fbi: international", 2, 16, 1), ("fbi", 5, 17, 2), ("fbi: most wanted", 4, 16, 3)]},
    {"name": "Nothing Seems the Same", "date": "2020-11-12", "page": "Grey's Anatomy : Station 19",
     "parts": [("station 19", 4, 1, 1), ("grey's anatomy", 17, 1, 2), ("grey's anatomy", 17, 2, 3)]},
    {"name": "Seattle en flammes", "date": "2024-05-23", "page": "Grey's Anatomy : Station 19",
     "parts": [("station 19", 7, 9, 1), ("station 19", 7, 10, 2), ("grey's anatomy", 20, 10, 3)]},
    {"name": "Flashlight", "date": "2017-03-10", "page": "MacGyver (série télévisée, 2016)",
     "parts": [("macgyver (2016)", 1, 18, 1)], "refs": [("hawaii five-0", 7, 19)]},
]

LOCK = threading.RLock()
os.makedirs(DATA, exist_ok=True)
WIKI = wikimod.Wiki(os.path.join(DATA, "mouflossover.db"), legacy_json=os.path.join(DATA, "wiki-cache.json"), delay=1.0)
WIKISTATE = {"running": False, "done": 0, "total": 0, "errors": 0, "claims": 0, "finished": None}
CACHE = {"items": [], "updated": None, "error": None}
EPCACHE = {}



def sonarr(method, path, body=None):
    base = os.environ.get("SONARR_URL", "http://192.168.1.133:8989/sonarr").rstrip("/")
    req = urllib.request.Request(base + "/api/v3" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": os.environ.get("SONARR_KEY", ""), "Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=120).read()
    return json.loads(raw) if raw else None


def load_state():
    try:
        with open(os.path.join(DATA, "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ignored": [], "done": {}, "history": []}


def save_state(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = os.path.join(DATA, "state.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DATA, "state.json"))


def episodes_of(series_id):
    if series_id not in EPCACHE:
        EPCACHE[series_id] = sonarr("GET", "/episode?seriesId=%d" % series_id)
    return EPCACHE[series_id]


def nfo_info(path):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            t = f.read()
    except OSError:
        return None
    g = lambda tag: (re.search(r"<%s>(.*?)</%s>" % (tag, tag), t, re.S) or [None, ""])[1].strip()
    return {"title": g("title"), "season": g("season"), "episode": g("episode"), "text": t}


def stem_of(path):
    return os.path.splitext(path)[0]


def group_files(directory, stem_name):
    out = []
    for f in sorted(os.listdir(directory)):
        if f.startswith(stem_name) and f[len(stem_name):][:1] in (".", "-"):
            out.append(f)
    return out


def file_prefix(series_dir):
    for dp, dn, fn in os.walk(series_dir):
        dn[:] = [d for d in dn if d != "@eaDir"]
        for f in sorted(fn):
            m = re.match(r"^(.+?)\.?S\d{2}\.?E\d{2}", f)
            if f.endswith(".mkv") and m:
                return m.group(1)
    return os.path.basename(series_dir)


def release_suffix(stem_name):
    m = re.match(r"^.*?S\d{2}\.?E\d{2}(?:[.-]part(?:ie)?\d\.?(?:\([A-Za-z0-9]+\))?)?", stem_name)
    return stem_name[m.end():] if m else ""


def season_dir(series_path, season):
    for cand in ("s%d" % season, "S%d" % season, "Season %d" % season, "Season %02d" % season):
        d = os.path.join(series_path, cand)
        if os.path.isdir(d):
            return d
    return None


def find_own(series, part):
    sd = season_dir(series["path"], part["season"])
    if not sd:
        return {"state": "missing"}
    tag = tag_of(series["title"])
    rx = re.compile(r"S%02d[. _-]?E%02d(?!\d)" % (part["season"], part["ep"]))
    cands = []
    for f in sorted(os.listdir(sd)):
        if not f.endswith(".mkv") or not rx.search(f):
            continue
        m = TAG_RX.search(f)
        if m and m.group(2).upper() != tag:
            continue
        cands.append((f, bool(m)))
    if not cands:
        return {"state": "missing"}
    if len(cands) > 1:
        same = [c for c in cands if (PARTANY_RX.search(c[0]) or [None, None])[1] == str(part["part"])]
        if len(same) == 1:
            cands = same
    if len(cands) > 1:
        return {"state": "ambiguous", "files": [c[0] for c in cands]}
    return {"state": "tagged" if cands[0][1] else "plain", "dir": sd, "file": cands[0][0]}


ALLITEMS = []


def slot_pref(c, sid):
    """Premier numéro de spécial voulu pour la série sid dans ce crossover : 100 pour son crossover le plus ancien,
    puis on avance de 1 par copie des crossovers précédents (ordre des dates)."""
    n = SLOT_START
    for o in ALLITEMS:
        if o["id"] == c["id"] or (o["date"], o["id"]) >= (c["date"], c["id"]):
            continue
        ids = [v["s"]["id"] for v in o["parts"].values()]
        if sid in ids:
            n += sum(1 for v in o["parts"].values() if v["s"]["id"] != sid and not v.get("ref") and not v.get("nocopy"))
    return n


def position_ok(texte, airs):
    """La place du spécial est bonne, qu'elle soit écrite par nous (airsbefore_*) ou par Emby quand il réécrit la fiche
    (displayseason / displayepisode)."""
    for a, b in (("airsbefore_season", "airsbefore_episode"), ("displayseason", "displayepisode")):
        if "<%s>%d</%s>" % (a, airs[0], a) in texte and "<%s>%d</%s>" % (b, airs[1], b) in texte:
            return True
    return False


def used_slots(series_path):
    used = set()
    for dp, dn, fn in os.walk(series_path):
        dn[:] = [d for d in dn if d != "@eaDir"]
        if dp[len(series_path):].count(os.sep) > 1:
            continue
        for f in fn:
            m = re.search(r"S00E(\d+)", f)
            if m:
                used.add(int(m.group(1)))
    return used


def build_crossovers():
    series = sonarr("GET", "/series")
    EPCACHE.clear()
    by_title = {s["title"].lower(): s for s in series}
    by_norm = {wikimod.norm(s["title"]): s for s in series}
    for s in series:
        pg = WIKI.page_of(s["title"])
        if pg:
            by_norm.setdefault(wikimod.norm(pg), s)
    groups = []
    auto = {}
    for s in series:
        k = franchise_key(s["title"])
        if k and s.get("statistics", {}).get("episodeFileCount", 0) > 0:
            auto.setdefault(k, []).append(s)
    known = {s["id"] for rx in FRANCHISES.values() for s in series if re.search(rx, s["title"].lower())}
    families = [(n, [s for s in series if re.search(rx, s["title"].lower())]) for n, rx in FRANCHISES.items()]
    families += [(k.title(), m) for k, m in auto.items() if len(m) >= 2 and not all(s["id"] in known for s in m)]
    for name, members in families:
        cands = []
        for s in members:
            for e in episodes_of(s["id"]):
                m = PART_RX.search(e.get("title") or "")
                if m and e.get("airDate") and e["seasonNumber"] > 0:
                    cands.append((e["airDate"], s, e, int(m.group(1))))
        cands.sort(key=lambda c: c[0])
        cur = []
        chunks = []
        for c in cands:
            d = datetime.date.fromisoformat(c[0])
            if cur and (d - datetime.date.fromisoformat(cur[-1][0])).days > WINDOW_DAYS:
                chunks.append(cur)
                cur = []
            cur.append(c)
        if cur:
            chunks.append(cur)
        for g in chunks:
            if len({c[1]["id"] for c in g}) < 2:
                continue
            parts = {}
            for date, s, e, pn in g:
                parts[(s["id"], e["seasonNumber"], e["episodeNumber"])] = {"s": s, "e": e, "part": pn, "src": "sonarr"}
            groups.append({"date": g[0][0], "parts": parts, "name": ""})
    for sd in SEEDS:
        resolved = {}
        for title, season, ep, pn in sd["parts"]:
            s = by_title.get(title)
            e = s and next((x for x in episodes_of(s["id"]) if x["seasonNumber"] == season and x["episodeNumber"] == ep), None)
            if e:
                resolved[(s["id"], season, ep)] = {"s": s, "e": e, "part": None, "src": "wikipedia"}
        # « refs » : série qui reçoit l'épisode sans avoir de suite à elle (simple apparition de ses personnages). L'épisode
        # indiqué sert seulement de repère : la copie se place juste avant lui.
        for title, season, ep in sd.get("refs", []):
            s = by_title.get(title)
            e = s and next((x for x in episodes_of(s["id"]) if x["seasonNumber"] == season and x["episodeNumber"] == ep), None)
            if e:
                resolved[(s["id"], season, ep)] = {"s": s, "e": e, "part": 0, "ref": True, "src": "wikipedia"}
        if len(resolved) >= 2:
            groups.append({"date": sd["date"], "parts": resolved, "name": sd["name"],
                           "wiki": [{"page": sd["page"], "line": "", "series": ""}]})
    for s in series:
        rec = WIKI.get(s["title"])
        if not rec:
            continue
        for c in rec["claims"]:
            b = by_norm.get(c["bn"])
            if not b or b["id"] == s["id"]:
                continue
            be = next((x for x in episodes_of(b["id"]) if x["seasonNumber"] == c["season"] and x["episodeNumber"] == c["episode"]), None)
            if not be or not be.get("airDate"):
                continue
            bd = datetime.date.fromisoformat(be["airDate"])
            near = [x for x in episodes_of(s["id"]) if x["seasonNumber"] > 0 and x.get("airDate")
                    and abs((datetime.date.fromisoformat(x["airDate"]) - bd).days) <= WINDOW_DAYS]
            if not near:
                continue
            ae = min(near, key=lambda x: abs((datetime.date.fromisoformat(x["airDate"]) - bd).days))
            hint = partie_dite(c["line"])
            groups.append({"date": be["airDate"], "name": "", "wiki": [{"page": c["page"], "line": c["line"], "series": s["title"]}], "parts": {
                (s["id"], ae["seasonNumber"], ae["episodeNumber"]): {"s": s, "e": ae, "part": hint, "src": "wikipedia"},
                (b["id"], be["seasonNumber"], be["episodeNumber"]): {"s": b, "e": be, "part": None, "src": "wikipedia"}}})
    import tvdb
    par_id = {s["id"]: s for s in series}
    for h in tvdb.indices():
        a, bser = par_id.get(h["sid"]), par_id.get(h["oid"])
        if not a or not bser:
            continue
        ae = next((x for x in episodes_of(a["id"]) if x["seasonNumber"] == h["season"] and x["episodeNumber"] == h["episode"]), None)
        if not ae or not ae.get("airDate"):
            continue
        be = None
        if h.get("bs"):
            be = next((x for x in episodes_of(bser["id"]) if x["seasonNumber"] == h["bs"] and x["episodeNumber"] == h["be"]), None)
        if not be:
            ad = datetime.date.fromisoformat(ae["airDate"])
            proches = [x for x in episodes_of(bser["id"]) if x["seasonNumber"] > 0 and x.get("airDate")
                       and abs((datetime.date.fromisoformat(x["airDate"]) - ad).days) <= WINDOW_DAYS]
            be = min(proches, key=lambda x: abs((datetime.date.fromisoformat(x["airDate"]) - ad).days)) if proches else None
        if not be:
            continue
        groups.append({"date": min(ae["airDate"], be.get("airDate") or ae["airDate"]), "name": "",
                       "wiki": [{"page": "TheTVDB : " + a["title"], "line": h["line"], "series": a["title"],
                                 "url": "https://thetvdb.com/dereferrer/series/%d" % a["tvdbId"] if a.get("tvdbId") else ""}],
                       "parts": {(a["id"], ae["seasonNumber"], ae["episodeNumber"]): {"s": a, "e": ae, "part": partie_dite(h["line"]), "src": "tvdb"},
                                 (bser["id"], be["seasonNumber"], be["episodeNumber"]): {"s": bser, "e": be, "part": None, "src": "tvdb"}}})
    import enwiki
    for titre, pp in enwiki.crossovers().items():
        ok, ko = enwiki.resoudre(pp, by_norm, episodes_de=episodes_of)
        if len({sr["id"] for sr, _, _ in ok}) < 2:
            continue
        dates = [ep["airDate"] for _, ep, _ in ok if ep.get("airDate")]
        ligne = " · ".join("Partie %d : %s S%02dE%02d « %s »" % (x["n"], x["series"], x["season"] or 0, x["episode"] or 0, x["title"]) for x in pp)
        groups.append({"date": min(dates) if dates else "2000-01-01", "name": "",
                       "wiki": [{"page": titre + " (Wikipédia anglais)", "line": ligne, "series": ok[0][0]["title"], "url": enwiki.lien(titre)}],
                       "parts": {(sr["id"], ep["seasonNumber"], ep["episodeNumber"]): {"s": sr, "e": ep, "part": n, "src": "wikipedia"}
                                 for sr, ep, n in ok}})
    # Crossovers ajoutés à la main (état « manuels ») : ils remplacent toute détection automatique qui touche les mêmes épisodes
    par_id = {s["id"]: s for s in series}
    manuels = []
    for m in load_state().get("manuels", []):
        res = {}
        for sec, cle in (("parts", False), ("refs", True)):
            for n, pp in enumerate(m.get(sec, []), 1):
                s_ = par_id.get(pp["sid"])
                e_ = s_ and next((x for x in episodes_of(s_["id"]) if x["seasonNumber"] == pp["season"] and x["episodeNumber"] == pp["ep"]), None)
                if e_:
                    res[(s_["id"], pp["season"], pp["ep"])] = {"s": s_, "e": e_, "part": 0 if cle else n, "src": "manuel", **({"ref": True} if cle else {}),
                                                         **({"nocopy": True} if pp.get("nocopy") else {})}
        if not any(not v.get("ref") for v in res.values()) or len({v["s"]["id"] for v in res.values()}) < 2:
            continue
        dates = [v["e"]["airDate"] for v in res.values() if v["e"].get("airDate")]
        manuels.append({"date": min(dates) if dates else "2000-01-01", "parts": res, "name": m.get("name", ""), "manuel": m["id"], "wiki": []})
    pris = set().union(*[set(g["parts"]) for g in manuels]) if manuels else set()
    groups = manuels + [g for g in groups if not (set(g["parts"]) & pris)]
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if set(groups[i]["parts"]) & set(groups[j]["parts"]):
                    for k, v in groups[j]["parts"].items():
                        cur = groups[i]["parts"].get(k)
                        if cur is None or (cur["part"] is None and v["part"] is not None):
                            groups[i]["parts"][k] = v
                    groups[i]["date"] = min(groups[i]["date"], groups[j]["date"])
                    groups[i]["name"] = groups[i]["name"] or groups[j]["name"]
                    groups[i]["wiki"] = groups[i].get("wiki", []) + groups[j].get("wiki", [])
                    del groups[j]
                    merged = True
                    break
            if merged:
                break
    etat = load_state()
    for g in etat.get("guests", []):
        if g.get("status") == "annule" or not g.get("sonarrId"):
            continue
        gs = next((x for x in series if x["id"] == g["sonarrId"]), None)
        ge = gs and next((x for x in episodes_of(gs["id"]) if x["seasonNumber"] == g["season"] and x["episodeNumber"] == g["episode"]), None)
        if not ge:
            continue
        for grp in groups:
            if set(g["with"]) <= {"%d-%d-%d" % k for k in grp["parts"]}:
                grp["parts"][(gs["id"], g["season"], g["episode"])] = {"s": gs, "e": ge, "part": g["part"], "src": "invite",
                                                                       "nettoye": g.get("status") == "range"}
                break
    items = []
    fixes = etat.get("fixes", {})
    for g in groups:
        order = sorted(g["parts"].items(), key=lambda kv: (kv[1]["e"].get("airDateUtc") or kv[1]["e"].get("airDate") or "", kv[1]["s"]["title"]))
        used = {v["part"] for v in g["parts"].values() if v["part"]}
        free = iter(n for n in range(1, 50) if n not in used)
        for k, v in order:
            if v["part"] is None and not v.get("ref"):
                v["part"] = next(free)
        titles = {v["s"]["title"] for v in g["parts"].values()}
        fr = next((fn for fn, rx in FRANCHISES.items() if all(re.search(rx, t.lower()) for t in titles)), None) \
            or next((fn for fn, m in families if titles <= {x["title"] for x in m}), "Autres séries")
        first = min((v for v in g["parts"].values() if not v.get("ref")), key=lambda v: v["part"])
        name = g["name"] or PART_RX.sub("", first["e"].get("title") or "").strip() or "Crossover"
        cid = hashlib.sha1("|".join(sorted("%d-%d-%d" % k for k in g["parts"])).encode()).hexdigest()[:10]
        parts = g["parts"]
        fx = fixes.get(cid)
        if fx:
            parts = {k: v for k, v in parts.items() if "%d-%d-%d" % k not in fx.get("exclude", [])}
            if len({v["s"]["id"] for v in parts.values()}) < 2:
                continue
            for k, v in parts.items():
                v["nocopy"] = "%d-%d-%d" % k in fx.get("nocopy", [])
            rang = {key: i for i, key in enumerate(fx.get("order", []))}
            for i, (k, v) in enumerate(sorted([kv for kv in parts.items() if not kv[1].get("ref")], key=lambda kv: (rang.get("%d-%d-%d" % kv[0], 999), kv[1]["part"]))):
                v["part"] = i + 1
        items.append({"id": cid, "franchise": fr, "date": g["date"], "parts": parts, "name": name, "wiki": g.get("wiki", []),
                      "fixed": bool(fx), "manuel": g.get("manuel")})
    items.sort(key=lambda c: c["date"], reverse=True)
    ALLITEMS[:] = items
    return items, series


def partie_dite(ligne):
    """« Il s'agit de la deuxième partie… » / « Part 2 of a crossover… » / « the second part of a major crossover » :
    numéro de la partie de l'épisode dont parle la phrase (None si elle ne le dit pas)."""
    t = ligne or ""
    m = re.search(r"\b(première|deuxième|seconde|troisième|quatrième)\s+partie\b", t, re.I)
    if m:
        return {"première": 1, "deuxième": 2, "seconde": 2, "troisième": 3, "quatrième": 4}[m.group(1).lower()]
    m = re.search(r"\b(first|second|third|fourth)\s+part\b", t, re.I)
    if m:
        return {"first": 1, "second": 2, "third": 3, "fourth": 4}[m.group(1).lower()]
    m = re.search(r"\bpart\s+(one|two|three|four|[1-4])\b", t, re.I)
    if m:
        return {"one": 1, "two": 2, "three": 3, "four": 4}.get(m.group(1).lower()) or int(m.group(1))
    return None


def wiki_url(page):
    return "https://fr.wikipedia.org/wiki/" + urllib.parse.quote(page.replace(" ", "_"), safe="()',:!")


def lisible(ligne, maxi=190):
    """Passage de Wikipédia sans le code de la page : liens [[a|b]] → b, préfixes de liste et « commentaire = » retirés."""
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", ligne or "")
    t = re.sub(r"\[\[[^\]]*$", "", t)          # lien coupé en fin de passage
    t = re.sub(r"\{\{[Hh]eure\|(\d+)(?:\|(\d+))?\}\}", lambda m: m.group(1) + " h" + (" " + m.group(2) if m.group(2) else ""), t)
    t = re.sub(r"\{\{(?:[^{}|]*\|)*([^{}|]*)\}\}", r"\1", t)   # autres modèles : on garde le dernier texte
    t = re.sub(r"\{\{[^}]*$", "", t)            # modèle coupé en fin de passage
    t = re.sub(r"'{2,}", "", t)
    t = re.sub(r"^[\s|#*:→-]+", "", t)
    t = re.sub(r"^(commentaire|titre)\s*=\s*", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > maxi:
        t = t[:maxi].rsplit(" ", 1)[0] + "…"
    return t


def wiki_sources(c):
    """Pages Wikipédia à ouvrir pour vérifier un crossover : la page et le passage qui le mentionne ;
    à défaut, la page de chaque série concernée."""
    vues, out = set(), []
    for w in c.get("wiki", []):
        if w["page"] not in vues:
            vues.add(w["page"])
            out.append({"page": w["page"], "url": w.get("url") or wiki_url(w["page"]), "extrait": lisible(w.get("line", "")), "serie": w.get("series", "")})
    if not out:
        for v in c["parts"].values():
            page = WIKI.page_of(v["s"]["title"])
            if page and page not in vues:
                vues.add(page)
                out.append({"page": page, "url": wiki_url(page), "extrait": "", "serie": v["s"]["title"]})
    return out[:6]


_QCACHE = {"t": 0, "d": {}}


def file_dl():
    """Téléchargements en cours chez Sonarr (épisode -> avancement), relus au plus toutes les 15 s."""
    if time.time() - _QCACHE["t"] > 15:
        d = {}
        try:
            for r in (sonarr("GET", "/queue?pageSize=200") or {}).get("records", []):
                taille = r.get("size") or 0
                d[r.get("episodeId")] = {"pct": int(100 * (taille - (r.get("sizeleft") or 0)) / taille) if taille else 0,
                                         "status": r.get("status"), "avert": r.get("trackedDownloadStatus") == "warning",
                                         "id": r.get("id"), "added": r.get("added") or ""}
        except Exception:
            d = _QCACHE["d"]
        _QCACHE.update(t=time.time(), d=d)
    return _QCACHE["d"]


def dl_etat(eid, a_fichier):
    """Phrase d'état d'un épisode demandé mais pas encore là (None si rien à dire)."""
    if a_fichier:
        return None
    q = file_dl().get(eid)
    if q:
        if q["status"] == "completed":
            return "Téléchargé : Sonarr est en train de le ranger"
        if q["status"] == "warning" or q["avert"]:
            return "Téléchargement bloqué (aucune source pour l'instant) : %d %%" % q["pct"]
        return "Téléchargement en cours : %d %%" % q["pct"]
    dem = load_state().get("demandes", {}).get(str(eid))
    if dem:
        return "Recherche lancée à %s : aucune version trouvée pour l'instant, nouvel essai automatique toutes les 6 h" % time.strftime("%H:%M", time.localtime(dem["t"]))
    return None


def noter_demande(eid):
    with LOCK:
        st = load_state()
        st.setdefault("demandes", {})[str(eid)] = {"t": int(time.time()), "last": int(time.time())}
        save_state(st)


def part_list(c):
    out = []
    for k, v in sorted(c["parts"].items(), key=lambda kv: (kv[1]["part"], kv[1]["s"]["title"])):
        s, e = v["s"], v["e"]
        out.append({"key": "%d-%d-%d" % k, "series": s["title"], "seriesId": s["id"], "tag": tag_of(s["title"]),
                    "season": e["seasonNumber"], "ep": e["episodeNumber"], "title": e.get("title") or "",
                    "part": v["part"], "hasFile": bool(e.get("hasFile")), "episodeId": e["id"],
                    "monitored": bool(e.get("monitored")), "source": v["src"], "path": s["path"],
                    "overview": e.get("overview") or "", "airDate": e.get("airDate") or "",
                    "dl": dl_etat(e["id"], bool(e.get("hasFile"))), "ref": bool(v.get("ref")), "nocopy": bool(v.get("nocopy")), "nettoye": bool(v.get("nettoye"))})
    return out


def make_plan(c):
    # Épisode invité déjà copié partout puis retiré du dossier de passage : il n'y a plus rien à faire pour lui
    parts = [p for p in part_list(c) if not (p["nettoye"] and est_invite(p["path"]))]
    ops, blockers, warnings = [], [], []
    own_ops = []     # renommages des fichiers d'origine : toujours APRÈS les copies, qui lisent encore l'ancien nom
    owners = {}
    sd = series_dir()
    numeros = sorted(p["part"] for p in parts if not p["ref"])
    trous = [n for n in range(1, numeros[-1] + 1) if n not in numeros] if numeros else []
    if trous and c["id"] not in load_state().get("done", {}):
        blockers.append("Partie %s manquante : cette partie est dans une série absente de ta bibliothèque (ou un épisode inconnu de Sonarr)"
                        % ", ".join(str(n) for n in trous))
    for p in parts:
        sr = {"title": p["series"], "path": p["path"]}
        if sd and not (p["path"].rstrip("/") + "/").startswith(sd.rstrip("/") + "/") and not est_invite(p["path"]):
            blockers.append("%s : le dossier de la série (%s) est en dehors du dossier des séries réglé (%s)" % (p["series"], p["path"], sd))
            continue
        if not p["hasFile"]:
            blockers.append("%s S%02dE%02d : épisode pas encore téléchargé" % (p["series"], p["season"], p["ep"]))
            continue
        o = find_own(sr, p)
        if o["state"] == "missing":
            blockers.append("%s S%02dE%02d : fichier introuvable sur le disque" % (p["series"], p["season"], p["ep"]))
        elif o["state"] == "ambiguous":
            blockers.append("%s S%02dE%02d : plusieurs fichiers candidats (%s)" % (p["series"], p["season"], p["ep"], ", ".join(o["files"])))
        else:
            owners[(p["seriesId"], p["season"], p["ep"])] = o
            info = None if p["ref"] else nfo_info(os.path.join(o["dir"], stem_of(o["file"]) + ".nfo"))
            if p["ref"]:
                pass
            elif info:
                m = PART_RX.search(info["title"])
                if (not m or int(m.group(1)) != p["part"]) and len(numeros) > 1:
                    warnings.append("%s : le titre du NFO « %s » ne se termine pas par (%d)" % (p["series"], info["title"], p["part"]))
            elif not est_invite(p["path"]):
                warnings.append("%s S%02dE%02d : pas de NFO" % (p["series"], p["season"], p["ep"]))
    if blockers:
        return {"ops": [], "blockers": blockers, "warnings": warnings}

    series_ids = []
    for p in parts:
        if est_invite(p["path"]):        # série invitée : elle ne reçoit rien et son fichier n'est pas renommé
            continue
        if p["seriesId"] not in series_ids:
            series_ids.append(p["seriesId"])
    allocated = {}
    for p in parts:
        o = owners[(p["seriesId"], p["season"], p["ep"])]
        if o["state"] == "plain" and not est_invite(p["path"]) and not p["ref"]:
            stem = stem_of(o["file"])
            pre = re.match(r"^(.*?S\d{2}\.?E\d{2})", stem).group(1)
            new_stem = "%s.part%d(%s)%s" % (pre, p["part"], p["tag"], release_suffix(stem))
            own_ops.append({"kind": "rename_group", "dir": o["dir"], "from_stem": stem, "to_stem": new_stem,
                        "files": group_files(o["dir"], stem),
                        "label": "Renommer %s en part%d(%s)" % (p["series"], p["part"], p["tag"])})
            if p["monitored"]:
                own_ops.append({"kind": "unmonitor", "episodeId": p["episodeId"],
                            "label": "Ne plus surveiller %s S%02dE%02d dans Sonarr" % (p["series"], p["season"], p["ep"])})
    for sid in series_ids:
        own_parts = sorted([p for p in parts if p["seriesId"] == sid and not p["ref"]], key=lambda p: p["part"])
        repere = next((p for p in parts if p["seriesId"] == sid and p["ref"]), None)
        t = own_parts[0] if own_parts else repere
        sdir = owners[(t["seriesId"], t["season"], t["ep"])]["dir"]
        spath = t["path"]
        prefix = file_prefix(spath)
        used = used_slots(spath) | allocated.setdefault(sid, set())
        for p in parts:
            if p["seriesId"] == sid or p["ref"] or p["nocopy"]:      # « nocopy » : cette partie reste dans sa série
                continue
            ow = owners[(p["seriesId"], p["season"], p["ep"])]
            ow_stem = stem_of(ow["file"])
            ow_nfo = nfo_info(os.path.join(ow["dir"], ow_stem + ".nfo"))
            ow_title = ow_nfo["title"] if ow_nfo else None
            nxt = next((q for q in own_parts if q["part"] > p["part"]), None)
            if not own_parts:
                airs = (repere["season"], repere["ep"])       # série sans partie à elle : juste avant l'épisode repère
            else:
                airs = (nxt["season"], nxt["ep"]) if nxt else (own_parts[-1]["season"], own_parts[-1]["ep"] + 1)
            existing = None
            own_files = {owners[(q["seriesId"], q["season"], q["ep"])]["file"] for q in (own_parts or [repere])}
            for f in sorted(os.listdir(sdir)):
                m = PARTANY_RX.search(f)
                if not f.endswith(".mkv") or f in own_files or not m or int(m.group(1)) != p["part"]:
                    continue
                if m.group(2) and m.group(2).upper() != p["tag"]:
                    continue
                ni = nfo_info(os.path.join(sdir, stem_of(f) + ".nfo"))
                if ow_title and ni and ni["title"].strip() != ow_title.strip():
                    continue
                if not m.group(2) and not (ow_title and ni):
                    continue
                existing = f
                break
            if existing:
                est = stem_of(existing)
                sm = re.search(r"S00E(\d+)", existing)
                if sm and int(sm.group(1)) >= SLOT_START:
                    ni = nfo_info(os.path.join(sdir, est + ".nfo"))
                    need = not ni or not position_ok(ni["text"], airs) or ("<sorttitle>%02d " % p["part"]) not in ni["text"]
                    if need and ni:
                        ops.append({"kind": "patch_nfo", "path": os.path.join(sdir, est + ".nfo"), "season": 0,
                                    "episode": int(sm.group(1)), "airs": list(airs), "part": p["part"],
                                    "label": "Position de %s (part %d) dans %s" % (p["series"], p["part"], t["series"])})
                    continue
                n = next(i for i in range(slot_pref(c, sid), 999) if i not in used)
                used.add(n)
                new_est = "%s.S00E%02d.part%d(%s)%s" % (prefix, n, p["part"], p["tag"], release_suffix(est))
                ops.append({"kind": "rename_group", "dir": sdir, "from_stem": est, "to_stem": new_est,
                            "files": group_files(sdir, est),
                            "label": "Migrer %s (part %d) de %s vers S00E%02d" % (p["series"], p["part"], t["series"], n)})
                ops.append({"kind": "patch_nfo", "path": os.path.join(sdir, new_est + ".nfo"), "season": 0, "episode": n,
                            "airs": list(airs), "part": p["part"], "label": "Corriger le NFO de %s (part %d)" % (p["series"], p["part"])})
                continue
            n = next(i for i in range(slot_pref(c, sid), 999) if i not in used)
            used.add(n)
            new_stem = "%s.S00E%02d.part%d(%s)%s" % (prefix, n, p["part"], p["tag"], release_suffix(ow_stem))
            dst_exists = [f for f in group_files(ow["dir"], ow_stem)
                          if os.path.exists(os.path.join(sdir, new_stem + f[len(ow_stem):]))]
            if dst_exists:
                blockers.append("Destination existante : %s" % new_stem)
                continue
            ops.append({"kind": "copy_group", "src_dir": ow["dir"], "dir": sdir, "from_stem": ow_stem, "to_stem": new_stem,
                        "files": group_files(ow["dir"], ow_stem),
                        "label": "Copier %s (part %d) dans %s en S00E%02d" % (p["series"], p["part"], t["series"], n)})
            if not ow_nfo:       # pas de fiche (épisode invité) : on en crée une depuis les infos de Sonarr
                ops.append({"kind": "make_nfo", "path": os.path.join(sdir, new_stem + ".nfo"), "title": p["title"],
                            "plot": p["overview"], "aired": p["airDate"], "season": 0, "episode": n,
                            "label": "Créer la fiche de %s (part %d) dans %s" % (p["series"], p["part"], t["series"])})
            ops.append({"kind": "patch_nfo", "path": os.path.join(sdir, new_stem + ".nfo"), "season": 0, "episode": n,
                        "airs": list(airs), "part": p["part"], "label": "Corriger le NFO de la copie %s dans %s" % (p["series"], t["series"])})
    return {"ops": ops + own_ops, "blockers": blockers, "warnings": warnings}


def refresh():
    import invites
    with LOCK:
        try:
            items, series = build_crossovers()
            st = load_state()
            out = []
            for c in items:
                parts = part_list(c)
                plan = make_plan(c)
                if c["id"] in st["ignored"]:
                    status = "ignored"
                elif plan["blockers"]:
                    status = "blocked"
                elif plan["ops"]:
                    status = "todo"
                else:
                    status = "done"
                out.append({"id": c["id"], "name": c["name"], "franchise": c["franchise"], "date": c["date"],
                            "status": status, "parts": [{k: v for k, v in p.items() if k != "path"} for p in parts if not p["ref"]],
                            "refs": [{"series": p["series"], "season": p["season"], "ep": p["ep"], "title": p["title"]} for p in parts if p["ref"]],
                            "opCount": len(plan["ops"]), "blockers": plan["blockers"], "warnings": plan["warnings"],
                            "applied": c["id"] in st["done"], "wiki": wiki_sources(c), "fixed": c.get("fixed", False), "manuel": c.get("manuel"),
                            "invites": invites.pour_carte({p["key"] for p in parts})})
            CACHE.update({"items": out, "updated": datetime.datetime.now().isoformat(timespec="seconds"), "error": None})
            if out and not WIKISTATE["running"]:
                import alerts
                alerts.veiller(out)
        except Exception as ex:
            traceback.print_exc()
            CACHE["error"] = str(ex)


def wiki_scan(force=False):
    if WIKISTATE["running"]:
        return
    WIKISTATE.update(running=True, done=0, errors=0)
    try:
        series = sonarr("GET", "/series")
        WIKISTATE["total"] = len(series)
        for s in series:
            try:
                WIKI.claims(s["title"], force=force)
            except Exception:
                WIKISTATE["errors"] += 1
                time.sleep(10)
            WIKISTATE["done"] += 1
            if WIKISTATE["done"] % 20 == 0:
                refresh()
    finally:
        WIKISTATE["claims"] = WIKI.stats()["claims"]
        WIKISTATE.update(running=False, finished=datetime.datetime.now().isoformat(timespec="seconds"))
        refresh()


JOB = {"state": "idle"}      # état d'avancement du rangement / de l'annulation en cours, lu par l'écran


def job_pct():
    t = max(JOB.get("total", 1), 1)
    sub = (JOB.get("sub_i", 0) + JOB.get("ff", 0)) / max(JOB.get("sub_n", 1), 1)
    return min(100, int(100 * (JOB.get("done", 0) + sub) / t))


def copie_suivie(src, dst):
    """Copie un fichier par morceaux en mettant à jour l'avancement (les vidéos sont lourdes)."""
    taille = os.path.getsize(src)
    fait = 0
    with open(src, "rb") as a, open(dst, "wb") as b:
        while True:
            buf = a.read(8 * 1024 * 1024)
            if not buf:
                break
            b.write(buf)
            fait += len(buf)
            JOB["ff"] = fait / taille if taille else 1
            JOB["pct"] = job_pct()
    shutil.copystat(src, dst)
    JOB["ff"] = 0


def _execute(c, plan, st):
    mid = "%s-%s" % (c["id"], datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    mdir = os.path.join(DATA, "manifests")
    bdir = os.path.join(mdir, "nfo-backups")
    os.makedirs(bdir, exist_ok=True)
    manifest = {"id": c["id"], "name": c["name"], "date": datetime.datetime.now().isoformat(timespec="seconds"), "actions": [], "status": "running"}
    mpath = os.path.join(mdir, mid + ".json")

    def flush():
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)

    JOB.update(state="running", kind="apply", name=c["name"], total=len(plan["ops"]), done=0, sub_i=0, sub_n=1, ff=0, pct=0,
               label="Préparation…", message="", emby="", ok=None)
    try:
        for op in plan["ops"]:
            k = op["kind"]
            JOB.update(label=op.get("label", ""), sub_i=0, sub_n=len(op.get("files", [])) or 1, ff=0, pct=job_pct())
            if k in ("rename_group", "copy_group"):
                for f in op["files"]:
                    dst = os.path.join(op["dir"], op["to_stem"] + f[len(op["from_stem"]):])
                    if os.path.exists(dst):
                        raise RuntimeError("Destination existe déjà : " + dst)
                    if k == "rename_group":
                        src = os.path.join(op["dir"], f)
                        os.rename(src, dst)
                        manifest["actions"].append({"type": "rename", "from": src, "to": dst})
                    else:
                        src = os.path.join(op["src_dir"], f)
                        copie_suivie(src, dst)
                        manifest["actions"].append({"type": "copy", "to": dst})
                    flush()
                    JOB["sub_i"] += 1
            elif k == "unmonitor":
                ep = sonarr("GET", "/episode/%d" % op["episodeId"])
                ep["monitored"] = False
                sonarr("PUT", "/episode/%d" % op["episodeId"], ep)
                manifest["actions"].append({"type": "unmonitor", "episodeId": op["episodeId"]})
                flush()
            elif k == "make_nfo":
                from xml.sax.saxutils import escape
                if os.path.exists(op["path"]):
                    raise RuntimeError("Destination existe déjà : " + op["path"])
                with open(op["path"], "w", encoding="utf-8") as f:
                    f.write('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<episodedetails>\n  <title>%s</title>\n'
                            '  <plot>%s</plot>\n  <aired>%s</aired>\n  <episode>%d</episode>\n  <season>%d</season>\n</episodedetails>\n'
                            % (escape(op["title"]), escape(op["plot"]), op["aired"], op["episode"], op["season"]))
                manifest["actions"].append({"type": "copy", "to": op["path"]})     # l'annulation supprimera ce fichier créé par nous
                flush()
            elif k == "patch_nfo":
                with open(op["path"], encoding="utf-8") as f:
                    orig = f.read()
                bk = os.path.join(bdir, uuid.uuid4().hex + ".nfo")
                with open(bk, "w", encoding="utf-8") as f:
                    f.write(orig)
                s = re.findall(r"<season>\d+</season>", orig)
                e = re.findall(r"<episode>\d+</episode>", orig)
                if len(s) != 1 or len(e) != 1:
                    raise RuntimeError("Balises season/episode ambiguës : " + op["path"])
                t = re.sub(r"<season>\d+</season>", "<season>%d</season>" % op["season"], orig)
                t = re.sub(r"<episode>\d+</episode>", "<episode>%d</episode>" % op["episode"], t)
                t = re.sub(r"\s*<airsbefore_(season|episode)>\d+</airsbefore_\1>", "", t)
                t = re.sub(r"\s*<airsafter_season>\d+</airsafter_season>", "", t)
                if op.get("part"):
                    # Deux spéciaux au même endroit : Emby les range alors par ordre alphabétique du titre. Un titre de tri
                    # « 01 … », « 02 … » impose l'ordre des parties.
                    mt = re.search(r"<title>(.*?)</title>", t, re.S)
                    t = re.sub(r"\s*<sorttitle>.*?</sorttitle>", "", t, flags=re.S)
                    if mt:
                        t = t.replace(mt.group(0), mt.group(0) + "\n  <sorttitle>%02d %s</sorttitle>" % (op["part"], mt.group(1).strip()), 1)
                ins = "  <airsbefore_season>%d</airsbefore_season>\n  <airsbefore_episode>%d</airsbefore_episode>\n" % tuple(op["airs"])
                if "</episodedetails>" not in t:
                    raise RuntimeError("NFO sans </episodedetails> : " + op["path"])
                t = t.replace("</episodedetails>", ins + "</episodedetails>")
                with open(op["path"], "w", encoding="utf-8") as f:
                    f.write(t)
                manifest["actions"].append({"type": "nfo", "path": op["path"], "backup": bk})
                flush()
            JOB.update(done=JOB["done"] + 1, sub_i=0, ff=0, pct=job_pct())
        manifest["status"] = "done"
        flush()
        st["done"][c["id"]] = mpath
        st["history"].insert(0, {"id": c["id"], "name": c["name"], "action": "applied", "date": manifest["date"]})
        save_state(st)
        return {"ok": True, "manifest": mpath, "actions": len(manifest["actions"])}
    except Exception as ex:
        manifest["status"] = "error: %s" % ex
        flush()
        st["history"].insert(0, {"id": c["id"], "name": c["name"], "action": "error", "date": manifest["date"], "detail": str(ex)})
        save_state(st)
        return {"ok": False, "error": str(ex), "manifest": mpath, "actions": len(manifest["actions"])}


def undo(cid, st):
    mpath = st["done"].get(cid)
    if not mpath or not os.path.exists(mpath):
        return {"ok": False, "error": "Aucun manifeste pour ce crossover"}
    with open(mpath, encoding="utf-8") as f:
        manifest = json.load(f)
    JOB.update(state="running", kind="undo", name=manifest.get("name", ""), total=len(manifest["actions"]), done=0, sub_i=0, sub_n=1,
               ff=0, pct=0, label="Remise en état…", message="", emby="", ok=None)
    for a in reversed(manifest["actions"]):
        JOB.update(done=JOB["done"] + 1, pct=job_pct())
        if a["type"] == "copy" and os.path.exists(a["to"]):
            os.remove(a["to"])
        elif a["type"] == "rename" and os.path.exists(a["to"]):
            os.rename(a["to"], a["from"])
        elif a["type"] == "nfo" and os.path.exists(a["backup"]):
            shutil.copyfile(a["backup"], a["path"])
        elif a["type"] == "unmonitor":
            ep = sonarr("GET", "/episode/%d" % a["episodeId"])
            ep["monitored"] = True
            sonarr("PUT", "/episode/%d" % a["episodeId"], ep)
    manifest["status"] = "undone"
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    del st["done"][cid]
    st["history"].insert(0, {"id": cid, "name": manifest["name"], "action": "undone", "date": datetime.datetime.now().isoformat(timespec="seconds")})
    save_state(st)
    return {"ok": True}


def find_crossover(cid):
    items, _ = build_crossovers()
    return next((c for c in items if c["id"] == cid), None)


APPLYING = {"on": False}


def staging_dir():
    """Dossier de passage des séries invitées : hors du dossier des séries, donc invisible pour Emby."""
    return os.environ.get("STAGING_DIR", "").strip() or os.path.join(os.path.dirname(series_dir().rstrip("/")), "Invites-MouFlossover")


def est_invite(path):
    return (path.rstrip("/") + "/").startswith(staging_dir().rstrip("/") + "/")


def series_dir():
    return os.environ.get("SERIES_DIR", "/mnt/mouflosyno/Emby-Media/Series").strip()


def execute(c, plan, st):
    APPLYING["on"] = True
    try:
        return _execute(c, plan, st)
    finally:
        APPLYING["on"] = False


def occupe():
    raisons = []
    if WIKISTATE["running"]:
        raisons.append("analyse de Wikipédia")
    if APPLYING["on"] or JOB.get("state") == "running":
        raisons.append("rangement d'un crossover")
    return raisons


def start_background():
    """Premier calcul, rafraîchissement régulier et analyse Wikipédia, tous en arrière-plan."""
    def boucle():
        while True:
            time.sleep(REFRESH)
            refresh()
    import invites
    threading.Thread(target=refresh, daemon=True).start()
    threading.Thread(target=boucle, daemon=True).start()
    invites.demarrer()
    import tvdb
    tvdb.demarrer()
    import enwiki
    enwiki.demarrer()
    threading.Thread(target=lambda: (time.sleep(20), wiki_scan()), daemon=True).start()
