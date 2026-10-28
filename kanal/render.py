"""Рендер через ffmpeg.

Длинное видео: одна анимированная петля (10 с, бесшовная) повторяется на всю длину озвучки —
перекодируются только вступление и концовка, поэтому 3 часа собираются за минуты.

Шортс: быстрые жёсткие склейки, резкий зум/тряска, караоке-субтитры по словам, хук на первых 1.5 с.
"""
import math
import os
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import CFG, ROOT

FF = os.environ.get("FFMPEG", "ffmpeg")
FONTS = ROOT / "assets" / "fonts"


def ff(*args: str) -> None:
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", *args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError(f"ffmpeg failed: {res.stderr[-2000:]}")


# ---------------- длинное видео ----------------

def _long_enc() -> list[str]:
    """Одинаковые параметры кодирования для всех кусков → склейка без перекодирования."""
    L = CFG["long"]
    return ["-c:v", "libx264", "-preset", "slow", "-b:v", L["video_bitrate"], "-maxrate", "1800k",
            "-bufsize", "3600k", "-pix_fmt", "yuv420p", "-r", str(L["fps"]), "-video_track_timescale", "24000",
            "-an"]


def prepare_loop(src: Path, out: Path, is_video: bool) -> float:
    """Нормализует петлю под 1080p/24fps с мягкой цветокоррекцией и виньеткой. Возвращает длину, с.

    is_video=False — запасной вариант без анимации: картинка медленно «дышит» (периодический зум,
    поэтому петля тоже бесшовная)."""
    L = CFG["long"]
    w, h, fps = L["width"], L["height"], L["fps"]
    grade = "eq=brightness=-0.03:saturation=0.9,colorbalance=bs=0.05:rs=0.02,vignette=PI/5"
    if is_video:
        seconds = round(float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True).stdout))
        frames = seconds * fps
        ff("-i", str(src), "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
           f"crop={w}:{h},fps={fps},{grade}", "-frames:v", str(frames), "-g", str(frames), *_long_enc(), str(out))
    else:
        seconds = 30
        frames = seconds * fps
        z = f"1.06+0.04*sin(2*PI*on/{frames})"
        ff("-loop", "1", "-i", str(src), "-vf",
           f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
           f"zoompan=z='{z}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=1:s={w}x{h}:fps={fps},{grade}",
           "-frames:v", str(frames), "-g", str(frames), *_long_enc(), str(out))
    return float(seconds)


def concat(parts: list[Path], out: Path, extra: list[str] | None = None) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", *(extra or []), str(out))


def pad_audio(src: Path, seconds: float, out: Path) -> None:
    ff("-i", str(src), "-af", f"apad=whole_dur={seconds:.4f}", "-t", f"{seconds:.4f}",
       "-ar", "24000", "-ac", "1", str(out))


def music_bed(tracks: list[Path], out: Path) -> Path:
    """Треки по очереди, у каждого мягкое появление и затухание → бесшовно зацикливается."""
    parts = []
    for i, t in enumerate(tracks):
        d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  str(t)], capture_output=True, text=True).stdout)
        p = out.with_name(f"bed_{i}.wav")
        ff("-i", str(t), "-af", f"aresample=48000,afade=t=in:d=6,afade=t=out:st={d - 10:.2f}:d=10,"
           "aformat=channel_layouts=stereo", str(p))
        parts.append(p)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    ff("-f", "concat", "-safe", "0", "-i", str(lst), str(out))
    return out


def assemble_long(loop: Path, loop_len: float, chapter_audio: list[Path], ambient: Path, out: Path,
                  work: Path, bed_db: float | None = None) -> float:
    """ambient — музыкальная подложка (music_bed) или запасной эмбиент; bed_db — её громкость."""
    """Петля повторяется на всю длину озвучки. Перекодируются только вступление (проявление из темноты)
    и концовка (затухание), остальное — копия. Голос сводится с эмбиентом. Возвращает длительность."""
    L = CFG["long"]
    voice = work / "voice_all.flac"
    lst = work / "voice_all.txt"  # FLAC перекодируем: при copy в заголовке остаётся длина первой главы
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in chapter_audio))
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "flac", str(voice))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(voice)], capture_output=True, text=True).stdout) + 4.0
    fo = L["fade_out_seconds"]
    n = math.ceil(dur / loop_len)
    k_out = min(n - 1, math.ceil((fo + 2) / loop_len))
    intro = work / "intro.mp4"
    ff("-i", str(loop), "-vf", "fade=t=in:st=0:d=4", *_long_enc(), "-g", str(int(loop_len * L["fps"])), str(intro))
    outro_raw = work / "outro_raw.mp4"
    concat([loop] * k_out, outro_raw)
    t0 = (n - k_out) * loop_len
    fade_end = dur - t0
    outro = work / "outro.mp4"
    ff("-i", str(outro_raw), "-vf", f"fade=t=out:st={max(0.0, fade_end - fo):.2f}:d={fo}",
       *_long_enc(), "-g", str(int(loop_len * L["fps"])), str(outro))
    video = work / "video_only.mp4"
    concat([intro] + [loop] * (n - 1 - k_out) + [outro], video)
    graph = (
        "[1:a]aresample=48000,pan=stereo|c0=c0|c1=c0,acompressor=threshold=-20dB:ratio=2.5,"
        "highshelf=f=6000:g=-4,apad[vo];"
        f"[2:a]aresample=48000,volume={L['ambient_db'] if bed_db is None else bed_db}dB[amb];"
        "[vo][amb]amix=inputs=2:duration=first:normalize=0,"
        f"afade=t=out:st={dur - fo:.2f}:d={fo},loudnorm=I=-20:TP=-3:LRA=11[a]"
    )
    ff("-i", str(video), "-i", str(voice), "-stream_loop", "-1", "-i", str(ambient),
       "-filter_complex", graph, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
       "-c:a", "aac", "-b:a", L["audio_bitrate"], "-t", f"{dur:.3f}", "-movflags", "+faststart", str(out))
    return dur


