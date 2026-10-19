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
REFRESH = int(os.environ.get("REFRESH_INTERVAL", "900"))

SLOT_START = 21
WINDOW_DAYS = 1

FRANCHISES = {
    "Chicago": r"^chicago (fire|med|p\.?d|justice)",
    "FBI": r"^fbi",
    "Grey's / Station 19": r"^(grey's anatomy|station 19)",
}
TAGS = {"chicago fire": "FIRE", "chicago med": "MED", "chicago p.d.": "PD", "chicago justice": "JUSTICE",
        "fbi": "FBI", "fbi: most wanted": "MW", "fbi: international": "INTL",
        "grey's anatomy": "GREYS", "station 19": "S19"}
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
            m = re.match(r"^(.+?)\.?S\d{2}E\d{2}", f)
            if f.endswith(".mkv") and m:
                return m.group(1)
    return os.path.basename(series_dir)


def release_suffix(stem_name):
    m = re.match(r"^.*?S\d{2}E\d{2}(?:[.-]part(?:ie)?\d\.?(?:\([A-Za-z0-9]+\))?)?", stem_name)
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
    tag = TAGS.get(series["title"].lower(), "?")
    rx = re.compile(r"S%02dE%02d(?!\d)" % (part["season"], part["ep"]))
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
    groups = []
    for name, rx in FRANCHISES.items():
        members = [s for s in series if re.search(rx, s["title"].lower())]
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
            groups.append({"date": be["airDate"], "name": "", "wiki": [{"page": c["page"], "line": c["line"], "series": s["title"]}], "parts": {
                (s["id"], ae["seasonNumber"], ae["episodeNumber"]): {"s": s, "e": ae, "part": None, "src": "wikipedia"},
                (b["id"], be["seasonNumber"], be["episodeNumber"]): {"s": b, "e": be, "part": None, "src": "wikipedia"}}})
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
    items = []
    for g in groups:
        order = sorted(g["parts"].items(), key=lambda kv: (kv[1]["e"].get("airDateUtc") or kv[1]["e"].get("airDate") or "", kv[1]["s"]["title"]))
        used = {v["part"] for v in g["parts"].values() if v["part"]}
        free = iter(n for n in range(1, 50) if n not in used)
        for k, v in order:
            if v["part"] is None:
                v["part"] = next(free)
        titles = {v["s"]["title"] for v in g["parts"].values()}
        fr = next((fn for fn, rx in FRANCHISES.items() if all(re.search(rx, t.lower()) for t in titles)), "Autres séries")
        first = min(g["parts"].values(), key=lambda v: v["part"])
        name = g["name"] or PART_RX.sub("", first["e"].get("title") or "").strip() or "Crossover"
        cid = hashlib.sha1("|".join(sorted("%d-%d-%d" % k for k in g["parts"])).encode()).hexdigest()[:10]
        items.append({"id": cid, "franchise": fr, "date": g["date"], "parts": g["parts"], "name": name, "wiki": g.get("wiki", [])})
    items.sort(key=lambda c: c["date"], reverse=True)
    return items, series


def wiki_url(page):
    return "https://fr.wikipedia.org/wiki/" + urllib.parse.quote(page.replace(" ", "_"), safe="()',:!")


def lisible(ligne, maxi=190):
    """Passage de Wikipédia sans le code de la page : liens [[a|b]] → b, préfixes de liste et « commentaire = » retirés."""
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", ligne or "")
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
            out.append({"page": w["page"], "url": wiki_url(w["page"]), "extrait": lisible(w.get("line", "")), "serie": w.get("series", "")})
    if not out:
        for v in c["parts"].values():
            page = WIKI.page_of(v["s"]["title"])
            if page and page not in vues:
                vues.add(page)
                out.append({"page": page, "url": wiki_url(page), "extrait": "", "serie": v["s"]["title"]})
    return out[:6]


def part_list(c):
    out = []
    for k, v in sorted(c["parts"].items(), key=lambda kv: (kv[1]["part"], kv[1]["s"]["title"])):
        s, e = v["s"], v["e"]
        out.append({"series": s["title"], "seriesId": s["id"], "tag": TAGS.get(s["title"].lower(), "?"),
                    "season": e["seasonNumber"], "ep": e["episodeNumber"], "title": e.get("title") or "",
                    "part": v["part"], "hasFile": bool(e.get("hasFile")), "episodeId": e["id"],
                    "monitored": bool(e.get("monitored")), "source": v["src"], "path": s["path"]})
    return out


