"""Рендер через ffmpeg.

Длинное видео: каждая картинка → сегмент с медленным Ken Burns, дрейфом звёзд, виньеткой и затемнением
в начале/конце (мягкий «вдох» между сценами). Сегменты одной главы склеиваются без перекодирования;
главы рендерятся параллельными job'ами и склеиваются в конце, аудио сводится целиком (assemble_long).

Шортс: быстрые жёсткие склейки, резкий зум/тряска, караоке-субтитры по словам, хук на первых 1.5 с.
"""
import os
import random
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import CFG, ROOT

FF = os.environ.get("FFMPEG", "ffmpeg")
FONTS = ROOT / "assets" / "fonts"


def ff(*args: str) -> None:
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", *args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError(f"ffmpeg failed: {res.stderr[-2000:]}")


# ---------------- длинное видео ----------------

def stars_layer(out: Path, w: int, h: int, seed: int = 7) -> Path:
    """Прозрачный слой звёзд/пылинок двойной ширины (для бесшовного скролла)."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGBA", (w * 2, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for _ in range(int(w * h / 9000)):
        x, y = rng.integers(0, w), rng.integers(0, h)
        r = rng.choice([1, 1, 1, 2, 2, 3])
        a = int(rng.integers(40, 150))
        col = (255, 236, 200, a) if rng.random() < 0.5 else (200, 220, 255, a)
        for dx in (0, w):  # дублируем → петля без шва
            d.ellipse([x + dx - r, y - r, x + dx + r, y + r], fill=col)
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    img.save(out)
    return out


def long_segment(image: Path, out: Path, frames: int, idx: int, stars: Path) -> None:
    L = CFG["long"]
    w, h, fps = L["width"], L["height"], L["fps"]
    rnd = random.Random(idx)
    zoom_in = idx % 2 == 0
    z = f"1.0+0.10*on/{frames}" if zoom_in else f"1.10-0.10*on/{frames}"
    # медленный дрейф центра кадра по горизонтали/вертикали
    px, py = rnd.choice([-1, 1]) * rnd.uniform(0.02, 0.06), rnd.uniform(-0.03, 0.03)
    x = f"(iw-iw/zoom)/2 + iw*{px:.3f}*(on/{frames}-0.5)"
    y = f"(ih-ih/zoom)/2 + ih*{py:.3f}*(on/{frames}-0.5)"
    fade = min(int(fps * 2.5), frames // 4)
    speed = rnd.choice([3, 4, 5])
    graph = (
        f"[0:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase:flags=lanczos,crop={w * 2}:{h * 2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
        "eq=brightness=-0.04:saturation=0.85,colorbalance=bs=0.06:rs=0.03:rh=0.04,"
        "vignette=PI/4.5,format=yuv420p[bg];"
        f"[1:v]format=rgba[st];"
        f"[bg][st]overlay=x='-mod(t*{speed},{w})':y=0:shortest=1,"
        f"fade=t=in:st=0:n={fade},fade=t=out:start_frame={frames - fade}:n={fade},format=yuv420p[v]"
    )
    ff("-i", str(image), "-loop", "1", "-framerate", str(fps), "-i", str(stars),
       "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames),
       "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
       "-b:v", L["video_bitrate"], "-maxrate", "1600k", "-bufsize", "3200k",
       "-g", str(fps * 10), "-r", str(fps), str(out))


def concat(parts: list[Path], out: Path, extra: list[str] | None = None) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", *(extra or []), str(out))


def long_chapter_video(images: list[Path], seconds: float, out: Path, work: Path, seed: int) -> None:
    L = CFG["long"]
    fps = L["fps"]
    total = round(seconds * fps)
    stars = stars_layer(work / "stars.png", L["width"], L["height"], seed)
    per = total // len(images)
    parts = []
    for i, img in enumerate(images):
        frames = per if i < len(images) - 1 else total - per * (len(images) - 1)
        part = work / f"seg_{i:02d}.mp4"
        long_segment(img, part, frames, seed * 100 + i, stars)
        parts.append(part)
    concat(parts, out)


def assemble_long(chapter_videos: list[Path], chapter_audio: list[Path], ambient: Path, out: Path,
                  work: Path) -> None:
    """Склейка глав без перекодирования видео + сведение голоса с эмбиентом и затуханием в конце."""
    L = CFG["long"]
    video = work / "video_only.mp4"
    concat(chapter_videos, video)
    voice = work / "voice_all.flac"
    concat(chapter_audio, voice)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(video)], capture_output=True, text=True).stdout)
    fo = L["fade_out_seconds"]
    graph = (
        f"[1:a]aresample=48000,pan=stereo|c0=c0|c1=c0,acompressor=threshold=-20dB:ratio=2.5,"
        f"highshelf=f=6000:g=-4[vo];"
        f"[2:a]volume={L['ambient_db']}dB[amb];"
        f"[vo][amb]amix=inputs=2:duration=first:normalize=0,"
        f"afade=t=out:st={dur - fo:.2f}:d={fo},loudnorm=I=-20:TP=-3:LRA=11[a]"
    )
    ff("-i", str(video), "-i", str(voice), "-stream_loop", "-1", "-i", str(ambient),
       "-filter_complex", graph, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
       "-c:a", "aac", "-b:a", L["audio_bitrate"], "-t", f"{dur:.3f}", "-movflags", "+faststart", str(out))


def pad_audio(src: Path, seconds: float, out: Path) -> None:
    """Подгоняет аудио главы ровно под длину видео главы (кадровая точность)."""
    ff("-i", str(src), "-af", f"apad=whole_dur={seconds:.4f}", "-t", f"{seconds:.4f}",
       "-ar", "24000", "-ac", "1", str(out))


# ---------------- шортс ----------------

def short_segment(src: Path, out: Path, seconds: float, idx: int, is_video: bool) -> None:
    S = CFG["shorts"]
    w, h, fps = S["width"], S["height"], S["fps"]
    frames = max(1, round(seconds * fps))
    rnd = random.Random(idx)
    fill = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    grade = "eq=contrast=1.08:saturation=1.15"
    if is_video:
        vf = f"{fill},{grade},fps={fps},format=yuv420p"
        args = ["-i", str(src), "-vf", vf]
    else:
        zin = rnd.random() < 0.7
        z = f"1.0+0.22*on/{frames}" if zin else f"1.22-0.22*on/{frames}"
        shake = rnd.random() < 0.35
        sx = "+12*sin(on*1.9)" if shake else ""
        sy = "+9*cos(on*2.3)" if shake else ""
        vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
              f"zoompan=z='{z}':x='(iw-iw/zoom)/2{sx}':y='(ih-ih/zoom)/2{sy}':d={frames}:s={w}x{h}:fps={fps},"
              f"{grade},format=yuv420p")
        args = ["-i", str(src), "-vf", vf]
    ff(*args, "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
       "-r", str(fps), "-g", str(fps), str(out))


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def karaoke_ass(words: list[dict], hook: str, out: Path) -> Path:
    S = CFG["shorts"]
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {S['width']}
PlayResY: {S['height']}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Anton,118,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1
Style: Hook,Anton,104,&H0000E5FF,&H00FFFFFF,&H00000000,&HC8000000,0,0,0,0,100,100,1,0,3,18,0,8,70,70,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 1,{_ass_time(0)},{_ass_time(1.6)},Hook,,0,0,0,,{{\\fad(0,150)}}{hook.upper()}"]
    # группы по 1–3 слова, разрыв на пунктуации
    groups, cur = [], []
    for wd in words:
        cur.append(wd)
        if len(cur) == 3 or wd["word"][-1] in ".,!?;:" or len(wd["word"]) > 9:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    pos = f"{{\\pos({S['width'] // 2},{int(S['height'] * 0.66)})}}"
    for g in groups:
        g_end = g[-1]["end"] + 0.08
        for i, wd in enumerate(g):
            start = wd["start"]
            end = g[i + 1]["start"] if i + 1 < len(g) else g_end
            parts = []
            for j, other in enumerate(g):
                txt = other["word"].upper().replace("{", "").replace("}", "")
                parts.append(f"{{\\c&H0000E5FF&}}{txt}{{\\c&H00FFFFFF&}}" if j == i else txt)
            pop = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Word,,0,0,0,,{pos}{pop}{' '.join(parts)}")
    out.write_text(head + "\n".join(lines) + "\n")
    return out


def assemble_short(segments: list[Path], cut_times: list[float], voice: Path, ass: Path, ambient: Path,
                   whoosh: Path, out: Path, work: Path) -> None:
    S = CFG["shorts"]
    video = work / "short_video.mp4"
    concat(segments, video)
    n = len(cut_times)
    graph = [f"[0:v]subtitles='{ass}':fontsdir='{FONTS}',format=yuv420p[v]",
             "[1:a]aresample=48000,pan=stereo|c0=c0|c1=c0,loudnorm=I=-14:TP=-1.5[vo]",
             "[2:a]volume=-24dB[amb]"]
    mix = ["[vo]", "[amb]"]
    if n:
        graph.append(f"[3:a]volume=-6dB,asplit={n}" + "".join(f"[w{i}]" for i in range(n)))
        for i, t in enumerate(cut_times):
            ms = max(0, int((t - 0.15) * 1000))
            graph.append(f"[w{i}]adelay={ms}|{ms}[wd{i}]")
            mix.append(f"[wd{i}]")
    graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95[a]")
    ff("-i", str(video), "-i", str(voice), "-stream_loop", "-1", "-i", str(ambient), "-i", str(whoosh),
       "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
       "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-r", str(S["fps"]),
       "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out))


# ---------------- превью ----------------

def thumbnail(image: Path, text: str, badge: str, out: Path) -> Path:
    img = Image.open(image).convert("RGB").resize((1280, 720), Image.LANCZOS)
    grad = Image.new("L", (1280, 720))
    gd = ImageDraw.Draw(grad)
    for x in range(1280):
        gd.line([(x, 0), (x, 720)], fill=int(190 * max(0, 1 - x / 900)))
    dark = Image.new("RGB", img.size, (8, 12, 32))
    img = Image.composite(dark, img, grad)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONTS / "Lora.ttf"), 92)
    try:
        font.set_variation_by_name("Bold")
    except Exception:
        pass
    small = ImageFont.truetype(str(FONTS / "Anton-Regular.ttf"), 46)
    words, lines, cur = text.upper().split(), [], ""
    for wd in words:
        if cur and d.textlength(f"{cur} {wd}", font=font) > 700:
            lines.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    lines.append(cur)
    y = 360 - len(lines) * 55
    for ln in lines:
        d.text((64, y), ln, font=font, fill=(255, 226, 170), stroke_width=3, stroke_fill=(10, 14, 40))
        y += 110
    d.rounded_rectangle([64, 600, 64 + d.textlength(badge, font=small) + 40, 670], 14, fill=(20, 30, 70))
    d.text((84, 606), badge, font=small, fill=(200, 215, 255))
    img.save(out, "JPEG", quality=90)
    return out