# ---------------- шортс ----------------

def short_segment(src: Path, out: Path, seconds: float, idx: int, is_video: bool,
                  size: tuple[int, int] | None = None, motion: str = "punchy") -> None:
    """motion: punchy — резкий зум и тряска; slow — медленный наезд/отъезд без тряски."""
    S = CFG["shorts"]
    fps = S["fps"]
    w, h = size or (S["width"], S["height"])
    frames = max(1, round(seconds * fps))
    rnd = random.Random(idx)
    fill = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    grade = "eq=contrast=1.08:saturation=1.15"
    if is_video:
        vf = f"{fill},{grade},fps={fps},format=yuv420p"
        args = ["-i", str(src), "-vf", vf]
    else:
        zin = rnd.random() < 0.7
        amp = 0.22 if motion == "punchy" else 0.08
        z = f"1.0+{amp}*on/{frames}" if zin else f"{1 + amp}-{amp}*on/{frames}"
        shake = motion == "punchy" and rnd.random() < 0.35
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


def _ass_head(styles: list[str]) -> str:
    S = CFG["shorts"]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {S['width']}
PlayResY: {S['height']}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
""" + "\n".join(styles) + """

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _clean(w: str) -> str:
    return w.replace("{", "").replace("}", "")


def karaoke_ass(words: list[dict], hook: str, out: Path, subs: dict | None = None) -> Path:
    """Субтитры шортса. subs (assets/short_style.yaml): mode word — по слову, мелко; phrase — фраза под панелью.
    Без subs — старые крупные караоке-слова с подсветкой."""
    from .style import ass_color
    S = CFG["shorts"]
    W, H = S["width"], S["height"]
    if subs and subs["mode"] in ("word", "phrase"):
        c, ol = ass_color(subs["color"]), ass_color(subs["outline"])
        lines = []
        if subs["mode"] == "word":
            head = _ass_head([f"Style: Sub,{subs['font_name']},{subs['size']},{c},{c},{ol},&H64000000,"
                              "0,0,0,0,100,100,1,0,1,3,2,5,60,60,0,1"])
            pos = f"{{\\pos({W // 2},{int(H * subs['y'])})\\fad(60,60)}}"
            for i, wd in enumerate(words):
                end = words[i + 1]["start"] if i + 1 < len(words) else wd["end"] + 0.4
                lines.append(f"Dialogue: 0,{_ass_time(wd['start'])},{_ass_time(max(end, wd['start'] + 0.15))},"
                             f"Sub,,0,0,0,,{pos}{_clean(wd['word'])}")
        else:
            head = _ass_head([f"Style: Sub,{subs['font_name']},{subs['size']},{c},{c},{ol},&H00000000,"
                              f"0,0,0,0,100,100,0,0,1,2,0,8,90,90,{subs['y']},1"])
            groups, cur = [], []
            for wd in words:
                cur.append(wd)
                if len(cur) >= 7 or (len(cur) >= 3 and wd["word"][-1] in ".,!?;:—"):
                    groups.append(cur)
                    cur = []
            if cur:
                groups.append(cur)
            for gi, g in enumerate(groups):
                end = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else g[-1]["end"] + 0.5
                text = " ".join(_clean(w["word"]) for w in g)
                lines.append(f"Dialogue: 0,{_ass_time(g[0]['start'])},{_ass_time(end)},Sub,,0,0,0,,"
                             f"{{\\fad(80,0)}}{text}")
        out.write_text(head + "\n".join(lines) + "\n")
        return out

    head = _ass_head([
        "Style: Word,Anton,118,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1",
        "Style: Hook,Anton,104,&H0000E5FF,&H00FFFFFF,&H00000000,&HC8000000,0,0,0,0,100,100,1,0,3,18,0,8,70,70,260,1"])
    lines = [f"Dialogue: 1,{_ass_time(0)},{_ass_time(1.6)},Hook,,0,0,0,,{{\\fad(0,150)}}{hook.upper()}"] if hook else []
    # группы по 1–3 слова, разрыв на пунктуации
    groups, cur = [], []
    for wd in words:
        cur.append(wd)
        if len(cur) == 3 or wd["word"][-1] in ".,!?;:" or len(wd["word"]) > 9:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    pos = f"{{\\pos({W // 2},{int(H * 0.66)})}}"
    for g in groups:
        g_end = g[-1]["end"] + 0.08
        for i, wd in enumerate(g):
            start = wd["start"]
            end = g[i + 1]["start"] if i + 1 < len(g) else g_end
            parts = [f"{{\\c&H0000E5FF&}}{_clean(o['word']).upper()}{{\\c&H00FFFFFF&}}" if j == i
                     else _clean(o["word"]).upper() for j, o in enumerate(g)]
            pop = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Word,,0,0,0,,{pos}{pop}{' '.join(parts)}")
    out.write_text(head + "\n".join(lines) + "\n")
    return out


