"""Telegram-бот: выбор тем, апрув, команды. Работает поллингом из GitHub Actions (bot.yml)."""
import html
import secrets
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import audio, classics, costs, llm, state, telegram, writer, youtube
from .config import CFG

HELP = ("Команды:\n/status — очередь и расходы\n/long — предложить темы длинного видео\n"
        "/shorts — предложить темы шортсов\n/report — отчёт по просмотрам\n/names — выбрать название канала\n/help")


def new_id(prefix: str) -> str:
    return f"{prefix}{datetime.now(timezone.utc):%m%d%H%M}{secrets.token_hex(2)}"


def dispatch(workflow: str, item_id: str) -> None:
    subprocess.run(["gh", "workflow", "run", workflow, "-f", f"item_id={item_id}"], check=True)


def _budget_ok(extra: float) -> bool:
    if costs.over_budget(extra):
        telegram.send(f"⛔️ Бюджет исчерпан, производство остановлено.\n{costs.summary()}\n"
                      "Подними budget.monthly_usd в config.yaml или дождись следующего месяца.")
        return False
    return True


# ---------------- предложения ----------------

def propose_long() -> None:
    st = state.load()
    longs = [i for i in st["items"].values() if i["kind"] == "long" and i["status"] != "rejected"]
    topics = writer.propose_mixed([i["topic"] for i in longs],
                                  {(i["series"], i["part"]) for i in longs if i.get("series")})
    pid = new_id("p")
    icon = {writer.FREQ_SCIENCE: "🔭", writer.FREQ_MIND: "🧠", classics.FREQ: "📜"}
    text = "🌙 <b>Следующее длинное видео</b> — выбери одно:\n\n" + "\n".join(
        f"{n + 1}. {icon.get(t.get('frequency'), '')} <b>{html.escape(t['topic'])}</b> — {html.escape(t['pitch'])}"
        + (f" 📚 <i>{html.escape(t['source'])}</i>" if t.get("source") else "") for n, t in enumerate(topics))
    rows = [[(f"{n + 1}. {t.get('button', t['topic'])[:42]}", f"L:{pid}:{n}")] for n, t in enumerate(topics)]
    rows.append([("🔄 Другие темы", f"X:{pid}:long")])
    msg = telegram.send(text, rows)

    def fn(s):
        s["proposals"][pid] = {"kind": "long", "topics": topics, "msg": msg["message_id"],
                               "picked": [], "created": state.now()}
        s["last_long_proposal"] = date.today().isoformat()
    state.update(fn, "propose long")
    llm.flush("proposals")


def propose_shorts() -> None:
    from . import style
    st = state.load()
    longs = sorted(((k, v) for k, v in st["items"].items() if v["kind"] == "long" and v.get("short_facts")
                    and v["status"] != "rejected"), key=lambda kv: kv[1]["created"], reverse=True)[:6]
    long_ids = [k for k, _ in longs]
    shorts = [i for i in st["items"].values() if i["kind"] == "short"]
    ideas = writer.propose_shorts([{"title": v["title"], "facts": v["short_facts"]} for _, v in longs],
                                  [i["topic"] for i in shorts])
    books = {b["id"] for b in writer.load_series()}
    pid = new_id("p")
    per = CFG["shorts"]["per_day"]
    topics = []
    for n, t in enumerate(ideas):
        d = t.model_dump()
        d["long_ref"] = long_ids[t.related_long] if 0 <= t.related_long < len(long_ids) else None
        d["book"] = t.book if t.book in books else None
        d["format"] = style.for_book(d["book"]) or style.rotate(len(shorts) + n)
        topics.append(d)
    fmts = style.load()["formats"]
    text = f"⚡️ <b>Темы шортсов</b> — выбери до {per}:\n\n" + "\n".join(
        f"{n + 1}. {fmts[t['format']]['label']} {html.escape(t['fact'])}" for n, t in enumerate(topics))
    rows = [[(f"{n + 1}. {t['fact'][:45]}", f"S:{pid}:{n}")] for n, t in enumerate(topics)]
    rows.append([("🔄 Другие идеи", f"X:{pid}:short")])
    msg = telegram.send(text, rows)

    def fn(s):
        s["proposals"][pid] = {"kind": "short", "topics": topics, "msg": msg["message_id"], "picked": [],
                               "created": state.now()}
        s["last_short_proposal"] = date.today().isoformat()
    state.update(fn, "propose shorts")
    llm.flush("proposals")


