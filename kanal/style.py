"""Дизайн шортсов из assets/short_style.yaml: форматы tale / series, чередуются."""
import yaml

from .config import ROOT

FILE = ROOT / "assets" / "short_style.yaml"


def load() -> dict:
    with open(FILE) as f:
        return yaml.safe_load(f)


def get(key: str | None) -> tuple[str, dict]:
    """(ключ, формат); в image уже дописана общая рисовка art."""
    d = load()
    if key not in d["formats"]:
        key = d["rotation"][0]
    fmt = dict(d["formats"][key])
    fmt["image"] = f"{fmt['image']}, {d['art']}"
    return key, fmt


def for_book(book: str | None) -> str | None:
    return load()["book_formats"].get(book) if book else None


def rotate(n: int) -> str:
    """Формат для n-го шортса по порядку."""
    r = load()["rotation"]
    return r[n % len(r)]


def ass_color(rgb: str, alpha: str = "00") -> str:
    """'FFE500' → '&H0000E5FF' (ASS хранит AABBGGRR)."""
    return f"&H{alpha}{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}".upper()


def rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
