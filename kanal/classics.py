"""«Classics to Fall Asleep To»: дословное чтение книг в общественном достоянии (assets/classics.yaml).

Текст не пишет LLM: части и главы режутся из assets/classics/<id>.txt, модель делает только метаданные.
"""
import math
import re

import yaml

from .config import CFG, ROOT

FREQ = "Classics to Fall Asleep To"


def load() -> list[dict]:
    with open(ROOT / "assets" / "classics.yaml") as f:
        return yaml.safe_load(f)


def get(cid: str) -> dict:
    return next(c for c in load() if c["id"] == cid)


def _paras(cid: str) -> list[str]:
    return [p for p in (ROOT / "assets" / "classics" / f"{cid}.txt").read_text().split("\n\n") if p.strip()]


def _words(p: str) -> int:
    return len(p.split())


def _split(paras: list[str], n: int, prefer_heading: bool) -> list[list[str]]:
    """n кусков примерно равной длины; разрез — по ближайшему заголовку, если он недалеко."""
    total = sum(map(_words, paras))
    cuts, acc, k = [], 0, 1
    for i, p in enumerate(paras):
        if k < n and acc >= total * k / n:
            j = i
            if prefer_heading:  # ищем заголовок в пределах ~8% длины от точки разреза
                window, w = total * 0.08, 0
                for d in range(i, len(paras)):
                    if paras[d].startswith("## "):
                        j = d
                        break
                    w += _words(paras[d])
                    if w > window:
                        break
            if not cuts or j > cuts[-1]:
                cuts.append(j)
            k += 1
        acc += _words(p)
    bounds = [0] + cuts + [len(paras)]
    return [paras[a:b] for a, b in zip(bounds, bounds[1:]) if b > a]


def parts(cid: str) -> list[list[str]]:
    paras = _paras(cid)
    target = CFG["long"]["target_minutes"] * CFG["long"]["words_per_minute"]
    return _split(paras, max(1, round(sum(map(_words, paras)) / target)), True)


def _spoken(p: str) -> str | None:
    """Заголовок читается отдельной фразой; голые римские номера глав не читаем."""
    if not p.startswith("## "):
        return p
    h = p[3:].strip().rstrip(".")
    return None if re.fullmatch(r"[IVXLC]+", h) else h + "."


def chapters(cid: str, n: int) -> list[dict]:
    """Главы для озвучки части n (1-based): ~CFG.long.chapters кусков + приветствие и прощание ведущего."""
    c, ps = get(cid), parts(cid)
    part, total = ps[n - 1], len(ps)
    chunks = _split(part, CFG["long"]["chapters"], True)
    of = f", part {n} of {total}" if total > 1 else ""
    intro = (f"{CFG['channel']['greeting']} Tonight we read {c['title']} by {c['author']}{of}"
             + (", picking up where we left off" if n > 1 else "")
             + ". The words are the author's own; just get comfortable, let your breathing slow down, "
               "and let the story carry you.")
    outro = ((f"Here we pause our reading of {c['title']}. Next time, we continue with part {n + 1}. "
              if n < total else f"And so ends {c['title']} by {c['author']}. ")
             + "Thank you for listening to Night Nerd Radio. Sleep well.")
    out = []
    for i, ch in enumerate(chunks):
        heads = [p[3:].strip().rstrip(".") for p in ch if p.startswith("## ")
                 and not re.fullmatch(r"[IVXLC]+\.?", p[3:].strip())]
        text = "\n\n".join(s for s in map(_spoken, ch) if s)
        if i == 0:
            text = intro + "\n\n" + text
        if i == len(chunks) - 1:
            text += "\n\n" + outro
        out.append({"title": heads[0] if heads else f"{c['title']} — {i + 1}", "text": text})
    return out


def next_parts(done: set[tuple[str, int]], limit: int = 1) -> list[dict]:
    out = []
    for c in load():
        total = len(parts(c["id"]))
        n = next((k for k in range(1, total + 1) if (f"classic:{c['id']}", k) not in done), None)
        if n:
            of = f", Part {n} of {total}" if total > 1 else ""
            out.append({"topic": f"{c['title']} by {c['author']}{of}", "subject": "literature", "frequency": FREQ,
                        "pitch": "дословное чтение классики (общественное достояние)", "source": "",
                        "series": f"classic:{c['id']}", "part": n,
                        "button": f"📜 {c['title']}{f' {n}/{total}' if total > 1 else ''}"})
    return out[:limit]
