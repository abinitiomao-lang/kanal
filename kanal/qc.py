"""Контроль качества готового шортса до отправки владельцу: техника (ffmpeg) + критик по стоп-кадрам (vision)."""
import base64
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from . import llm
from .config import CFG


def _ff(*args) -> str:
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True).stderr


def technical(video: Path) -> list[str]:
    """Разрешение, длительность, громкость (LUFS), чёрные/застывшие куски. Возвращает список проблем."""
    S = CFG["shorts"]
    probs = []
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height:format=duration", "-of", "default=nw=1", str(video)],
                          capture_output=True, text=True).stdout
    vals = dict(ln.split("=", 1) for ln in info.split() if "=" in ln)
    if (int(vals.get("width", 0)), int(vals.get("height", 0))) != (S["width"], S["height"]):
        probs.append(f"разрешение {vals.get('width')}×{vals.get('height')}")
    dur = float(vals.get("duration", 0))
    if not S["min_seconds"] <= dur <= S["max_seconds"] + 1:
        probs.append(f"длительность {dur:.1f} с вне {S['min_seconds']}–{S['max_seconds']} с")
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", _ff("-i", str(video), "-af", "ebur128", "-f", "null", "-"))
    if m and not -17 <= float(m[-1]) <= -11:
        probs.append(f"громкость {float(m[-1]):.1f} LUFS (норма −14)")
    log = _ff("-i", str(video), "-vf", "blackdetect=d=0.6:pix_th=0.08,freezedetect=n=0.002:d=2.5",
              "-an", "-f", "null", "-")
    if "black_start" in log:
        probs.append("есть чёрный кусок ≥0.6 с")
    if "freeze_start" in log:
        probs.append("картинка застывает ≥2.5 с")
    return probs


class Verdict(BaseModel):
    ok: bool = Field(description="true if a viewer would see nothing wrong")
    issues: list[str] = Field(description="concrete problems in Russian, empty if ok")


def frames(video: Path, times: list[float], work: Path) -> list[Path]:
    out = []
    for i, t in enumerate(times):
        p = work / f"qc_{i}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=540:-1", "-q:v", "4", str(p)], check=False)
        if p.exists():
            out.append(p)
    return out


def visual(video: Path, ru_events: list[tuple[float, float, str]], work: Path, fmt_label: str) -> Verdict:
    """Критик смотрит 4 кадра в моменты русских титров: читаемость, перекрытия, грамотность, артефакты."""
    picks = ru_events[:: max(1, len(ru_events) // 4)][:4] if ru_events else []
    times = [(a + b) / 2 for a, b, _ in picks] or [1.0, 5.0, 10.0]
    imgs = frames(video, times, work)
    expected = "\n".join(f"- {t}" for _, _, t in picks)
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(p.read_bytes()).decode()}} for p in imgs]
    content.append({"type": "text", "text": (
        f"Это стоп-кадры вертикального YouTube Shorts (формат «{fmt_label}»): рисованная иллюстрация, "
        "английские субтитры и русские субтитры с розовым неоновым свечением. Проверь как строгий редактор: "
        "читаются ли оба вида субтитров, не налезают ли они друг на друга, на важную часть картинки или в "
        "нижнюю зону интерфейса Shorts, нет ли обрезанных строк; грамотен ли русский текст (орфография, "
        "пунктуация, латиница); нет ли на картинке мусорного текста, уродливых рук или лиц, чёрных кадров. "
        "Учти задуманное: русский титр — часть предложения, разбитого на строки, поэтому продолжение со "
        "строчной буквы и без точки — норма; английские субтитры в формате «Сказка» идут по одному слову "
        "и не обязаны совпадать с русской строкой. Субтитры поверх рисунка без подложки — задуманный стиль; "
        "отмечай только если текст реально нечитаем. Перечисляй только то, что зритель заметит. "
        + (f"Ожидаемые русские титры на этих кадрах:\n{expected}" if expected else ""))})
    return llm.json_content(content, Verdict, role="checker")


def report(video: Path, ru_events, work: Path, fmt_label: str) -> str:
    try:
        tech = technical(video)
    except Exception as e:
        tech = [f"проверка не прошла: {e}"]
    try:
        v = visual(video, ru_events or [], work, fmt_label)
        vis = [] if v.ok else v.issues
    except Exception as e:
        vis = [f"критик недоступен: {str(e)[:120]}"]
    if not tech and not vis:
        return "🧪 Контроль качества: ✅ техника и кадры в порядке"
    return "🧪 Контроль качества:\n" + "\n".join(f"⚠️ {p}" for p in tech + vis)
