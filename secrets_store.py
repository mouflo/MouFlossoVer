"""Écriture d'un réglage dans data/secrets.env (jamais envoyé sur GitHub), droits 600."""
import os
from pathlib import Path


def write_secret(file: Path, name: str, value: str) -> None:
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    lines = file.read_text(encoding="utf-8").splitlines() if file.exists() else []
    lines = [l for l in lines if not l.startswith(name + "=")]
    lines.append(f'{name}="{value}"')
    tmp = file.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, file)
