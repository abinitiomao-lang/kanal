"""Минимальный клиент Telegram Bot API. Принимаются апдейты только от владельца."""
import json
from pathlib import Path

import requests

from .config import secret

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method: str, files=None, http_timeout=60, **params):
    url = API.format(token=secret("TELEGRAM_BOT_TOKEN"), method=method)
    data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}
    try:
        r = requests.post(url, data=data, files=files, timeout=http_timeout)
    except requests.RequestException as e:
        # текст исключения содержит URL с токеном — не пробрасываем его в лог
        raise RuntimeError(f"Telegram {method}: network error {type(e).__name__}") from None
    body = r.json()
    if not body.get("ok"):
        # не печатаем url — в нём токен
        raise RuntimeError(f"Telegram {method}: {body.get('description')}")
    return body["result"]


def owner() -> int:
    return int(secret("TELEGRAM_OWNER_ID"))


def kb(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def send(text: str, buttons=None, **kw) -> dict:
    return _call("sendMessage", chat_id=owner(), text=text[:4000], parse_mode="HTML",
                 disable_web_page_preview=True, reply_markup=kb(buttons) if buttons else None, **kw)


def send_photo(path: Path, caption: str, buttons=None) -> dict:
    with open(path, "rb") as f:
        return _call("sendPhoto", files={"photo": f}, chat_id=owner(), caption=caption[:1024],
                     parse_mode="HTML", reply_markup=kb(buttons) if buttons else None)


def send_document(path: Path, caption: str = "") -> dict:
    with open(path, "rb") as f:
        return _call("sendDocument", files={"document": f}, http_timeout=300, chat_id=owner(),
                     caption=caption[:1024], parse_mode="HTML")


def edit_markup(message_id: int, buttons=None) -> None:
    try:
        _call("editMessageReplyMarkup", chat_id=owner(), message_id=message_id,
              reply_markup=kb(buttons) if buttons else {"inline_keyboard": []})
    except RuntimeError:
        pass  # «message is not modified» и т.п.


def answer(callback_id: str, text: str = "") -> None:
    try:
        _call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])
    except RuntimeError:
        pass  # колбэк старше 15 минут — не страшно


def updates(offset: int, timeout: int = 50) -> list[dict]:
    return _call("getUpdates", http_timeout=timeout + 15, offset=offset, timeout=timeout,
                 allowed_updates=["message", "callback_query"])
