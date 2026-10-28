"""Проверка всех ключей прямо в Actions. Отчёт — в Telegram. Ничего не публикует."""
import os
import traceback

from . import telegram
from .config import workdir


def _anthropic():
    from . import llm
    r = llm.text("Reply with the single word OK.", max_tokens=200)
    return f"ответ «{r[:20]}»"


def _fal():
    from . import images
    lib = images.Library.__new__(images.Library)
    lib.new, lib.spent, lib.used, lib.index = [], 0.0, set(), []
    p = lib.generate("deep ocean twilight zone, bioluminescent jellyfish drifting in dark water, faint light "
                     "from the surface far above", "landscape", workdir("selftest") / "fal.jpg")
    telegram.send_photo(p, "🖼 Тест fal.ai — так будут выглядеть кадры длинных видео.")
    return "картинка отправлена"


def _eleven():
    from . import audio
    q = audio.eleven_quota()
    if not q:
        raise RuntimeError("не удалось прочитать подписку")
    return f"использовано {q[0]} из {q[1]} кредитов"


def _youtube():
    from . import youtube
    yt = youtube._yt()
    youtube.check_channel(yt)
    ch = yt.channels().list(part="snippet,status", mine=True).execute()["items"][0]
    return f"канал «{ch['snippet']['title']}»"


def _pexels():
    import requests
    r = requests.get("https://api.pexels.com/videos/search", params={"query": "ocean", "per_page": 1},
                     headers={"Authorization": os.environ["PEXELS_API_KEY"]}, timeout=30)
    r.raise_for_status()
    return "ok"


CHECKS = [("Anthropic", "ANTHROPIC_API_KEY", _anthropic), ("fal.ai", "FAL_KEY", _fal),
          ("ElevenLabs", "ELEVENLABS_API_KEY", _eleven), ("YouTube", "YT_REFRESH_TOKEN", _youtube),
          ("Pexels", "PEXELS_API_KEY", _pexels)]


def run() -> None:
    lines = ["🧪 <b>Проверка ключей</b>", "✅ Telegram: ok"]
    for name, env, fn in CHECKS:
        if not os.environ.get(env):
            lines.append(f"⚪️ {name}: ключ не задан")
            continue
        try:
            lines.append(f"✅ {name}: {fn()}")
        except Exception as e:
            traceback.print_exc()
            lines.append(f"❌ {name}: {str(e)[:200]}")
    telegram.send("\n".join(lines))
    print("\n".join(lines))
