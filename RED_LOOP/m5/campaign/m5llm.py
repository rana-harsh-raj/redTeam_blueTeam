"""Minimal, credential-safe LiteLLM gateway client for the M5 campaign.

Stdlib-only. The API key is read from the environment and is NEVER logged,
persisted, or placed in any campaign record; redact() scrubs it defensively.
Callers persist the normalized request/response themselves.
"""
import json
import os
import time
import urllib.error
import urllib.request


def _base():
    b = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    if not b:
        raise RuntimeError("LITELLM_BASE_URL not set (source RED_LOOP/llm.env)")
    return b


def _key():
    k = os.environ.get("LITELLM_API_KEY", "")
    if not k:
        raise RuntimeError("LITELLM_API_KEY not set (source RED_LOOP/llm.env)")
    return k


def redact(text):
    if not text:
        return text
    try:
        k = _key()
    except RuntimeError:
        return text
    return text.replace(k, "sk-***REDACTED***") if k and k in text else text


class Usage:
    def __init__(self):
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.by_model = {}

    def add(self, model, u):
        self.calls += 1
        pt = int(u.get("prompt_tokens", 0) or 0)
        ct = int(u.get("completion_tokens", 0) or 0)
        self.prompt_tokens += pt
        self.completion_tokens += ct
        m = self.by_model.setdefault(model, {"calls": 0, "prompt_tokens": 0,
                                             "completion_tokens": 0})
        m["calls"] += 1
        m["prompt_tokens"] += pt
        m["completion_tokens"] += ct

    def as_dict(self):
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "by_model": self.by_model}


def chat(model, messages, *, temperature=0.7, max_tokens=1200, usage=None,
         timeout=90, retries=3):
    """One chat completion. Returns (content_str, raw_usage_dict). Adapts to
    per-model param quirks (some models accept only the default temperature)."""
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens}
    last = None
    for attempt in range(retries):
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{_base()}/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {_key()}",
                     "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
            content = d["choices"][0]["message"]["content"]
            u = d.get("usage", {})
            if usage is not None:
                usage.add(model, u)
            return content, u
        except urllib.error.HTTPError as e:
            detail = redact(e.read().decode(errors="ignore")[:300])
            last = f"HTTP {e.code}: {detail}"
            if e.code == 400 and "temperature" in detail and "temperature" in payload:
                payload.pop("temperature")          # model wants default temp
                continue
            if e.code == 400 and "max_tokens" in detail:
                payload["max_completion_tokens"] = payload.pop("max_tokens", max_tokens)
                continue
            if e.code in (429, 502, 503, 529):
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        except Exception as e:  # noqa: BLE001
            last = redact(str(e))
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"chat failed for {model}: {last}")


def extract_json(text):
    """Pull the first JSON object out of a model reply (handles code fences and
    trailing prose). Returns dict or None."""
    if not text:
        return None
    t = text.strip()
    if "```" in t:
        # take the content of the first fenced block
        parts = t.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                t = p
                break
    # find first balanced object
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    # tolerate trailing commas / minor issues
                    try:
                        import ast
                        return ast.literal_eval(t[start:i + 1])
                    except Exception:
                        return None
    return None
