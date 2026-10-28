"""Озвучка (Kokoro для длинных, ElevenLabs для шортсов) и генерируемый эмбиент (свой, без лицензий)."""
import base64
import re
import subprocess
import urllib.request
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

from .config import CFG, ROOT, secret

KOKORO_DIR = ROOT / "models"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
SR = 24000

_kokoro = None


def _kokoro_model():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        KOKORO_DIR.mkdir(exist_ok=True)
        for name in KOKORO_FILES:
            if not (KOKORO_DIR / name).exists():
                urllib.request.urlretrieve(KOKORO_URL + name, KOKORO_DIR / name)
        _kokoro = Kokoro(str(KOKORO_DIR / KOKORO_FILES[0]), str(KOKORO_DIR / KOKORO_FILES[1]))
    return _kokoro


def _chunks(paragraph: str, limit: int = 350) -> list[str]:
    sentences = re.split(r"(?<=[.!?;])\s+", paragraph.strip())
    out, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def kokoro(text: str, out: Path) -> float:
    """Синтез длинного текста. Паузы: 0.45 c между фразами, 1.4 c между абзацами. Возвращает длительность."""
    L = CFG["long"]
    model = _kokoro_model()
    pieces = []
    for para in [p for p in text.split("\n") if p.strip()]:
        for chunk in _chunks(para):
            samples, sr = model.create(chunk, voice=L["kokoro_voice"], speed=L["kokoro_speed"], lang="en-us")
            pieces += [samples, np.zeros(int(sr * 0.45), dtype=np.float32)]
        pieces.append(np.zeros(int(SR * 1.0), dtype=np.float32))
    audio = np.concatenate(pieces)
    sf.write(out, audio, SR)
    return len(audio) / SR


def eleven(text: str, out: Path) -> tuple[float, list[dict]]:
    """ElevenLabs Flash с таймингами. Возвращает (длительность, [{word,start,end}])."""
    S = CFG["shorts"]
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{S['eleven_voice_id']}/with-timestamps",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": secret("ELEVENLABS_API_KEY")},
        json={"text": text, "model_id": S["eleven_model"],
              "voice_settings": {"stability": 0.35, "similarity_boost": 0.75, "style": 0.35, "speed": 1.08}},
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
    data = r.json()
    out.write_bytes(base64.b64decode(data["audio_base64"]))
    al = data["alignment"]
    words, cur, start, prev_end = [], "", None, 0.0
    for ch, t0, t1 in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": start, "end": prev_end})
            cur, start = "", None
            continue
        if start is None:
            start = t0
        cur += ch
        prev_end = t1
    if cur:
        words.append({"word": cur, "start": start, "end": prev_end})
    return probe_duration(out), words


def eleven_quota() -> tuple[int, int] | None:
    try:
        r = requests.get("https://api.elevenlabs.io/v1/user/subscription",
                         headers={"xi-api-key": secret("ELEVENLABS_API_KEY")}, timeout=30)
        d = r.json()
        return d["character_count"], d["character_limit"]
    except Exception:
        return None


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def ambient(out: Path, seconds: float = 120, seed: int = 0, sr: int = 48000) -> Path:
    """Бесшовная петля: мягкий брауновский шум + медленно дышащий аккорд. Полностью сгенерирована."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    # brown noise, сглаженный
    brown = np.cumsum(rng.standard_normal(n)).astype(np.float64)
    brown -= np.convolve(brown, np.ones(4801) / 4801, mode="same")
    brown /= np.max(np.abs(brown)) + 1e-9
    # пэд: частоты кратны 1/seconds → петля бесшовная
    base = rng.choice([110.0, 98.0, 116.54, 103.83])
    chord = [1, 1.5, 2, 2.5198, 3]  # квинта/октава/терция — мягко
    pad = np.zeros(n)
    for i, k in enumerate(chord):
        f = round(base * k * seconds) / seconds
        lfo = 0.55 + 0.45 * np.sin(2 * np.pi * (i + 1) / seconds * t + rng.uniform(0, 6.28))
        pad += np.sin(2 * np.pi * f * t) * lfo / (i + 1.5)
    pad /= np.max(np.abs(pad)) + 1e-9
    mono = 0.55 * pad + 0.45 * brown
    # плавная склейка конца с началом
    fade = int(sr * 3)
    ramp = np.linspace(0, 1, fade)
    mono[:fade] = mono[:fade] * ramp + mono[-fade:] * (1 - ramp)
    mono = mono[:-fade]
    stereo = np.stack([mono, np.roll(mono, int(sr * 0.013))], axis=1) * 0.5
    sf.write(out, stereo.astype(np.float32), sr)
    return out


def whoosh(out: Path, sr: int = 48000) -> Path:
    """Короткий свуш для склеек в шортсах (фильтрованный шум)."""
    n = int(0.35 * sr)
    rng = np.random.default_rng(1)
    noise = rng.standard_normal(n)
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    kernel = np.ones(12) / 12
    sig = np.convolve(noise, kernel, mode="same") * env * 0.25
    sf.write(out, np.stack([sig, sig], axis=1).astype(np.float32), sr)
    return out