def frame_template(fmt: dict, kicker: str, title: str, footer: str, out: Path) -> Path:
    """Статичный фон формата series: надпись-кикер, заголовок, золотые линии вокруг панели, подвал."""
    from .style import rgb
    S = CFG["shorts"]
    W, H = S["width"], S["height"]
    F, P = fmt["frame"], fmt["frame"]["panel"]
    img = Image.new("RGB", (W, H), rgb(F["bg"]))
    d = ImageDraw.Draw(img)
    # мягкий световой конус сверху, как в референсе
    glow = Image.new("L", (W, H), 0)
    if sum(rgb(F["bg"])) < 300:
        ImageDraw.Draw(glow).polygon([(W * 0.42, 0), (W * 0.58, 0), (W * 0.78, P["y"]), (W * 0.22, P["y"])],
                                     fill=38)
    from PIL import ImageFilter
    img = Image.composite(Image.new("RGB", (W, H), rgb(F["line"])), img, glow.filter(ImageFilter.GaussianBlur(40)))
    d = ImageDraw.Draw(img)
    if F.get("stars"):  # чернильные точки и жёлтые звёздочки по бумаге (вне панели)
        rnd = random.Random(title)
        text_bands = [(P["y"] - 10, P["y"] + P["h"] + 10), (280, 580), (P["y"] + P["h"] + 40, P["y"] + P["h"] + 260),
                      (H - 175, H - 95)]  # панель, заголовок, субтитры, подвал
        for _ in range(220):
            x, y = rnd.uniform(0, W), rnd.uniform(0, H)
            if any(a < y < b for a, b in text_bands):
                continue
            r = rnd.uniform(1, 2.6)
            d.ellipse([x - r, y - r, x + r, y + r], fill=rgb(F["line"]))
        for _ in range(16):
            x, y = rnd.uniform(40, W - 40), rnd.choice([rnd.uniform(40, 280), rnd.uniform(H - 330, H - 40)])
            r = rnd.uniform(12, 22)
            k = r * 0.28
            d.polygon([(x, y - r), (x + k, y - k), (x + r, y), (x + k, y + k), (x, y + r), (x - k, y + k),
                       (x - r, y), (x - k, y - k)], fill=rgb(F["stars"]), outline=rgb(F["line"]))
    mono = ImageFont.truetype(str(FONTS / "SpaceMono-Regular.ttf"), 30)
    serif = ImageFont.truetype(str(FONTS / "Lora.ttf"), 76)
    try:
        serif.set_variation_by_name("Regular")
    except Exception:
        pass
    small = ImageFont.truetype(str(FONTS / "Lora.ttf"), 30)

    def center(y, text, font, fill):
        d.text(((W - d.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)

    center(300, kicker.upper()[:40], mono, rgb(F["kicker"]))
    lines, cur = [], ""
    for wd in title.split():
        if cur and d.textlength(f"{cur} {wd}", font=serif) > W - 140:
            lines.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    lines.append(cur)
    y = 360 if len(lines) <= 2 else 330
    for ln in lines[:3]:
        center(y, ln, serif, rgb(F["title"]))
        y += 88
    for yy in (P["y"] - 4, P["y"] + P["h"] + 2):
        d.rectangle([0, yy, W, yy + 2], fill=rgb(F["line"]))
    center(H - 150, footer, small, rgb(F["footer"]))
    img.save(out, "PNG")
    return out


def assemble_short(segments: list[Path], cut_times: list[float], voice: Path, ass: Path, ambient: Path,
                   whoosh: Path | None, out: Path, work: Path, template: Path | None = None,
                   panel_y: int = 0) -> None:
    """template — фон 1080×1920 (формат series): видео из сегментов кладётся панелью на высоте panel_y."""
    S = CFG["shorts"]
    video = work / "short_video.mp4"
    concat(segments, video)
    n = len(cut_times) if whoosh else 0
    base = "[0:v]"
    pre = []
    if template:
        pre = [f"[4:v][0:v]overlay=0:{panel_y}:shortest=1[base]"]
        base = "[base]"
    graph = pre + [f"{base}subtitles='{ass}':fontsdir='{FONTS}',format=yuv420p[v]",
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
    extra = ["-i", str(whoosh or ambient)] + (["-loop", "1", "-i", str(template)] if template else [])
    ff("-i", str(video), "-i", str(voice), "-stream_loop", "-1", "-i", str(ambient), *extra,
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
