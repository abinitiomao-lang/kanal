"""Скачивает тексты из assets/classics.yaml с Project Gutenberg и чистит их → assets/classics/<id>.txt.

Формат результата: абзацы через пустую строку, заголовки глав — строки «## Заголовок».
Запуск: python scripts/fetch_classics.py [id ...]
"""
import re
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^(?:[IVXLC]+\.?|[A-Z][A-Z0-9 ,.;:'’\-—()]{2,70}|BOOK THE .*|PART [IVX]+\.?)$")


def clean(raw: str, start: str, end: str, nth: int = 1, hyphen: bool = False) -> str:
    """nth — какое по счёту совпадение start считать началом (первое часто стоит в оглавлении);
    hyphen — «--» между буквами это дефис (self--evident), а не тире."""
    if hyphen:
        raw = re.sub(r"(?<=[A-Za-z])--(?=[a-z])", "-", raw)
    lines = raw.replace("\r", "").split("\n")
    i = [n for n, l in enumerate(lines) if re.search(start, l.strip())][nth - 1]
    j = next(n for n, l in enumerate(lines[i + 1:], i + 1) if re.search(end, l.strip()))
    paras, cur = [], []
    for line in lines[i:j] + [""]:
        if line.strip():
            cur.append(line.strip())
            continue
        if cur:
            text = " ".join(cur)
            cur = []
            if text.startswith(("[Illustration", "[Footnote")):
                continue
            text = re.sub(r"\[\d+\]|\[Footnote[^\]]*\]", "", text)
            text = re.sub(r"_([^_]+)_", r"\1", text).replace("--", " — ")
            if HEADING.match(text):
                text = "## " + (text.rstrip(".") if re.fullmatch(r"[IVXLC]+\.?", text) else text.title())
            paras.append(text)
    return "\n\n".join(paras) + "\n"


def main(ids: list[str]) -> None:
    for c in yaml.safe_load((ROOT / "assets" / "classics.yaml").read_text()):
        if ids and c["id"] not in ids:
            continue
        url = f"https://www.gutenberg.org/cache/epub/{c['gutenberg']}/pg{c['gutenberg']}.txt"
        raw = urllib.request.urlopen(url, timeout=60).read().decode("utf-8-sig")
        text = clean(raw, c["start"], c["end"], c.get("start_nth", 1), c.get("hyphen", False))
        (ROOT / "assets" / "classics" / f"{c['id']}.txt").write_text(text)
        print(f"{c['id']}: {len(text.split())} words, {text.count('## ')} headings")


if __name__ == "__main__":
    main(sys.argv[1:])
