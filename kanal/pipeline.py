"""Производство: шортс (одним job'ом) и длинное видео (prepare → render×N → assemble)."""
import html
import json
import re
import subprocess
from pathlib import Path

from . import audio, costs, images, llm, render, state, stock, telegram, writer, youtube
from .config import CFG, workdir

DISCLOSURE = ("Narration uses an AI voice; visuals are AI-generated illustrations and royalty-free stock. "
              "Scripts are fact-checked, but if you spot a mistake, tell us in the comments.")


def _repo() -> str:
    return subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                          capture_output=True, text=True).stdout.strip()


def _feedback(item: dict) -> str:
    return (item.get("feedback") or [""])[-1]


def _issues_text(issues: list[dict], found: int) -> str:
    if not found:
        return "✅ Фактчек: замечаний нет"
    s = f"🔎 Фактчек: найдено {found}, осталось {len(issues)}"
    for i in issues[:5]:
        s += f"\n⚠️ «{html.escape(i['claim'][:120])}» — {html.escape(i['problem'][:160])}"
    return s


def _long_link(item: dict) -> tuple[str | None, str | None]:
    """Длинное видео той же темы для отсылки из шортса: (заголовок, url)."""
    ref = item.get("long_ref")
    if not ref:
        return None, None
    long_item = state.load()["items"].get(ref, {})
    url = f"https://youtu.be/{long_item['public_id']}" if long_item.get("public_id") else None
    return long_item.get("title"), url


def fail(item_id: str, err: Exception, run_url: str = "") -> None:
    state.set_item(item_id, status="failed", error=str(err)[:500])
    telegram.send(f"❌ Ошибка производства <b>{item_id}</b>: {html.escape(str(err)[:500])}\n{run_url}",
                  [[("🔁 Повторить", f"D:{item_id}")]])


def approval_message(item_id: str) -> None:
    it = state.get_item(item_id)
    kind = "Шортс" if it["kind"] == "short" else "Длинное видео"
    cap = (f"🎬 <b>{kind}</b> готово (приватно): https://youtu.be/{it['video_id']}\n\n"
           f"<b>{html.escape(it['title'])}</b>\n\n{html.escape(it['description'][:450])}…\n\n"
           f"🏷 {html.escape(', '.join(it['tags'][:12]))}\n⏱ {it['duration'] / 60:.1f} мин\n"
           f"{it.get('factcheck', '')}\n\n{costs.summary()}")
    buttons = [[("✅ Опубликовать", f"P:{item_id}"), ("🗑 Отклонить", f"R:{item_id}")],
               [("🔁 Переделать", f"D:{item_id}")]]
    thumb = Path(it["thumb_path"]) if it.get("thumb_path") else None
    if thumb and thumb.exists():
        telegram.send_photo(thumb, cap, buttons)
    else:
        telegram.send(cap, buttons)


# ======================= ШОРТС =======================

def produce_short(item_id: str) -> None:
    item = state.get_item(item_id)
    state.set_item(item_id, status="producing")
    W = workdir("short", item_id)
    S = CFG["shorts"]
    long_title, long_url = _long_link(item)

    script = writer.short_script(item["topic"], long_title, _feedback(item))
    narration, issues, found = writer.checked(script.narration)

    voice = W / "voice.mp3"
    dur, words = audio.eleven(narration, voice)
    if dur > S["max_seconds"]:  # слегка ускоряем, сохраняя тайминги слов
        k = min(dur / (S["max_seconds"] - 0.5), 1.15)
        sped = W / "voice_fast.mp3"
        render.ff("-i", str(voice), "-af", f"atempo={k:.4f}", str(sped))
        voice, dur = sped, dur / k
        words = [{**w, "start": w["start"] / k, "end": w["end"] / k} for w in words]
    costs.add("elevenlabs", 0, item_id, eleven_chars=len(narration))

    # тайминг сцен: пропорционально числу слов сцены, привязка к реальным словам озвучки
    counts = [max(1, len(sc.words.split())) for sc in script.scenes]
    total = sum(counts)
    starts, acc = [], 0
    for c in counts:
        idx = min(len(words) - 1, round(acc / total * len(words)))
        starts.append(0.0 if not starts else words[idx]["start"])
        acc += c
    starts = sorted(set(round(s, 2) for s in starts))
    starts = [s for i, s in enumerate(starts) if i == 0 or s - starts[i - 1] >= 1.2]
    bounds = starts + [dur + 0.3]

    lib = images.Library()
    segs = []
    for i in range(len(starts)):
        sc = script.scenes[min(i, len(script.scenes) - 1)]
        seconds = bounds[i + 1] - bounds[i]
        src, is_video = None, False
        if S["use_pexels"] and stock.enabled() and i % 3 == 2:
            src = stock.vertical_clip(sc.stock_query, W / f"clip_{i}.mp4", seconds)
            is_video = src is not None
        if src is None:
            src = lib.get(sc.visual, "portrait", W / f"img_{i}.jpg")
        seg = W / f"seg_{i:02d}.mp4"
        render.short_segment(src, seg, seconds, hash(item_id) % 1000 + i, is_video)
        segs.append(seg)

    ass = render.karaoke_ass(words, script.hook_text, W / "subs.ass")
    amb = audio.ambient(W / "amb.wav", 20, seed=len(item_id))
    out = W / "short.mp4"
    render.assemble_short(segs, starts[1:], voice, ass, amb, audio.whoosh(W / "whoosh.wav"), out, W)

    desc = script.description.strip()
    if long_url:
        desc += f"\n\n🌙 Fall asleep learning more: {long_url}"
    desc += f"\n\n{DISCLOSURE}"
    vid = youtube.upload(out, script.title, desc, script.tags)

    lib.commit(item_id)
    llm.flush(item_id)
    state.set_item(item_id, status="awaiting_approval", video_id=vid, title=script.title, description=desc,
                   tags=script.tags, duration=audio.probe_duration(out), narration=narration,
                   factcheck=_issues_text([i.model_dump() for i in issues], found))
    telegram.send_document(out, f"▶️ Превью шортса {item_id}")
    approval_message(item_id)