def propose_names() -> None:
    names = list(dict.fromkeys(CFG["channel"]["name_candidates"] + writer.channel_names()))[:8]
    state.update(lambda s: s.__setitem__("name_options", names), "name options")
    telegram.send("🏷 <b>Название канала</b> — выбери:", [[(n, f"N:{i}")] for i, n in enumerate(names)])
    llm.flush("names")


# ---------------- обработка ----------------

def on_pick(kind: str, pid: str, n: int, msg_id: int) -> str:
    st = state.load()
    prop = st["proposals"].get(pid)
    if not prop:
        return "Предложение устарело"
    limit = 1 if kind == "long" else CFG["shorts"]["per_day"]
    if n in prop["picked"]:
        return "Уже в работе"
    if len(prop["picked"]) >= limit:
        return "Лимит на это предложение выбран"
    if not _budget_ok(0.5 if kind == "long" else 0.1):
        return "Бюджет"
    t = prop["topics"][n]
    item_id = new_id("L" if kind == "long" else "S")
    topic = t["topic"] if kind == "long" else t["fact"]

    def fn(s):
        s["proposals"][pid]["picked"].append(n)
        s["items"][item_id] = {"kind": kind, "topic": topic, "status": "queued", "created": state.now(),
                               "long_ref": t.get("long_ref"), "series": t.get("series"), "part": t.get("part"),
                               "format": t.get("format"), "book": t.get("book"),
                               "feedback": []}
    state.update(fn, f"pick {item_id}")
    picked = prop["picked"] + [n]
    if len(picked) >= limit:
        telegram.edit_markup(msg_id)
    else:
        rows = [[(("✅ " if i in picked else "") + f"{i + 1}. {(x.get('topic') or x.get('fact'))[:42]}",
                  f"{'L' if kind == 'long' else 'S'}:{pid}:{i}")] for i, x in enumerate(prop["topics"])]
        telegram.edit_markup(msg_id, rows)
    dispatch("long.yml" if kind == "long" else "short.yml", item_id)
    telegram.send(f"▶️ В работе: <b>{html.escape(topic)}</b> ({item_id}). "
                  f"{'Длинное видео займёт ~1.5–2.5 ч.' if kind == 'long' else 'Шортс будет через ~10 мин.'}")
    return "Принято"


def slot_dt(kind: str = "short") -> datetime:
    """Ближайшие свободные publish_hour_ny:00 по Нью-Йорку (люди ложатся спать): один ролик своего вида на день."""
    ny = datetime.now(ZoneInfo("America/New_York"))
    slot = ny.replace(hour=CFG["shorts"]["publish_hour_ny"], minute=0, second=0, microsecond=0)
    if slot <= ny + timedelta(minutes=30):
        slot += timedelta(days=1)
    taken = {datetime.fromisoformat(i["publish_at"].replace("Z", "+00:00")).astimezone(slot.tzinfo).date()
             for i in state.load()["items"].values() if i.get("publish_at") and i.get("kind") == kind}
    while slot.date() in taken:
        slot += timedelta(days=1)
    return slot


def best_slot(slot: datetime | None = None) -> str:
    """Слот публикации текстом, в МСК для удобства."""
    slot = slot or slot_dt()
    msk = slot.astimezone(ZoneInfo("Europe/Moscow"))
    return f"{slot:%d.%m %H:%M} по Нью-Йорку = {msk:%d.%m %H:%M} МСК"


