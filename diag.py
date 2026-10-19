"""
Journal et diagnostic intégrés à l'appli : une fenêtre « Journal » affiche un rapport complet (état du serveur,
connexion à Sonarr, dernières lignes des journaux) à copier-coller directement, sans se connecter au serveur.

Les clés et mots de passe sont masqués automatiquement dans le rapport.
"""

import logging
import logging.handlers
import os
import platform
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from flask import jsonify, request

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_FILE = DATA_DIR / "mouflossover.log"

logger = logging.getLogger("mouflossover.diag")

_SECRET_PATTERNS = [
    (re.compile(r"(api_key=)[^&\s\"']+", re.I), r"\1***"),
    (re.compile(r"(X-Api-Key['\":= ]+)[A-Za-z0-9]+", re.I), r"\1***"),
    (re.compile(r"(SONARR_KEY=)\S+"), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1***"),
    (re.compile(r"(APP_PASSWORD_HASH=)\S+"), r"\1***"),
    (re.compile(r"(\b(?:password|passwd|mot_de_passe)=)[^&\s]+", re.I), r"\1***"),
]


def redact(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class _RedactFormatter(logging.Formatter):
    """Masque les clés dans TOUTES les lignes du journal, même dans un message d'erreur imprévu."""
    def format(self, record):
        return redact(super().format(record))


def setup_logging():
    """Journal détaillé dans la console ET dans data/mouflossover.log (tourne tout seul : 1 Mo x 3 fichiers)."""
    fmt = _RedactFormatter("%(asctime)s - %(levelname)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    if "unittest" in sys.modules:          # tests automatiques : jamais dans le vrai journal
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        root.warning(f"Journal fichier indisponible : {e}")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # pas une ligne par requête


def _tail(path: Path, lines: int, max_bytes: int = 200_000) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])
    except OSError:
        return "(aucun journal pour le moment)"


def _mem():
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
        return f"{info['MemAvailable'] // 1024} Mo libres sur {info['MemTotal'] // 1024} Mo"
    except Exception:
        return "inconnue"


def _disk(path):
    try:
        u = shutil.disk_usage(path)
        return f"{u.free / 1e9:.1f} Go libres sur {u.total / 1e9:.0f} Go"
    except OSError as e:
        return f"inaccessible ({e})"


def _run_cmd(cmd, timeout=8):
    import subprocess
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or out.stderr or "").strip() or "(vide)"
    except FileNotFoundError:
        return "(commande indisponible)"
    except subprocess.TimeoutExpired:
        return "(délai dépassé)"
    except Exception as e:
        return f"(erreur : {e})"


def system_sections(service):
    """Sections communes à toutes les applis : déploiement automatique, cron et journal système du service."""
    return [
        "",
        "--- Déploiement automatique (40 dernières lignes) ---",
        _tail(Path(f"/var/log/{service}-deploy.log"), 40),
        "",
        "--- Cron (20 dernières lignes) ---",
        _tail(Path(f"/var/log/{service}-cron.log"), 20),
        "",
        "--- Journal système du service (120 dernières lignes, erreurs Python comprises) ---",
        _run_cmd(["journalctl", "-u", service, "-n", "120", "--no-pager", "-o", "short-iso"]),
    ]


def build_report(version, series_dir, extra):
    """extra : lignes d'état propres à l'appli (Sonarr, crossovers, Wikipédia…)"""
    lines = [
        f"=== Rapport MouFlossover · {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ===",
        f"Version : {version}",
        f"Python  : {sys.version.split()[0]} · {platform.platform()} · {os.cpu_count()} cœurs",
        f"Mémoire : {_mem()}",
        f"Disque  : appli {_disk(BASE_DIR)} · séries {_disk(series_dir)}",
        "",
        "--- Réglages ---",
        f"Dossier des séries : {series_dir} → {'accessible' if Path(series_dir).is_dir() else 'INTROUVABLE'}"
        + (f" · écriture {'OK' if os.access(series_dir, os.W_OK) else 'IMPOSSIBLE'}" if Path(series_dir).is_dir() else ""),
    ]
    lines += list(extra or [])
    lines += [
        "",
        "--- Journal de l'appli (150 dernières lignes) ---",
        _tail(LOG_FILE, 150),
    ]
    lines += system_sections("mouflossover")
    return redact("\n".join(lines))


def init_app(app, version, series_dir_fn, extra_fn=None):
    logger.info(f"Démarrage MouFlossover {version} · Python {sys.version.split()[0]}")

    @app.route("/api/diagnostic")
    def api_diagnostic():
        report = build_report(version, series_dir_fn(), extra_fn() if extra_fn else [])
        return jsonify({"report": report})

    @app.route("/api/clientlog", methods=["POST"])
    def api_clientlog():
        """Erreurs JavaScript du navigateur, pour qu'elles apparaissent aussi dans le journal."""
        data = request.get_json(silent=True) or {}
        msg = " ".join(str(data.get("message", "")).split())[:400]
        where = " ".join(str(data.get("where", "")).split())[:200]
        logger.error(f"[navigateur] {msg} ({where})")
        return jsonify({"ok": True})

    @app.errorhandler(Exception)
    def on_error(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return exc
        logger.exception(f"Erreur non gérée sur {request.method} {request.path}")
        if request.path.startswith("/api/"):
            return jsonify({"error": f"Erreur interne : {type(exc).__name__}: {exc} (détails dans le Journal)"}), 500
        raise exc
