"""DeepSeek client.

OpenAI-compatible endpoint, so the `openai` package works with a swapped base_url.

Prompt assembly order matters for cost: DeepSeek caches repeated prefixes
automatically, so the static instruction block goes in the system message and
everything volatile (post text, candidate summaries) goes in the user message.
Any drift in the system message silently zeroes the cache hit rate — which is
why tokens_cached is reported per run.
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from newsai.lang import LANG, LANGUAGE

PROMPTS = Path(__file__).parent / "prompts"


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0

    def __iadd__(self, other):
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.tokens_cached += other.tokens_cached
        return self


class DryRun(Exception):
    pass


def _load_env():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

MODEL_A = os.environ.get("MODEL_A", "deepseek-chat")
MODEL_B = os.environ.get("MODEL_B", "deepseek-chat")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

_client = None


def client():
    global _client
    if _client is None:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise DryRun("DEEPSEEK_API_KEY not set")
        from openai import OpenAI
        _client = OpenAI(api_key=key, base_url=BASE_URL)
    return _client


def prompt(name: str) -> str:
    """Language-specific prompt if one exists (prompts/<lang>/), else the
    language-neutral one (prompts/default/) told which language to answer in."""
    path = PROMPTS / LANG / f"{name}.txt"
    if not path.exists():
        path = PROMPTS / "default" / f"{name}.txt"
    return path.read_text(encoding="utf-8").replace("{LANGUAGE}", LANGUAGE)


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _parse(content: str) -> dict:
    """The prompts forbid fences; strip them anyway rather than lose a whole run."""
    cleaned = _FENCE.sub("", content).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def call(system: str, user: str, model: str, max_tokens: int = 800) -> tuple:
    """Returns (parsed_json, Usage). Raises DryRun when no key is configured."""
    resp = client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},   # static — the cached prefix
            {"role": "user", "content": user},       # volatile — never cached
        ],
        temperature=0.0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    u = resp.usage
    usage = Usage(
        tokens_in=u.prompt_tokens,
        tokens_out=u.completion_tokens,
        tokens_cached=getattr(u, "prompt_cache_hit_tokens", 0) or 0,
    )
    return _parse(resp.choices[0].message.content), usage
