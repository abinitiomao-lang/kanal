"""Точка входа для workflow'ов: python -m kanal.cli <команда> [аргументы]."""
import os
import sys

from . import bot, pipeline, selftest, state, youtube
from .config import CFG


def main() -> None:
    cmd, args = sys.argv[1], sys.argv[2:]
    run_url = os.environ.get("RUN_URL", "")
    if cmd not in ("selftest", "groups", "voices"):
        state.ensure_checkout()
    try:
        if cmd == "poll":
            bot.poll(CFG["bot"]["poll_seconds"])
        elif cmd == "daily":
            bot.daily()
        elif cmd == "short":
            pipeline.produce_short(args[0])
        elif cmd == "long-prepare":
            pipeline.long_prepare(args[0])
        elif cmd == "long-render":
            pipeline.long_render(args[0], int(args[1]), int(args[2]))
        elif cmd == "long-assemble":
            pipeline.long_assemble(args[0])
        elif cmd == "selftest":
            selftest.run()
        elif cmd == "voices":
            selftest.voice_samples()
        elif cmd == "preview-loop":
            selftest.preview_loop(" ".join(args))
        elif cmd == "wipe":
            youtube.wipe(args[:1] == ["DELETE"])
        elif cmd == "edit":
            e = os.environ
            youtube.edit(e["EDIT_MATCH"], e.get("EDIT_TITLE", ""), e.get("EDIT_DESC", ""),
                         [t.strip() for t in e.get("EDIT_TAGS", "").split(",") if t.strip()])
        elif cmd == "groups":
            n = min(CFG["long"]["render_groups"], CFG["long"]["chapters"])
            print("[" + ",".join(str(i) for i in range(n)) + "]")
        else:
            raise SystemExit(f"unknown command {cmd}")
    except Exception as e:
        if cmd in ("short", "long-prepare", "long-render", "long-assemble"):
            pipeline.fail(args[0], e, run_url)
        raise


if __name__ == "__main__":
    main()
