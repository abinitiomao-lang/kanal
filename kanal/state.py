"""Состояние пайплайна хранится в ветке `state` этого же репо (state.json).

В CI ветка подключается как worktree в .state/. Обновления — через update(fn):
fetch → reset → применить fn → commit → push, с повтором при гонке.
Локально (без git remote) просто пишет файл.
"""
import json
import subprocess
import time
from datetime import datetime, timezone

from .config import ROOT

STATE_DIR = ROOT / ".state"
STATE_FILE = STATE_DIR / "state.json"
BRANCH = "state"

EMPTY = {
    "tg_offset": 0,
    "proposals": {},
    "items": {},
    "images": [],
    "costs": {},
    "costs_log": [],
    "awaiting_comment": None,
    "channel_name": None,
    "last_long_proposal": None,
    "last_short_proposal": None,
    "budget_warned": None,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(STATE_DIR), *args], capture_output=True, text=True, check=check)


def _is_git() -> bool:
    return (STATE_DIR / ".git").exists()


def ensure_checkout() -> None:
    """Подключает ветку state в .state/ (создаёт orphan-ветку при первом запуске)."""
    if _is_git():
        return
    root = ["git", "-C", str(ROOT)]
    has_remote = subprocess.run([*root, "ls-remote", "--exit-code", "--heads", "origin", BRANCH],
                                capture_output=True).returncode == 0
    if has_remote:
        subprocess.run([*root, "fetch", "-q", "origin", f"{BRANCH}:{BRANCH}"], check=False)
        subprocess.run([*root, "worktree", "add", "-f", str(STATE_DIR), BRANCH], check=True, capture_output=True)
    else:
        subprocess.run([*root, "worktree", "add", "--orphan", "-b", BRANCH, str(STATE_DIR)],
                       check=True, capture_output=True)
        STATE_FILE.write_text(json.dumps(EMPTY, indent=1))
        _git("add", "state.json")
        _git("-c", "user.name=kanal-bot", "-c", "user.email=bot@users.noreply.github.com",
             "commit", "-qm", "init state")
        _git("push", "-q", "-u", "origin", BRANCH)


def load() -> dict:
    if _is_git():
        _git("fetch", "-q", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}", check=False)
        _git("reset", "-q", "--hard", f"origin/{BRANCH}", check=False)
    if not STATE_FILE.exists():
        STATE_DIR.mkdir(exist_ok=True)
        return json.loads(json.dumps(EMPTY))
    data = json.loads(STATE_FILE.read_text())
    for k, v in EMPTY.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    return data


def update(fn, message: str = "update state"):
    """Атомарно применяет fn(state) -> result и пушит. Возвращает result."""
    for attempt in range(8):
        st = load()
        result = fn(st)
        STATE_DIR.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(st, indent=1, ensure_ascii=False))
        if not _is_git():
            return result
        _git("add", "state.json")
        if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return result
        _git("-c", "user.name=kanal-bot", "-c", "user.email=bot@users.noreply.github.com",
             "commit", "-qm", message)
        if _git("push", "-q", "origin", f"HEAD:{BRANCH}", check=False).returncode == 0:
            return result
        time.sleep(2 + attempt * 2)
    raise RuntimeError("Не удалось записать state после 8 попыток")


def set_item(item_id: str, **fields) -> None:
    def fn(st):
        item = st["items"].setdefault(item_id, {"created": now()})
        item.update(fields)
        item["updated"] = now()
    update(fn, f"item {item_id}: {fields.get('status', 'update')}")


def get_item(item_id: str) -> dict:
    return load()["items"][item_id]
