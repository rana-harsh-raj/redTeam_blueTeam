"""Credential-safe OpenAI-compatible client for the Razorpay LiteLLM gateway.

Stdlib-only (urllib) so no new dependency is added to the twin. Both the
primary Claude model and the reproducer GPT model speak the same
/v1/chat/completions + tool_calls schema through this gateway, so one client
drives both.

Security invariants:
- The API key is read from the environment via config and is NEVER logged,
  echoed, returned, or written to any campaign record. redact() scrubs it from
  any string before it is persisted.
- Callers persist normalized request/response records themselves; this module
  returns structured data and does no disk I/O.
"""
import json
import time
import urllib.error
import urllib.request

from . import config


class LLMError(RuntimeError):
    pass


def redact(text: str) -> str:
    """Remove any occurrence of the live API key from a string before it is
    persisted or shown. Defense in depth; callers should not pass the key at all."""
    if not text:
        return text
    try:
        key = config.gateway_key()
    except RuntimeError:
        return text
    if key and key in text:
        text = text.replace(key, "sk-***REDACTED***")
    return text


class Usage:
    """Running token/cost accounting across a session. Cost is UNKNOWN unless a
    price table is supplied; we record tokens and mark cost UNKNOWN otherwise."""

    def __init__(self):
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.by_model = {}

    def add(self, model, usage):
        self.calls += 1
        pt = int(usage.get("prompt_tokens", 0) or 0)
        ct = int(usage.get("completion_tokens", 0) or 0)
        tt = int(usage.get("total_tokens", 0) or (pt + ct))
        self.prompt_tokens += pt
        self.completion_tokens += ct
        self.total_tokens += tt
        m = self.by_model.setdefault(model, {"calls": 0, "prompt_tokens": 0,
                                             "completion_tokens": 0, "total_tokens": 0})
        m["calls"] += 1
        m["prompt_tokens"] += pt
        m["completion_tokens"] += ct
        m["total_tokens"] += tt

    def snapshot(self):
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": "UNKNOWN (no gateway price table exposed)",
            "by_model": self.by_model,
        }


class ChatClient:
    def __init__(self, model, usage=None, timeout=180, max_retries=4, max_tokens=8000):
        self.model = model
        self.usage = usage if usage is not None else Usage()
        self.timeout = timeout
        self.max_retries = max_retries
        self.default_max_tokens = max_tokens

    def complete(self, messages, tools=None, tool_choice="auto",
                 max_tokens=None, temperature=None, extra=None):
        """One chat completion. Returns a normalized dict:
        {role, content, tool_calls:[{id,name,arguments(str)}], finish_reason, raw_usage}."""
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": max_tokens or self.default_max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature
        if extra:
            payload.update(extra)

        body = json.dumps(payload).encode()
        url = config.gateway_base() + "/v1/chat/completions"
        last_err = None
        for attempt in range(self.max_retries):
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={
                    "Authorization": "Bearer " + config.gateway_key(),
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
                return self._normalize(data)
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode()[:500]
                except Exception:  # noqa: BLE001
                    pass
                last_err = "HTTP %s: %s" % (e.code, redact(detail))
                # 4xx other than 429 are not retryable.
                if e.code in (400, 401, 403, 404, 422):
                    raise LLMError(last_err)
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = redact(str(e))
            except Exception as e:  # noqa: BLE001
                last_err = redact(str(e))
            time.sleep(min(2 ** attempt, 20))
        raise LLMError("gateway call failed after %d attempts: %s" % (self.max_retries, last_err))

    def _normalize(self, data):
        choices = data.get("choices") or [{}]
        ch = choices[0]
        msg = ch.get("message", {}) or {}
        tool_calls = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {}) or {}
            tool_calls.append({
                "id": tc.get("id"),
                "name": fn.get("name"),
                "arguments": fn.get("arguments", "") or "",
            })
        usage = data.get("usage", {}) or {}
        self.usage.add(self.model, usage)
        return {
            "role": msg.get("role", "assistant"),
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
            "finish_reason": ch.get("finish_reason"),
            "raw_usage": usage,
        }


def is_unusable(resp):
    """Return a short reason string if a completion is blank / content-filtered /
    refused / interrupted (Workstream D), else None. A filtered turn produces no
    usable action and must be recovered, not counted as progress."""
    if resp is None:
        return "no_response"
    fr = (resp.get("finish_reason") or "").lower()
    if fr in ("content_filter", "content_management_policy"):
        return "content_filter"
    content = (resp.get("content") or "").strip()
    if not resp.get("tool_calls") and not content:
        return "blank_no_action"
    if fr == "length" and not resp.get("tool_calls") and len(content) < 3:
        return "truncated_empty"
    return None


def list_models(timeout=25):
    """Return the sorted list of model ids the gateway currently exposes, for
    fallback selection. Empty list on failure (never raises)."""
    try:
        req = urllib.request.Request(
            config.gateway_base() + "/v1/models",
            headers={"Authorization": "Bearer " + config.gateway_key()})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return sorted(m.get("id") for m in data.get("data", []) if m.get("id"))
    except Exception:  # noqa: BLE001
        return []


def pick_fallback(current, prefer_families=("claude", "gpt", "gemini"), available=None):
    """Choose a DIFFERENT approved large model than `current` from the live
    inventory, preferring the same-then-other strong families. Returns None if
    no distinct model is available. No hard-coded unavailable models."""
    available = available if available is not None else list_models()
    cand = [m for m in available if m != current]
    if not cand:
        return None
    # prefer a strong model of the current family first, then other families
    cur_fam = next((f for f in prefer_families if f in (current or "")), None)
    def score(m):
        ml = m.lower()
        fam_rank = 99
        for i, f in enumerate(prefer_families):
            if f in ml:
                fam_rank = i
                break
        same_fam = 0 if (cur_fam and cur_fam in ml) else 1
        mini = 1 if any(x in ml for x in ("mini", "nano", "small", "haiku", "lite")) else 0
        return (same_fam, fam_rank, mini, m)
    cand.sort(key=score)
    return cand[0]


def parse_tool_args(raw):
    """Tolerant JSON parse of a tool-call arguments string."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {"__unparsed__": str(raw)[:2000]}
