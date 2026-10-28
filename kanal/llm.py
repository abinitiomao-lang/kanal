"""Обёртка над Anthropic API: текст/JSON + подсчёт стоимости (сбрасывается в state одним коммитом)."""
import anthropic
from pydantic import BaseModel

from . import costs
from .config import CFG, secret

_client = None
_spent = 0.0


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"), max_retries=4)
    return _client


def _track(model, usage):
    global _spent
    _spent += costs.llm_cost(model, usage)


def _check(resp):
    if resp.stop_reason == "refusal":
        raise RuntimeError("Модель отказалась отвечать (refusal)")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Ответ обрезан по max_tokens")


def text(prompt: str, system: str = "", role: str = "writer", max_tokens: int = 16000) -> str:
    model = CFG["models"][role]
    with client().messages.stream(
        model=model, max_tokens=max_tokens, system=system,
        output_config={"effort": CFG["models"][f"{role}_effort"]},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        resp = stream.get_final_message()
    _track(model, resp.usage)
    _check(resp)
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def json(prompt: str, schema: type[BaseModel], system: str = "", role: str = "writer",
         max_tokens: int = 16000) -> BaseModel:
    model = CFG["models"][role]
    resp = client().messages.parse(
        model=model, max_tokens=max_tokens, system=system,
        output_config={"effort": CFG["models"][f"{role}_effort"]},
        messages=[{"role": "user", "content": prompt}],
        output_format=schema,
    )
    _track(model, resp.usage)
    _check(resp)
    return resp.parsed_output


def flush(note: str) -> float:
    """Записывает накопленные расходы в state. Вызывать в конце job'а."""
    global _spent
    spent, _spent = _spent, 0.0
    if spent:
        costs.add("anthropic", spent, note)
    return spent
