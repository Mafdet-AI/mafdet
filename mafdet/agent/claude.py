"""Claude for the agent, through Anthropic's own SDK and its Messages API.

The agent keeps its conversation in the OpenAI shape that every other engine takes (model.py); this
module turns it into a Messages request and the streamed answer back into a Reply, so the loop, the
interface and /model see one more model like the others. What Claude needs that the others do not:

  - Its thinking blocks go back unchanged on every later request: each Reply keeps the answer's content
    blocks as they came (`claude` in the assistant message, also saved with the session), and they are
    sent again as they are. The agent edits its history (old tool outputs cut to a line, tools turned on
    mid-session with find_tools), which would invalidate the blocks after the edit: the request asks the
    API to drop those blocks instead of refusing the request (`prefix_mismatch_behavior: "drop_block"`).
  - Reasoning is adaptive, and how much is the effort level: auto leaves the model's default, low,
    medium and high ask for that; off asks for low, since the current models cannot turn it off. Its
    summary streams as the reasoning the interface folds. No temperature: the current models refuse one.
  - A request a safety classifier declines is retried server-side on the model Anthropic recommends for
    that kind of decline (`fallbacks: "default"`), on the models that take it. When the answer crossed
    over mid-way, what the declined model wrote before the switch is echoed back the way the API wants
    it (no thinking or tool calls from before the `fallback` block).
  - The prompt is cached between steps (automatic caching), so each step pays for the new part only.

A feature a model does not take (an older Claude, a parameter it 400s on) is dropped for the session and
the request goes again without it, as model.py does for the other providers.
"""
from __future__ import annotations

import json

from .model import ModelError, Reply, Stopped

FALLBACK_BETA = "server-side-fallback-2026-07-01"
BINDING_BETA = "thinking-binding-controls-2026-08-01"
# The models that take `fallbacks: "default"`.
FALLBACK_MODELS = ("claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
MIN_TOKENS = 32000          # thinking counts against max_tokens: the agent's 4096 would cut it short
# The optional parts of a request, in the order they are given up when the API refuses one and does not
# say which: the most recent features first.
OPTIONAL = ("fallbacks", "block_binding", "display", "effort", "cache_control", "thinking")
FINISH = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length", "refusal": "refusal",
          "pause_turn": "stop", "stop_sequence": "stop"}


def list_models(key: str) -> list[str]:
    import anthropic
    try:
        return sorted(m.id for m in anthropic.Anthropic(api_key=key).models.list())
    except anthropic.APIStatusError as e:
        raise SystemExit(f"Anthropic Claude answered {e.status_code}: {_said(e)}") from None
    except anthropic.APIConnectionError as e:
        raise SystemExit(f"Anthropic Claude does not answer: {e}") from None