def on_publish(item_id: str) -> str:
    it = state.get_item(item_id)
    if it.get("status") != "awaiting_approval":
        return f"Статус: {it.get('status')}"
    if not CFG["pre_audit_mode"] and it.get("video_id"):
        privacy = youtube.make_public(it["video_id"])
        if privacy == "public":
            state.set_item(item_id, status="published", public_id=it["video_id"])
            _drop_release(it)
            telegram.send(f"🚀 Опубликовано: https://youtu.be/{it['video_id']}")
            return "Опубликовано"
        telegram.send("⚠️ YouTube оставил видео приватным — похоже, проект API ещё не прошёл аудит. "
                      "Переключаю на ручную публикацию.")
    # ручная публикация (до аудита)
    where = ("файл — в сообщении «Превью шортса» выше (загрузи через приложение YouTube → Shorts)"
             if it["kind"] == "short" else
             f"файл и превью — в черновике релиза <code>{it.get('release')}</code>: "
             f"GitHub → Releases (видно только тебе)")
    telegram.send(
        f"📤 <b>Ручная публикация</b> ({item_id})\n{where}\n\n"
        f"<b>Заголовок:</b>\n<code>{html.escape(it['title'])}</code>\n\n"
        f"<b>Описание:</b>\n<code>{html.escape(it['description'][:3000])}</code>\n\n"
        f"<b>Теги:</b>\n<code>{html.escape(', '.join(it['tags']))}</code>\n\n"
        "В Studio: категория Education, «Не для детей», Altered content → Yes.\n"
        f"⏰ Не публикуй сразу — «Запланировать» на <b>{best_slot()}</b>.\n"
        "После загрузки пришли ссылку на видео ответом или нажми кнопку.",
        [[("✅ Загрузил вручную", f"M:{item_id}")]])
    state.update(lambda s: s.__setitem__("awaiting_link", item_id), "await link")
    return "Инструкция отправлена"


def _drop_release(it: dict) -> None:
    if it.get("release"):
        subprocess.run(["gh", "release", "delete", it["release"], "-y", "--cleanup-tag"], check=False)


def on_schedule(item_id: str) -> str:
    it = state.get_item(item_id)
    if it.get("status") != "awaiting_approval":
        return f"Статус: {it.get('status')}"
    if CFG["pre_audit_mode"] or not it.get("video_id"):
        return on_publish(item_id)  # ручной режим: инструкция с подсказкой времени
    when = slot_dt(it["kind"])
    st = youtube.schedule(it["video_id"], when)
    if not st.get("publishAt"):
        telegram.send("⚠️ YouTube не принял время публикации — нажми «Опубликовать» или поставь время в Studio.")
        return "Не вышло"
    state.set_item(item_id, status="published", public_id=it["video_id"], publish_at=st["publishAt"])
    _drop_release(it)
    telegram.send(f"⏰ Запланировано на {best_slot(when)}: https://youtu.be/{it['video_id']}")
    return "Запланировано"


def on_manual_done(item_id: str, public_url: str | None = None) -> None:
    it = state.get_item(item_id)
    public_id = None
    if public_url:
        public_id = public_url.rstrip("/").split("/")[-1].split("?v=")[-1].split("?")[0]
    if it.get("video_id"):
        youtube.delete(it["video_id"])  # приватная копия больше не нужна
    if it.get("release"):
        subprocess.run(["gh", "release", "delete", it["release"], "-y", "--cleanup-tag"], check=False)
    state.set_item(item_id, status="published", public_id=public_id)

    def fn(s):
        if s.get("awaiting_link") == item_id:
            s["awaiting_link"] = None
    state.update(fn, "manual done")
    telegram.send(f"🚀 Отмечено как опубликованное{': ' + public_url if public_url else ''}. "
                  "Приватная копия удалена." + ("" if public_id else
                                                 "\nПришли ссылку, чтобы шортсы могли на него ссылаться."))


def on_reject(item_id: str) -> str:
    it = state.get_item(item_id)
    if it.get("video_id"):
        youtube.delete(it["video_id"])
    if it.get("release"):
        subprocess.run(["gh", "release", "delete", it["release"], "-y", "--cleanup-tag"], check=False)
    state.set_item(item_id, status="rejected")
    return "Отклонено, видео удалено"


def on_redo(item_id: str) -> str:
    state.update(lambda s: s.__setitem__("awaiting_comment", item_id), "await comment")
    telegram.send(f"✏️ Что поправить в {item_id}? Напиши комментарий одним сообщением "
                  "или «-», чтобы переделать без комментария.")
    return "Жду комментарий"


