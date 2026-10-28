"""Учёт расходов. Всё пишется в state.json (ветка state) по месяцам."""
from datetime import datetime, timezone

from . import state
from .config import CFG

# $ за 1M токенов (input, output) — сверено с прайсом Anthropic на 2026-10.
LLM_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}


def month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def add(service: str, usd: float, note: str = "", **counters) -> None:
    def fn(st):
        m = st["costs"].setdefault(month(), {})
        m[service] = round(m.get(service, 0) + usd, 5)
        for k, v in counters.items():
            m[k] = m.get(k, 0) + v
        st["costs_log"] = (st["costs_log"] + [{"ts": state.now(), "service": service,
                                                "usd": round(usd, 5), "note": note}])[-300:]
    state.update(fn, f"cost {service} +${usd:.4f}")


def llm_cost(model: str, usage) -> float:
    pin, pout = LLM_PRICES.get(model, (2.0, 10.0))
    inp = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25 \
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
    return inp / 1e6 * pin + (usage.output_tokens or 0) / 1e6 * pout


def month_total(st: dict | None = None) -> tuple[float, dict]:
    st = st or state.load()
    m = st["costs"].get(month(), {})
    variable = sum(v for k, v in m.items() if k in ("anthropic", "fal", "pexels", "other"))
    fixed = sum(CFG["budget"]["fixed_monthly"].values())
    return round(variable + fixed, 2), m


def over_budget(extra_usd: float = 0) -> bool:
    total, _ = month_total()
    return total + extra_usd > CFG["budget"]["monthly_usd"]


def summary() -> str:
    total, m = month_total()
    limit = CFG["budget"]["monthly_usd"]
    chars = m.get("eleven_chars", 0)
    return (f"💰 {month()}: ${total:.2f} из ${limit} "
            f"(Anthropic ${m.get('anthropic', 0):.2f}, картинки ${m.get('fal', 0):.2f}, "
            f"ElevenLabs $22 фикс., {chars} симв. озвучки)")
