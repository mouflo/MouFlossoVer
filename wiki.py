import json, os, re, sqlite3, threading, time, unicodedata, urllib.error, urllib.parse, urllib.request, zlib

UA = "MouFlossover/0.2 (personal media library tool)"
KEYWORDS = re.compile(r"crossover|cross-over|croisé|backdoor|en (?:deux|trois|quatre) parties|se poursuit|se conclut|s'achève|débute dans|se termine dans|suite dans", re.I)
ALIASES = {"chicagopolicedepartment": "chicagopd", "chicagopolice": "chicagopd"}
TTL = 30 * 86400
NO_PAGE_TTL = 90 * 86400
PARSER_VERSION = 1


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return ALIASES.get(s, s)


def clean(line):
    line = re.sub(r"<ref[^>]*?/>", "", line)
    line = re.sub(r"<ref[^>]*>.*?</ref>", "", line, flags=re.S)
    line = re.sub(r"\{\{[Ll]angue\|[^|]*\|(.*?)\}\}", r"\1", line)
    line = re.sub(r"</?small>", "", line)
    return line.replace("''", "")


def extract_refs(line):
    s = clean(line)
    refs = []
    for m in re.finditer(r"\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]\s*\(\s*saison\s+(\d+)\s*,\s*épisode\s+(\d+)\s*\)", s):
        refs.append((m.group(1), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r"épisode\s+(\d+)\s+de\s+la\s+\[\[Saison\s+(\d+)\s+de\s+([^\]|#]+)", s):
        refs.append((m.group(3), int(m.group(2)), int(m.group(1))))
    for m in re.finditer(r"épisode\s+(\d+)\s+de\s+la\s+saison\s+(\d+)\s+de\s+\[?\[?([^\]|,.;()\[]+)", s):
        refs.append((m.group(3).strip(), int(m.group(2)), int(m.group(1))))
    for m in re.finditer(r"\[\[Saison\s+(\d+)\s+de\s+([^\]|#]+)(?:#[^\]|]*)?\|\s*S(\d+)E(\d+)\s*\]\]", s):
        refs.append((m.group(2), int(m.group(3)), int(m.group(4))))
    return refs


def extract_claims(series_title, texts):
    me = norm(series_title)
    out, seen = [], set()
    for ptitle, txt in texts.items():
        for line in txt.split("\n"):
            if not KEYWORDS.search(line):
                continue
            for name, season, ep in extract_refs(line):
                n = norm(name)
                if n and n != me and (n, season, ep) not in seen:
                    seen.add((n, season, ep))
                    out.append({"b": name, "bn": n, "season": season, "episode": ep, "page": ptitle,
                                "line": re.sub(r"\s+", " ", clean(line))[:220]})
    return out


class Wiki:
    def __init__(self, db_path, legacy_json=None, delay=1.0, lang="fr"):
        self.delay = delay
        self.lang = lang
        self.calls = 0
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS wiki_pages(title TEXT PRIMARY KEY, revid INTEGER, fetched_at REAL, text BLOB);
            CREATE TABLE IF NOT EXISTS wiki_series(series TEXT PRIMARY KEY, page TEXT, pages TEXT, scanned_at REAL, parser INTEGER, legacy INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS wiki_claims(series TEXT, b TEXT, bn TEXT, season INTEGER, episode INTEGER, page TEXT, line TEXT,
                                                   UNIQUE(series, bn, season, episode));
            CREATE INDEX IF NOT EXISTS idx_claims_series ON wiki_claims(series);
        """)
        self.db.commit()
        if legacy_json and os.path.exists(legacy_json):
            self.import_legacy(legacy_json)

    def import_legacy(self, path):
        with self.lock:
            if self.db.execute("SELECT COUNT(*) FROM wiki_series").fetchone()[0]:
                return
            try:
                with open(path, encoding="utf-8") as f:
                    old = json.load(f)
            except Exception:
                return
            for series, rec in old.items():
                self.db.execute("INSERT OR REPLACE INTO wiki_series VALUES (?,?,?,?,?,1)",
                                (series, rec.get("page"), json.dumps(rec.get("pages", [])), rec.get("t", 0), PARSER_VERSION))
                for c in rec.get("claims", []):
                    self.db.execute("INSERT OR IGNORE INTO wiki_claims VALUES (?,?,?,?,?,?,?)",
                                    (series, c["b"], c["bn"], c["season"], c["episode"], c.get("page"), c.get("line")))
            self.db.commit()
        try:
            os.replace(path, path + ".migre")
        except OSError:
            pass

    def api(self, params):
        u = "https://%s.wikipedia.org/w/api.php?" % self.lang + urllib.parse.urlencode(dict(params, format="json"))
        for attempt in range(6):
            time.sleep(self.delay)
            self.calls += 1
            try:
                req = urllib.request.Request(u, headers={"User-Agent": UA})
                return json.loads(urllib.request.urlopen(req, timeout=40).read())
            except urllib.error.HTTPError as ex:
                if ex.code not in (429, 500, 502, 503, 504) or attempt == 5:
                    raise
                wait = int(ex.headers.get("Retry-After") or 0) or 8 * (attempt + 1)
                time.sleep(min(wait, 90))
        raise RuntimeError("Wikipédia injoignable")

    def fetch(self, titles):
        out = {}
        for i in range(0, len(titles), 15):
            r = self.api({"action": "query", "prop": "revisions", "rvprop": "content|ids", "rvslots": "main",
                          "titles": "|".join(titles[i:i + 15]), "redirects": 1})
            for p in r.get("query", {}).get("pages", {}).values():
                rev = p.get("revisions", [{}])[0]
                txt = rev.get("slots", {}).get("main", {}).get("*", "")
                if txt:
                    out[p["title"]] = (txt, rev.get("revid"))
        return out

    def revids(self, titles):
        out = {}
        for i in range(0, len(titles), 40):
            r = self.api({"action": "query", "prop": "info", "titles": "|".join(titles[i:i + 40]), "redirects": 1})
            for p in r.get("query", {}).get("pages", {}).values():
                if p.get("lastrevid"):
                    out[p["title"]] = p["lastrevid"]
        return out

    def find_page(self, title):
        cands = ["%s (série télévisée)" % title, title, "%s (série d'animation)" % title, "%s (anime)" % title]
        pages = self.fetch(cands)
        for c in cands:
            for t, (txt, rid) in pages.items():
                if norm(t) == norm(c) or norm(t) == norm(title):
                    if "{{Homonymie" in txt or "{{Page d'homonymie" in txt or len(txt) < 1500:
                        continue
                    return t, txt, rid
        r = self.api({"action": "query", "list": "search", "srsearch": "%s série télévisée" % title, "srlimit": 3})
        for h in r.get("query", {}).get("search", []):
            if norm(h["title"]).startswith(norm(title)) and not h["title"].startswith(("Liste", "Saison")):
                for t, (txt, rid) in self.fetch([h["title"]]).items():
                    return t, txt, rid
        return None, "", None

    def _stored_texts(self, titles):
        out = {}
        with self.lock:
            for t in titles:
                row = self.db.execute("SELECT text FROM wiki_pages WHERE title=?", (t,)).fetchone()
                if not row:
                    return None
                out[t] = zlib.decompress(row[0]).decode("utf-8")
        return out

    def _save(self, series, page, texts):
        claims = extract_claims(series, {t: v[0] for t, v in texts.items()})
        now = time.time()
        with self.lock:
            for t, (txt, rid) in texts.items():
                self.db.execute("INSERT OR REPLACE INTO wiki_pages VALUES (?,?,?,?)",
                                (t, rid, now, zlib.compress(txt.encode("utf-8"), 6)))
            self.db.execute("DELETE FROM wiki_claims WHERE series=?", (series,))
            for c in claims:
                self.db.execute("INSERT OR IGNORE INTO wiki_claims VALUES (?,?,?,?,?,?,?)",
                                (series, c["b"], c["bn"], c["season"], c["episode"], c["page"], c["line"]))
            self.db.execute("INSERT OR REPLACE INTO wiki_series VALUES (?,?,?,?,?,0)",
                            (series, page, json.dumps(list(texts)), now, PARSER_VERSION))
            self.db.commit()

    def _touch(self, series):
        with self.lock:
            self.db.execute("UPDATE wiki_series SET scanned_at=? WHERE series=?", (time.time(), series))
            self.db.commit()

    def _full_scan(self, series):
        page, text, rid = self.find_page(series)
        if not page:
            with self.lock:
                self.db.execute("DELETE FROM wiki_claims WHERE series=?", (series,))
                self.db.execute("INSERT OR REPLACE INTO wiki_series VALUES (?,?,?,?,?,0)",
                                (series, None, "[]", time.time(), PARSER_VERSION))
                self.db.commit()
            return
        texts = {page: (text, rid)}
        me = norm(series)
        seasons = []
        for m in re.finditer(r"\[\[(Saison\s+\d+\s+(?:de|d')\s*([^\]|#]+))", text):
            if norm(m.group(2)) in (me, norm(page)):
                seasons.append(m.group(1).strip())
        seasons = list(dict.fromkeys(seasons))
        if seasons:
            texts.update(self.fetch(seasons))
        self._save(series, page, texts)

    def claims(self, series, force=False):
        with self.lock:
            row = self.db.execute("SELECT page, pages, scanned_at, parser, legacy FROM wiki_series WHERE series=?", (series,)).fetchone()
        now = time.time()
        if row:
            page, pages_json, scanned, parser, legacy = row
            ttl = TTL if page else NO_PAGE_TTL
            pages = json.loads(pages_json or "[]")
            if not force and now - scanned < ttl and parser == PARSER_VERSION:
                return
            if page and pages and not legacy:
                stored = self._stored_texts(pages)
                if stored is not None:
                    current = self.revids(pages)
                    with self.lock:
                        old = {t: r for t, r in self.db.execute(
                            "SELECT title, revid FROM wiki_pages WHERE title IN (%s)" % ",".join("?" * len(pages)), pages)}
                    if all(current.get(t) == old.get(t) for t in pages):
                        if parser != PARSER_VERSION:
                            self._save_local(series, page, stored)
                        else:
                            self._touch(series)
                        return
        self._full_scan(series)

    def _save_local(self, series, page, stored):
        claims = extract_claims(series, stored)
        with self.lock:
            self.db.execute("DELETE FROM wiki_claims WHERE series=?", (series,))
            for c in claims:
                self.db.execute("INSERT OR IGNORE INTO wiki_claims VALUES (?,?,?,?,?,?,?)",
                                (series, c["b"], c["bn"], c["season"], c["episode"], c["page"], c["line"]))
            self.db.execute("UPDATE wiki_series SET parser=?, scanned_at=? WHERE series=?", (PARSER_VERSION, time.time(), series))
            self.db.commit()

    def get(self, series):
        with self.lock:
            rows = self.db.execute("SELECT b, bn, season, episode, page, line FROM wiki_claims WHERE series=?", (series,)).fetchall()
        return {"claims": [{"b": r[0], "bn": r[1], "season": r[2], "episode": r[3], "page": r[4], "line": r[5]} for r in rows]} if rows else None

    def invalidate(self, series):
        with self.lock:
            self.db.execute("UPDATE wiki_series SET scanned_at=0 WHERE series=?", (series,))
            self.db.commit()

    def count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM wiki_series").fetchone()[0]

    def stats(self):
        with self.lock:
            return {"series": self.db.execute("SELECT COUNT(*) FROM wiki_series").fetchone()[0],
                    "pages": self.db.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0],
                    "claims": self.db.execute("SELECT COUNT(*) FROM wiki_claims").fetchone()[0]}
