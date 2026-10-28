import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("KANAL_WORK", ROOT / "work"))


def load() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


CFG = load()


def secret(name: str, required: bool = True) -> str | None:
    """Секреты берутся только из окружения (GitHub Secrets). Никогда не логируем значения."""
    val = os.environ.get(name)
    if required and not val:
        raise RuntimeError(f"Не задан секрет {name} (добавь в GitHub → Settings → Secrets → Actions)")
    return val


def workdir(*parts: str) -> Path:
    p = WORK.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p