class ClaudeModel:
    llama = False
    thinking = "anthropic"
    url = "https://api.anthropic.com"

    def __init__(self, model: str, *, api_key: str, effort: str = "auto", timeout: float = 3600.0):
        import anthropic
        self.model = model
        self.api_key = api_key
        self.effort = effort
        self.refused: set[str] = set()
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, max_tokens: int = 4096,
             temperature: float = 0.6, think: bool = True, on_delta=None, should_stop=None) -> Reply:
        """One assistant turn, as ChatModel.chat. `temperature` is not sent: current Claude models refuse it."""
        import anthropic
        system, history = to_messages(messages)
        while True:
            params = self._params(system, history, tools, max_tokens, think)
            try:
                return self._stream(params, on_delta, should_stop)
            except anthropic.BadRequestError as e:
                drop = self._to_drop(_said(e))
                if drop is None:
                    raise ModelError(f"Claude answered 400: {_said(e)}") from None
                self.refused.add(drop)
            except anthropic.APIStatusError as e:
                raise ModelError(f"Claude answered {e.status_code}: {_said(e)}") from None
            except anthropic.APIConnectionError as e:
                raise ModelError(f"Claude does not answer: {e}") from None

    # ------------------------------------------------------------ the request

    def _params(self, system: str, history: list[dict], tools, max_tokens: int, think: bool) -> dict:
        on = lambda name: name not in self.refused           # noqa: E731
        params: dict = {"model": self.model, "max_tokens": max(max_tokens, MIN_TOKENS), "messages": history}
        if system:
            params["system"] = system
        if tools:
            params["tools"] = [to_tool(t) for t in tools]
        betas = []
        if on("thinking"):
            thinking: dict = {"type": "adaptive"}
            if on("display"):
                thinking["display"] = "summarized"
            if on("block_binding"):
                thinking["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
                betas.append(BINDING_BETA)
            params["thinking"] = thinking
        level = "low" if not think else self.effort if self.effort in ("low", "medium", "high") else ""
        if level and on("effort"):
            params["output_config"] = {"effort": level}
        if on("cache_control"):
            params["cache_control"] = {"type": "ephemeral"}
        if on("fallbacks") and self.model in FALLBACK_MODELS:
            params["fallbacks"] = "default"
            betas.append(FALLBACK_BETA)
        if betas:
            params["betas"] = betas
        return params

    def _to_drop(self, said: str) -> str | None:
        """The optional part a 400 is about: the one it names, else the next not given up yet."""
        left = [name for name in OPTIONAL if name not in self.refused]
        names = {"output_config": "effort", "budget_tokens": "thinking", "prefix_mismatch_behavior": "block_binding"}
        for word, name in [*names.items(), *((n, n) for n in OPTIONAL)]:
            if word in said and name in left:
                return name
        return left[0] if left else None

    # ------------------------------------------------------------ the answer

    def _stream(self, params: dict, on_delta, should_stop) -> Reply:
        with self.client.beta.messages.stream(**params) as stream:
            for event in stream:
                if should_stop is not None and should_stop():
                    raise Stopped                    # leaving the `with` closes the stream
                if event.type == "content_block_delta" and on_delta is not None:
                    kind = event.delta.type
                    if kind == "text_delta" and event.delta.text:
                        on_delta("text", event.delta.text)
                    elif kind == "thinking_delta" and event.delta.thinking:
                        on_delta("think", event.delta.thinking)
            final = stream.get_final_message()
        return to_reply(final)


def to_reply(final) -> Reply:
    blocks = [b.to_dict() for b in final.content]
    reply = Reply(claude=blocks)
    after = _after_fallback(blocks)
    for i, b in enumerate(blocks):
        kind = b.get("type")
        if kind == "text":
            reply.content += b.get("text", "")
        elif kind == "thinking":
            reply.reasoning += b.get("thinking", "")
        elif kind == "tool_use" and i >= after:
            reply.tool_calls.append({"id": b["id"], "name": b["name"],
                                     "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)})
    stop = final.stop_reason or ""
    reply.finish = FINISH.get(stop, stop)
    if stop == "max_tokens":
        reply.tool_calls = []                # a tool input cut off parses as a valid partial one: never run it
    if stop == "refusal":
        reply.tool_calls = []
        details = getattr(final, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        reply.content += ("\n\n" if reply.content else "") + (
            f"[Claude declined this request ({category})]" if category else "[Claude declined this request]")
    usage = final.usage
    reply.prompt_tokens = sum(getattr(usage, f, 0) or 0
                              for f in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    reply.completion_tokens = usage.output_tokens or 0
    return reply


def _after_fallback(blocks: list[dict]) -> int:
    """The index after the last `fallback` block (0 when there is none): what came before it was written by a
    model that then declined."""
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    return last + 1


# ------------------------------------------------------------ the conversation, OpenAI shape -> Messages

def to_tool(spec: dict) -> dict:
    fn = spec.get("function") or spec
    return {"name": fn["name"], "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            "eager_input_streaming": True}


def to_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """(system prompt, Messages history) from the agent's conversation."""
    system = ""
    out: list[dict] = []

    def add(role: str, blocks: list[dict]):
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)            # tool results of one turn go back in one message
        else:
            out.append({"role": role, "content": list(blocks)})

    for i, m in enumerate(messages):
        role, content = m.get("role"), m.get("content")
        if role == "system":
            if i == 0:
                system = str(content or "")
            else:
                add("user", [{"type": "text", "text": f"[system] {content}"}])
        elif role == "user":
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            add("user", [{"type": "text", "text": text or "(empty)"}])
        elif role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                          "content": str(content or "(no output)")}])
        elif role == "assistant":
            add("assistant", _assistant_blocks(m))
    return system, out


def _assistant_blocks(m: dict) -> list[dict]:
    native = m.get("claude")
    if native:                                       # Claude's own blocks, thinking and signatures as they came
        boundary = _after_fallback(native)
        kept = [b for i, b in enumerate(native)
                if i >= boundary or b.get("type") in ("text", "fallback")]
        return [_clean(b) for b in kept]
    blocks: list[dict] = []
    if m.get("content"):
        blocks.append({"type": "text", "text": str(m["content"])})
    for call in m.get("tool_calls") or []:           # written by another model: no thinking to give back
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {"arguments": fn.get("arguments")}
        blocks.append({"type": "tool_use", "id": call.get("id", ""), "name": fn.get("name", ""),
                       "input": args if isinstance(args, dict) else {"value": args}})
    return blocks


def _clean(block: dict) -> dict:
    """A response block as a request takes it: `citations` and `caller` are output-only on a text or
    tool_use block and come back as None."""
    return {k: v for k, v in block.items() if v is not None}


def _said(e) -> str:
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:500]
    return str(getattr(e, "message", e))[:500]