def make_plan(c):
    parts = part_list(c)
    ops, blockers, warnings = [], [], []
    own_ops = []     # renommages des fichiers d'origine : toujours APRÈS les copies, qui lisent encore l'ancien nom
    owners = {}
    sd = series_dir()
    for p in parts:
        sr = {"title": p["series"], "path": p["path"]}
        if sd and not (p["path"].rstrip("/") + "/").startswith(sd.rstrip("/") + "/"):
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
            info = nfo_info(os.path.join(o["dir"], stem_of(o["file"]) + ".nfo"))
            if info:
                m = PART_RX.search(info["title"])
                if not m or int(m.group(1)) != p["part"]:
                    warnings.append("%s : le titre du NFO « %s » ne se termine pas par (%d)" % (p["series"], info["title"], p["part"]))
            else:
                warnings.append("%s S%02dE%02d : pas de NFO" % (p["series"], p["season"], p["ep"]))
    if blockers:
        return {"ops": [], "blockers": blockers, "warnings": warnings}

    series_ids = []
    for p in parts:
        if p["seriesId"] not in series_ids:
            series_ids.append(p["seriesId"])
    allocated = {}
    for p in parts:
        o = owners[(p["seriesId"], p["season"], p["ep"])]
        if o["state"] == "plain":
            stem = stem_of(o["file"])
            pre = re.match(r"^(.*?S\d{2}E\d{2})", stem).group(1)
            new_stem = "%s.part%d(%s)%s" % (pre, p["part"], p["tag"], release_suffix(stem))
            own_ops.append({"kind": "rename_group", "dir": o["dir"], "from_stem": stem, "to_stem": new_stem,
                        "files": group_files(o["dir"], stem),
                        "label": "Renommer %s en part%d(%s)" % (p["series"], p["part"], p["tag"])})
            if p["monitored"]:
                own_ops.append({"kind": "unmonitor", "episodeId": p["episodeId"],
                            "label": "Ne plus surveiller %s S%02dE%02d dans Sonarr" % (p["series"], p["season"], p["ep"])})
    for sid in series_ids:
        own_parts = sorted([p for p in parts if p["seriesId"] == sid], key=lambda p: p["part"])
        t = own_parts[0]
        sdir = owners[(t["seriesId"], t["season"], t["ep"])]["dir"]
        spath = t["path"]
        prefix = file_prefix(spath)
        used = used_slots(spath) | allocated.setdefault(sid, set())
        for p in parts:
            if p["seriesId"] == sid:
                continue
            ow = owners[(p["seriesId"], p["season"], p["ep"])]
            ow_stem = stem_of(ow["file"])
            ow_nfo = nfo_info(os.path.join(ow["dir"], ow_stem + ".nfo"))
            ow_title = ow_nfo["title"] if ow_nfo else None
            nxt = next((q for q in own_parts if q["part"] > p["part"]), None)
            airs = (nxt["season"], nxt["ep"]) if nxt else (own_parts[-1]["season"], own_parts[-1]["ep"] + 1)
            existing = None
            own_files = {owners[(q["seriesId"], q["season"], q["ep"])]["file"] for q in own_parts}
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
                if sm:
                    ni = nfo_info(os.path.join(sdir, est + ".nfo"))
                    need = not ni or "<airsbefore_season>%d</airsbefore_season>" % airs[0] not in ni["text"] \
                        or "<airsbefore_episode>%d</airsbefore_episode>" % airs[1] not in ni["text"]
                    if need and ni:
                        ops.append({"kind": "patch_nfo", "path": os.path.join(sdir, est + ".nfo"), "season": 0,
                                    "episode": int(sm.group(1)), "airs": list(airs),
                                    "label": "Position de %s (part %d) dans %s" % (p["series"], p["part"], t["series"])})
                    continue
                n = next(i for i in range(SLOT_START, 999) if i not in used)
                used.add(n)
                new_est = "%s.S00E%02d.part%d(%s)%s" % (prefix, n, p["part"], p["tag"], release_suffix(est))
                ops.append({"kind": "rename_group", "dir": sdir, "from_stem": est, "to_stem": new_est,
                            "files": group_files(sdir, est),
                            "label": "Migrer %s (part %d) de %s vers S00E%02d" % (p["series"], p["part"], t["series"], n)})
                ops.append({"kind": "patch_nfo", "path": os.path.join(sdir, new_est + ".nfo"), "season": 0, "episode": n,
                            "airs": list(airs), "label": "Corriger le NFO de %s (part %d)" % (p["series"], p["part"])})
                continue
            n = next(i for i in range(SLOT_START, 999) if i not in used)
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
            ops.append({"kind": "patch_nfo", "path": os.path.join(sdir, new_stem + ".nfo"), "season": 0, "episode": n,
                        "airs": list(airs), "label": "Corriger le NFO de la copie %s dans %s" % (p["series"], t["series"])})
    return {"ops": ops + own_ops, "blockers": blockers, "warnings": warnings}


def refresh():
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
                            "status": status, "parts": [{k: v for k, v in p.items() if k != "path"} for p in parts],
                            "opCount": len(plan["ops"]), "blockers": plan["blockers"], "warnings": plan["warnings"],
                            "applied": c["id"] in st["done"], "wiki": wiki_sources(c)})
            CACHE.update({"items": out, "updated": datetime.datetime.now().isoformat(timespec="seconds"), "error": None})
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

    try:
        for op in plan["ops"]:
            k = op["kind"]
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
                        shutil.copy2(src, dst)
                        manifest["actions"].append({"type": "copy", "to": dst})
                    flush()
            elif k == "unmonitor":
                ep = sonarr("GET", "/episode/%d" % op["episodeId"])
                ep["monitored"] = False
                sonarr("PUT", "/episode/%d" % op["episodeId"], ep)
                manifest["actions"].append({"type": "unmonitor", "episodeId": op["episodeId"]})
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
                ins = "  <airsbefore_season>%d</airsbefore_season>\n  <airsbefore_episode>%d</airsbefore_episode>\n" % tuple(op["airs"])
                if "</episodedetails>" not in t:
                    raise RuntimeError("NFO sans </episodedetails> : " + op["path"])
                t = t.replace("</episodedetails>", ins + "</episodedetails>")
                with open(op["path"], "w", encoding="utf-8") as f:
                    f.write(t)
                manifest["actions"].append({"type": "nfo", "path": op["path"], "backup": bk})
                flush()
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
    for a in reversed(manifest["actions"]):
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
    if APPLYING["on"]:
        raisons.append("rangement d'un crossover")
    return raisons


def start_background():
    """Premier calcul, rafraîchissement régulier et analyse Wikipédia, tous en arrière-plan."""
    def boucle():
        while True:
            time.sleep(REFRESH)
            refresh()
    threading.Thread(target=refresh, daemon=True).start()
    threading.Thread(target=boucle, daemon=True).start()
    threading.Thread(target=lambda: (time.sleep(20), wiki_scan()), daemon=True).start()