# ======================= ДЛИННОЕ =======================

def long_prepare(item_id: str) -> Path:
    """Сценарий по главам + фактчек, одна главная картинка + её бесшовная анимация, превью.
    Результат — work/long/<id>/ (уходит артефактом в следующие job'ы)."""
    item = state.get_item(item_id)
    state.set_item(item_id, status="producing")
    W = workdir("long", item_id)
    fb = _feedback(item)
    A = CFG["animation"]
    outline = writer.long_outline(item["topic"], fb)
    chapters, all_issues, found = [], [], 0
    for i in range(len(outline.chapters)):
        text, issues, f = writer.checked(writer.long_chapter(outline, i, fb))
        found += f
        all_issues += [x.model_dump() for x in issues]
        chapters.append({"title": outline.chapters[i].title, "text": text})
        print(f"chapter {i + 1}/{len(outline.chapters)}: {len(text.split())} words, issues {f}")

    lib = images.Library()
    hero = lib.generate(outline.hero_image, "landscape", W / "hero.jpg", model=A["hero_model"], usd=A["hero_usd"])
    animated = True
    try:
        if costs.over_budget(A["usd_per_second"] * A["seconds"]):
            raise RuntimeError("бюджет")
        lib.spent += images.animate_loop(lib.last_url, outline.motion, W / "anim.mp4")
        loop_len = render.prepare_loop(W / "anim.mp4", W / "loop.mp4", is_video=True)
    except Exception as e:
        print(f"WARN animation failed, static fallback: {e}")
        animated = False
        loop_len = render.prepare_loop(hero, W / "loop.mp4", is_video=False)
    render.thumbnail(hero, outline.thumbnail_text, "FALL ASLEEP LEARNING", W / "thumb.jpg")

    chapter_list = "\n".join(f"• {c['title']}" for c in chapters)
    desc = f"{outline.description.strip()}\n\nIn this video:\n{chapter_list}\n\n{DISCLOSURE}"
    script = {"item_id": item_id, "title": outline.video_title, "description": desc, "tags": outline.tags,
              "chapters": chapters, "loop_len": loop_len, "animated": animated,
              "factcheck": _issues_text(all_issues, found)}
    (W / "script.json").write_text(json.dumps(script, indent=1, ensure_ascii=False))
    lib.commit(item_id)
    llm.flush(item_id)
    state.set_item(item_id, status="rendering", title=outline.video_title)
    telegram.send_document(W / "loop.mp4", f"🎞 Анимация для «{html.escape(outline.video_title)}» "
                           f"({'Kling' if animated else '⚠️ запасной вариант без анимации'}) — она будет "
                           f"повторяться всё видео.\n📝 Сценарий: "
                           f"{sum(len(c['text'].split()) for c in chapters)} слов. {script['factcheck']}\nОзвучиваю…")
    return W


def long_render(item_id: str, group: int, groups: int) -> None:
    """Озвучка глав group, group+groups, … (параллельные job'ы матрицы)."""
    W = workdir("long", item_id)
    script = json.loads((W / "script.json").read_text())
    for ci, ch in enumerate(script["chapters"]):
        if ci % groups != group:
            continue
        raw = workdir("long", item_id, f"ch{ci:02d}") / "voice.wav"
        seconds = audio.kokoro(ch["text"], raw) + 2.0
        render.pad_audio(raw, seconds, W / f"chapter_{ci:02d}.flac")
        print(f"chapter {ci}: {seconds / 60:.1f} min")


def long_assemble(item_id: str) -> None:
    W = workdir("long", item_id)
    script = json.loads((W / "script.json").read_text())
    n = len(script["chapters"])
    voices = [W / f"chapter_{i:02d}.flac" for i in range(n)]
    missing = [p.name for p in voices if not p.exists()]
    if missing:
        raise RuntimeError(f"Нет частей: {missing}")
    out = W / "final.mp4"
    dur = render.assemble_long(W / "loop.mp4", script["loop_len"], voices,
                               audio.ambient(W / "amb.wav", 120, seed=n), out, W)
    # в заголовке — фактическая длительность, а не плановая
    script["title"] = re.sub(r"\d+(\.\d+)? Hours?", f"{max(1, round(dur / 3600))} Hours", script["title"])
    vid = youtube.upload(out, script["title"], script["description"], script["tags"])
    thumb_ok = youtube.set_thumbnail(vid, W / "thumb.jpg")
    release = None
    if CFG["pre_audit_mode"]:
        release = f"video-{item_id}"
        subprocess.run(["gh", "release", "create", release, "--draft", "--title", script["title"][:100],
                        "--notes", "Файл для ручной загрузки в YouTube Studio (до аудита API).",
                        f"{out}#final.mp4", f"{W / 'thumb.jpg'}#thumbnail.jpg"], check=True)
    state.set_item(item_id, status="awaiting_approval", video_id=vid, title=script["title"],
                   description=script["description"], tags=script["tags"], duration=dur,
                   factcheck=script["factcheck"] + ("" if thumb_ok else "\nℹ️ Превью не установлено "
                                                    "(верифицируй канал по телефону)"),
                   release=release, thumb_path=str(W / "thumb.jpg"))
    if dur < 2 * 3600:
        telegram.send(f"ℹ️ Длительность {dur / 3600:.2f} ч — меньше 2 ч. Увеличу target_minutes, если нужно.")
    approval_message(item_id)
