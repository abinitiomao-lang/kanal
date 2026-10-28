"""Telegram-бот: выбор тем, апрув, команды. Работает поллингом из GitHub Actions (bot.yml)."""
import html
import secrets
import subprocess
import time
from datetime import date, datetime, timezone

from . import audio, costs, llm, state, telegram, writer, youtube
from .config import CFG

HELP = ("Команды:\n/status — очередь и расходы\n/long — предложить темы длинного видео\n"
        "/shorts — предложить темы шортсов\n/names — выбрать название канала\n/help")


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
    topics = writer.next_book_parts({(i["series"], i["part"]) for i in longs if i.get("series")})
    if not topics:  # все книги сняты — обычные научные темы
        topics = [t.model_dump() for t in writer.propose_long([i["topic"] for i in longs])]
    pid = new_id("p")
    text = "🌙 <b>Следующее длинное видео</b> — выбери одно:\n\n" + "\n".join(
        f"{n + 1}. <b>{html.escape(t['topic'])}</b> — {html.escape(t['pitch'])}"
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
    st = state.load()
    longs = sorted(((k, v) for k, v in st["items"].items() if v["kind"] == "long" and v.get("short_facts")
                    and v["status"] != "rejected"), key=lambda kv: kv[1]["created"], reverse=True)[:6]
    if not longs:
        telegram.send("⚡️ Шортсы делаются из фактов длинных видео — темы появятся, когда будет готов сценарий "
                      "первого длинного (выбери его в /long).")
        return
    long_ids = [k for k, _ in longs]
    used = [i["topic"] for i in st["items"].values() if i["kind"] == "short"]
    ideas = writer.propose_shorts([{"title": v["title"], "facts": v["short_facts"]} for _, v in longs], used)
    pid = new_id("p")
    per = CFG["shorts"]["per_day"]
    text = f"⚡️ <b>Темы шортсов</b> — выбери до {per}:\n\n" + "\n".join(
        f"{n + 1}. {html.escape(t.fact)}" for n, t in enumerate(ideas))
    rows = [[(f"{n + 1}. {t.fact[:45]}", f"S:{pid}:{n}")] for n, t in enumerate(ideas)]
    rows.append([("🔄 Другие факты", f"X:{pid}:short")])
    msg = telegram.send(text, rows)
    topics = []
    for t in ideas:
        d = t.model_dump()
        d["long_ref"] = long_ids[t.related_long] if 0 <= t.related_long < len(long_ids) else None
        topics.append(d)

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


def on_publish(item_id: str) -> str:
    it = state.get_item(item_id)
    if it.get("status") != "awaiting_approval":
        return f"Статус: {it.get('status')}"
    if not CFG["pre_audit_mode"]:
        privacy = youtube.make_public(it["video_id"])
        if privacy == "public":
            state.set_item(item_id, status="published", public_id=it["video_id"])
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
        "После загрузки пришли ссылку на видео ответом или нажми кнопку.",
        [[("✅ Загрузил вручную", f"M:{item_id}")]])
    state.update(lambda s: s.__setitem__("awaiting_link", item_id), "await link")
    return "Инструкция отправлена"


def on_manual_done(item_id: str, public_url: str | None = None) -> None:
    it = state.get_item(item_id)
    public_id = None
    if public_url:
        public_id = public_url.rstrip("/").split("/")[-1].split("?v=")[-1].split("?")[0]
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
            elif kind == "M":
                on_manual_done(rest)
                res = "Готово"
            elif kind == "R":
                telegram.edit_markup(msg_id)
                res = on_reject(rest)
            elif kind == "D":
                res = on_redo(rest)
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
        else:
            telegram.send(HELP)
    elif st.get("awaiting_comment"):
        on_comment(st["awaiting_comment"], text)
    elif st.get("awaiting_link") and "youtu" in text:
        on_manual_done(st["awaiting_link"], text)
    else:
        telegram.send(HELP)


def poll(seconds: int) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        offset = state.load()["tg_offset"]
        ups = telegram.updates(offset, timeout=min(50, max(1, int(deadline - time.time()))))
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
    total, _ = costs.month_total()
    limit = CFG["budget"]["monthly_usd"]
    if total >= limit * CFG["budget"]["warn_at"] and st.get("budget_warned") != costs.month():
        telegram.send(f"⚠️ Расходы приближаются к лимиту.\n{costs.summary()}")
        state.update(lambda s: s.__setitem__("budget_warned", costs.month()), "budget warned")
    q = audio.eleven_quota()
    if q and q[0] > q[1] * 0.8:
        telegram.send(f"⚠️ ElevenLabs: использовано {q[0]} из {q[1]} кредитов.")
