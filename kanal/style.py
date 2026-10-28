"""Дизайн шортсов из assets/short_style.yaml; выбор владельца (state.short_style) перекрывает `active`."""
import yaml

from . import state
from .config import ROOT

FILE = ROOT / "assets" / "short_style.yaml"


def load() -> dict:
    with open(FILE) as f:
        return yaml.safe_load(f)


def active() -> tuple[str, dict, str]:
    """(ключ, стиль, общие правила)."""
    d = load()
    key = state.load().get("short_style") or d["active"]
    if key not in d["styles"]:
        key = d["active"]
    return key, d["styles"][key], d["rules"]


def image_style(st: dict, rules: str) -> str:
    return f"{st['prompt']}, {rules}"


def ass_color(rgb: str, alpha: str = "00") -> str:
    """'FFE500' → '&H0000E5FF' (ASS хранит AABBGGRR)."""
    return f"&H{alpha}{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}".upper()