def on_comment(item_id: str, text: str) -> None:
    it = state.get_item(item_id)
    if not _budget_ok(0.5 if it["kind"] == "long" else 0.1):
        return
    if it.get("video_id"):
        youtube.delete(it["video_id"])
    if it.get("release"):
        subprocess.run(["gh", "release", "delete", it["release"], "-y", "--cleanup-tag"], check=False)
    fb = it.get("feedback", []) + ([text] if text.strip() not in ("-", "") else [])

    def fn(s):
        s["awaiting_comment"] = None
        s["items"][item_id].update(feedback=fb, status="queued", video_id=None, release=None)
    state.update(fn, f"redo {item_id}")
    dispatch("long.yml" if it["kind"] == "long" else "short.yml", item_id)
    telegram.send(f"🔁 Переделываю {item_id}" + (f" с учётом: «{html.escape(fb[-1])}»" if fb else ""))


def on_name(n: int) -> str:
    st = state.load()
    name = st.get("name_options", [])[n]
    state.update(lambda s: s.__setitem__("channel_name", name), "channel name")
    telegram.send(f"🏷 Выбрано: <b>{html.escape(name)}</b>.\nПереименуй канал вручную: YouTube Studio → "
                  "Настройка канала → Основная информация → Название (API этого не умеет).")
    return name


def status_text() -> str:
    st = state.load()
    active = [(k, v) for k, v in st["items"].items() if v["status"] not in ("published", "rejected")]
    lines = [f"• {k}: {v['status']} — {html.escape(v['topic'][:60])}" for k, v in sorted(active)[-15:]]
    pub = sum(v["status"] == "published" for v in st["items"].values())
    return f"📊 Опубликовано: {pub}\n" + ("\n".join(lines) or "Очередь пуста") + f"\n\n{costs.summary()}"


def handle(u: dict) -> None:
    owner = telegram.owner()
    if "callback_query" in u:
        cq = u["callback_query"]
        if cq["from"]["id"] != owner:
            return
        data, msg_id = cq.get("data", ""), cq["message"]["message_id"]
        kind, _, rest = data.partition(":")
        try:
            if kind in ("L", "S"):
                pid, n = rest.split(":")
                res = on_pick("long" if kind == "L" else "short", pid, int(n), msg_id)
            elif kind == "X":
                telegram.edit_markup(msg_id)
                (propose_long if rest.endswith("long") else propose_shorts)()
                res = "Новые темы"
            elif kind == "P":
                res = on_publish(rest)
            elif kind == "T":
                res = on_schedule(rest)
            elif kind == "M":
                on_manual_done(rest)
                res = "Готово"
            elif kind == "R":
                telegram.edit_markup(msg_id)
                res = on_reject(rest)
            elif kind == "D":
                res = on_redo(rest)
            elif kind == "Y":
                telegram.edit_markup(msg_id)
                res = on_delete_dup(rest)
            elif kind == "N":
                telegram.edit_markup(msg_id)
                res = on_name(int(rest))
            else:
                res = "?"
        except Exception as e:
            telegram.send(f"❌ {html.escape(str(e)[:400])}")
            res = "Ошибка"
        telegram.answer(cq["id"], res)
        return

    msg = u.get("message") or {}
    if msg.get("from", {}).get("id") != owner or "text" not in msg:
        return
    text = msg["text"].strip()
    st = state.load()
    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0]
        if cmd == "/status":
            telegram.send(status_text())
        elif cmd == "/long":
            propose_long()
        elif cmd == "/shorts":
            propose_shorts()
        elif cmd == "/names":
            propose_names()
        elif cmd == "/report":
            weekly_report()
        else:
            telegram.send(HELP)
    elif st.get("awaiting_comment"):
        on_comment(st["awaiting_comment"], text)
    elif st.get("awaiting_link") and "youtu" in text:
        on_manual_done(st["awaiting_link"], text)
    else:
        telegram.send(HELP)


def _retry_uploads() -> None:
    st = state.load()
    last = st.get("last_upload_retry")
    if not any(i.get("status") == "awaiting_upload" for i in st["items"].values()):
        return
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=1):
        return
    state.update(lambda s: s.__setitem__("last_upload_retry", state.now()), "upload retry")
    from . import pipeline
    try:
        pipeline.retry_uploads()
    except Exception as e:
        print(f"WARN retry uploads: {e}")


def _sync_playlists() -> None:
    """Раскладывает по плейлистам новые опубликованные видео (только если такие появились)."""
    st = state.load()
    pub = sorted(i["public_id"] for i in st["items"].values() if i.get("status") == "published" and i.get("public_id"))
    if pub == st.get("playlists_synced"):
        return
    try:
        youtube.playlists_sync(st["items"])
        state.update(lambda s: s.__setitem__("playlists_synced", pub), "playlists synced")
    except Exception as e:
        print(f"WARN playlists: {e}")


def _hours_since(ts: str | None) -> float:
    if not ts:
        return 1e9
    return (datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 3600


def sync_youtube(force: bool = False) -> None:
    """Раз в час сверяет state с YouTube: открытые вручную видео → published, пропавшие id ищет по названию,
    сообщает о дублях; кладёт просмотры в item['stats'] (для еженедельного отчёта)."""
    st = state.load()
    if not force and _hours_since(st.get("last_yt_sync")) < 1:
        return
    try:
        uploads = youtube.list_all()
        by_id = {v["id"]: v for v in uploads}
        nums = youtube.stats(list(by_id))
    except Exception as e:
        print(f"WARN youtube sync: {e}")
        return
    by_title: dict[str, list[str]] = {}
    for v in uploads:
        by_title.setdefault(v["title"].strip().casefold(), []).append(v["id"])
    tracked = {it.get("public_id") or it.get("video_id") for it in st["items"].values()}
    fixes, notes = {}, []
    for k, it in st["items"].items():
        if it.get("status") not in ("published", "awaiting_approval"):
            continue
        vid = it.get("public_id") or it.get("video_id")
        if vid not in by_id:
            # id устарел (перезалили / удалили копию) — ищем единственное неучтённое видео с тем же названием
            cands = [x for x in by_title.get((it.get("title") or "").strip().casefold(), []) if x not in tracked]
            if len(cands) != 1:
                continue
            vid = cands[0]
            tracked.add(vid)
            fixes[k] = {"public_id": vid}
            notes.append(f"🔗 {k}: на YouTube нашёлся по названию → {vid}")
        if it["status"] == "awaiting_approval" and by_id[vid]["privacy"] == "public":
            fixes.setdefault(k, {}).update(status="published", public_id=vid)
            _drop_release(it)
            notes.append(f"✅ {k} уже открыт на YouTube — отметил опубликованным")
    dups = {t: ids for t, ids in by_title.items() if len(ids) > 1}
    new_dups = sorted(set(dups) - set(st.get("dups_reported") or []))

    def fn(s):
        s["last_yt_sync"] = state.now()
        s["dups_reported"] = sorted(set(s.get("dups_reported") or []) | set(dups))
        for k, f in fixes.items():
            s["items"][k].update(f, updated=state.now())
        for it in s["items"].values():
            vid = it.get("public_id") or it.get("video_id")
            if vid in nums:
                it["stats"] = nums[vid]
    state.update(fn, "youtube sync")
    if notes:
        telegram.send("🔄 <b>Сверка с YouTube</b>\n" + "\n".join(notes))
    for t in new_dups:
        ids = sorted(dups[t], key=lambda v: nums.get(v, {}).get("views", 0), reverse=True)
        title = by_id[ids[0]]["title"]
        telegram.send(f"👯 <b>Дубль на канале</b>: «{html.escape(title)}»\n" + "\n".join(
            f"• https://youtu.be/{v} — {nums.get(v, {}).get('views', 0)} просм., {by_id[v]['privacy']}" for v in ids)
            + "\nОставить самый просматриваемый, остальные удалить?",
            [[(f"🗑 Удалить {v}", f"Y:{v}")] for v in ids[1:]])


def on_delete_dup(video_id: str) -> str:
    youtube.delete(video_id)

    def fn(s):
        for it in s["items"].values():
            if it.get("public_id") == video_id:
                it["public_id"] = None
            if it.get("video_id") == video_id:
                it["video_id"] = None
    state.update(fn, f"delete dup {video_id}")
    return "Удалено"


def weekly_report() -> None:
    """Отчёт за неделю: прирост просмотров по видео, средние по форматам и книгам, вывод что делать больше."""
    from . import style
    sync_youtube(force=True)
    st = state.load()
    prev = st.get("report_snapshot") or {}
    pub = [it for it in st["items"].values() if it.get("status") == "published" and it.get("stats")]
    if not pub:
        return
    for it in pub:
        vid = it.get("public_id") or it.get("video_id")
        it["_vid"], it["_gain"] = vid, it["stats"]["views"] - prev.get(vid, 0)
    try:
        ch = youtube.channel_stats()
        head = f"👥 {ch['subs']} подписчиков · 👁 {ch['views']} просмотров всего · 🎬 {ch['videos']} видео"
    except Exception as e:
        head = f"(статистика канала недоступна: {str(e)[:80]})"
    period = "за неделю" if prev else "за всё время"

    def line(it):
        return (f"{it['_gain']:>4} · {'🌙' if it['kind'] == 'long' else '⚡️'} "
                f"<a href=\"https://youtu.be/{it['_vid']}\">{html.escape((it.get('title') or it['topic'])[:55])}</a>")

    def avg(group):
        return sum(i["_gain"] for i in group) / len(group)

    top = sorted(pub, key=lambda i: i["_gain"], reverse=True)
    shorts = [i for i in pub if i["kind"] == "short"]
    longs = [i for i in pub if i["kind"] == "long"]
    fmts, books = {}, {}
    for i in shorts:
        fmts.setdefault(i.get("format") or "?", []).append(i)
        if i.get("book"):
            books.setdefault(i["book"], []).append(i)
    labels = style.load()["formats"]
    fmt_rows = sorted(((avg(g), f, len(g)) for f, g in fmts.items()), reverse=True)
    book_rows = sorted(((avg(g), b, len(g)) for b, g in books.items() if len(g) >= 2), reverse=True)
    parts = [f"📈 <b>Отчёт канала</b> ({period})\n{head}",
             "<b>Топ:</b>\n" + "\n".join(line(i) for i in top[:5]),
             "<b>Слабее всех:</b>\n" + "\n".join(line(i) for i in top[-3:]) if len(top) > 6 else "",
             "<b>В среднем на видео:</b> " + " · ".join(
                 ([f"шортс {avg(shorts):.0f}"] if shorts else []) + ([f"длинное {avg(longs):.0f}"] if longs else [])),
             "<b>Форматы шортсов:</b> " + ", ".join(
                 f"{labels.get(f, {}).get('label', f)} {a:.0f} ({n})" for a, f, n in fmt_rows) if fmt_rows else "",
             "<b>Книги (от 2 шортсов):</b> " + ", ".join(f"{b} {a:.0f} ({n})" for a, b, n in book_rows)
             if book_rows else ""]
    voices = {}
    for i in longs:
        voices.setdefault(i.get("voice") or "af_heart", []).append(i)
    if len(voices) > 1:
        parts.append("<b>Голоса длинных:</b> " + ", ".join(
            f"{v} {avg(g):.0f} ({len(g)})" for v, g in sorted(voices.items(), key=lambda kv: -avg(kv[1]))))
    advice = []
    if len(fmt_rows) > 1 and fmt_rows[0][0] >= 1.5 * max(fmt_rows[-1][0], 1):
        advice.append(f"формат «{fmt_rows[0][1]}» заметно сильнее «{fmt_rows[-1][1]}» — "
                      f"переводить книги из «{fmt_rows[-1][1]}» (assets/short_style.yaml → book_formats)")
    if len(book_rows) > 1:
        advice.append(f"больше шортсов по «{book_rows[0][1]}», меньше по «{book_rows[-1][1]}»")
    if longs and shorts and avg(longs) < avg(shorts) / 3:
        advice.append("длинные почти не находят зрителя: брать темы с поисковым спросом и ставить «Похожее видео» "
                      "в шортсах на длинное (Studio, вручную)")
    if advice:
        parts.append("💡 <b>Что делать:</b>\n" + "\n".join(f"• {a}" for a in advice))
    telegram.send("\n\n".join(p for p in parts if p))

    def fn(s):
        s["report_snapshot"] = {i["_vid"]: i["stats"]["views"] for i in pub}
        s["last_report"] = date.today().isoformat()
    state.update(fn, "weekly report")


def _auto_rank(t: dict) -> int:
    """Порядок автовыбора шортсов: игры Берна, потом форматы по силе (scene > series > tale)."""
    return 0 if t.get("book") == "berne" else {"scene": 1, "series": 2}.get(t.get("format"), 3)


def autopilot() -> None:
    """Если владелец молчит: через N ч берёт темы сам, а готовое видео ставит в план (21:00 NY) с кнопкой «снять»."""
    A = CFG.get("autopilot") or {}
    if not A.get("enabled"):
        return
    st = state.load()
    for kind in ("long", "short"):
        props = sorted(((pid, p) for pid, p in st["proposals"].items() if p["kind"] == kind),
                       key=lambda kv: kv[1].get("created", ""))
        if not props:
            continue
        pid, p = props[-1]
        if p.get("picked") or p.get("auto") or _hours_since(p.get("created")) < A["pick_after_hours"]:
            continue
        state.update(lambda s, pid=pid: s["proposals"][pid].__setitem__("auto", True), f"autopilot {pid}")
        n = 1 if kind == "long" else CFG["shorts"]["per_day"]
        order = list(range(len(p["topics"])))
        if kind == "short":
            order.sort(key=lambda i: _auto_rank(p["topics"][i]))
        telegram.send(f"🤖 Автопилот: тему не выбрали за {A['pick_after_hours']} ч — выбираю сам.")
        for i in order[:n]:
            on_pick(kind, pid, i, p["msg"])
    for k, it in st["items"].items():
        if it.get("status") != "awaiting_approval" or it.get("auto_tried"):
            continue
        if _hours_since(it.get("updated") or it.get("created")) < A["approve_after_hours"]:
            continue
        state.set_item(k, auto_tried=True)
        try:
            res = on_schedule(k)
        except Exception as e:
            res = str(e)[:200]
        if res == "Запланировано":
            telegram.send(f"🤖 Автопилот: <b>{html.escape(it.get('title') or k)}</b> ждал решения "
                          f"{A['approve_after_hours']} ч — поставил в план. До выхода можно снять:",
                          [[("🗑 Снять и удалить", f"R:{k}")]])
        else:
            telegram.send(f"⏳ {k} ждёт решения, автопилот не смог поставить в план: {html.escape(res)}")


def poll(seconds: int) -> None:
    _retry_uploads()
    _sync_playlists()
    sync_youtube()
    autopilot()
    deadline = time.time() + seconds
    conflicts = 0
    while time.time() < deadline:
        offset = state.load()["tg_offset"]
        try:
            ups = telegram.updates(offset, timeout=min(50, max(1, int(deadline - time.time()))))
        except RuntimeError as e:
            if "Conflict" not in str(e):  # параллельный getUpdates (будильник проверяет очередь) — не страшно
                raise
            conflicts += 1
            if conflicts in (1, 20):  # webhook или чужой поллер съедает нажатия — должно быть видно в логе
                print(f"WARN getUpdates: {e}")
            time.sleep(3)
            continue
        for u in ups:
            # сначала сдвигаем offset — чтобы сбой обработки не зациклил бота
            state.update(lambda s, uid=u["update_id"]: s.__setitem__("tg_offset", uid + 1), "tg offset")
            try:
                handle(u)
            except Exception as e:
                print(f"handle error: {type(e).__name__}: {e}")
                telegram.send(f"❌ {html.escape(str(e)[:400])}")


def daily() -> None:
    st = state.load()
    today = date.today()
    if not st.get("channel_name") and not st.get("name_options"):
        propose_names()
    if st.get("last_short_proposal") != today.isoformat():
        propose_shorts()
    last = st.get("last_long_proposal")
    if not last or (today - date.fromisoformat(last)).days >= CFG["long"]["every_days"]:
        propose_long()
    rep = st.get("last_report")
    if not rep or (today - date.fromisoformat(rep)).days >= 7:
        weekly_report()
    total, _ = costs.month_total()
    limit = CFG["budget"]["monthly_usd"]
    if total >= limit * CFG["budget"]["warn_at"] and st.get("budget_warned") != costs.month():
        telegram.send(f"⚠️ Расходы приближаются к лимиту.\n{costs.summary()}")
        state.update(lambda s: s.__setitem__("budget_warned", costs.month()), "budget warned")
    q = audio.eleven_quota()
    if q and q[0] > q[1] * 0.8:
        telegram.send(f"⚠️ ElevenLabs: использовано {q[0]} из {q[1]} кредитов.")
