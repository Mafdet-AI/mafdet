"""The model behind the agent: one streamed chat completion with tools, on an OpenAI-compatible
endpoint (llama-server's, any other engine's, or a provider's in the cloud: cloud.py).

llama-server parses the tool calls of the model's own chat template and constrains them with a
grammar, so any GGUF with a tool-calling template works, not only Qwen3. The conversation is pinned
to one slot and its prompt cached: an agent's prompt only grows, so each step reads just the last
tool result.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class ModelError(RuntimeError):
    pass


class Stopped(Exception):
    """The user stopped the turn (Esc in the terminal app)."""


@dataclass
class Reply:
    content: str = ""
    reasoning: str = ""
    # {"id", "name", "arguments" (a JSON string)}, and "extra_content" when the provider sent one (Gemini's
    # thought signature, which it wants back with the call)
    tool_calls: list[dict] = field(default_factory=list)
    finish: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def message(self) -> dict:
        """The assistant message as it goes back into the history: the reasoning stays out, as in the
        chat templates (and as the app does for past turns: the context lasts several times longer)."""
        msg: dict = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["name"], "arguments": c["arguments"]},
                                  **({"extra_content": c["extra_content"]} if c.get("extra_content") else {})}
                                 for c in self.tool_calls]
        return msg


# Parameters sent when they help but that not every provider takes: a 400 naming one of them (or naming
# nothing) makes the request go again without them, and they stay off for the session.
OPTIONAL = ("stream_options", "reasoning_effort", "enable_thinking")


def completions_url(url: str) -> str:
    """Where the chat completions are: after the base when it already names an API version (a provider's
    /v1, /compatible-mode/v1, /v1beta/openai), else under /v1 as llama-server and most engines have them."""
    url = url.rstrip("/")
    if re.search(r"/v\d+[a-z]*(/openai)?$", url):
        return url + "/chat/completions"
    return url + "/v1/chat/completions"


class ChatModel:
    def __init__(self, url: str, model: str = "default", *, llama: bool = True, api_key: str = "", slot: int = 0,
                 timeout: float = 3600.0, thinking: str = "reasoning_effort"):
        """`thinking`, for an engine other than llama-server: how it is told to reason or not,
        "reasoning_effort" (OpenAI's way, Gemini, OpenRouter) or "enable_thinking" (Qwen on Alibaba Cloud)."""
        self.url = url.rstrip("/")
        self.model = model
        self.llama = llama
        self.api_key = api_key
        self.slot = slot
        self.timeout = timeout
        self.thinking = thinking
        self.refused: set[str] = set()        # OPTIONAL parameters this engine said no to

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, max_tokens: int = 4096,
             temperature: float = 0.6, think: bool = True, on_delta=None, should_stop=None) -> Reply:
        """One assistant turn. `on_delta(kind, text)` gets "think" and "text" pieces as they are written;
        `should_stop()` turning true ends it with Stopped, and the engine stops writing."""
        body: dict = {"model": self.model, "messages": messages, "stream": True, "max_tokens": max_tokens,
                      "temperature": temperature, "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = tools
        if self.llama:
            body["cache_prompt"] = True
            body["id_slot"] = self.slot
            if not think:
                body["chat_template_kwargs"] = {"enable_thinking": False}
        elif self.thinking == "enable_thinking":
            body["enable_thinking"] = bool(think)
        elif not think:
            body["reasoning_effort"] = "none"
        resp = self._post(body)
        reply = Reply()
        calls: dict[int, dict] = {}
        with resp:
            for raw in resp:
                if should_stop is not None and should_stop():
                    raise Stopped                 # leaving the `with` closes the stream: llama-server stops there
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise ModelError(_error_text(json.dumps(chunk).encode()))
                usage = chunk.get("usage") or {}
                reply.prompt_tokens = usage.get("prompt_tokens") or reply.prompt_tokens
                reply.completion_tokens = usage.get("completion_tokens") or reply.completion_tokens
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if thought:
                        reply.reasoning += thought
                        if on_delta:
                            on_delta("think", thought)
                    if delta.get("content"):
                        reply.content += delta["content"]
                        if on_delta:
                            on_delta("text", delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        call = calls.setdefault(_call_index(tc, calls), {"id": "", "name": "", "arguments": ""})
                        fn = tc.get("function") or {}
                        call["id"] = tc.get("id") or call["id"]
                        call["name"] += fn.get("name") or ""
                        call["arguments"] += fn.get("arguments") or ""
                        if tc.get("extra_content"):
                            call["extra_content"] = tc["extra_content"]
                    reply.finish = choice.get("finish_reason") or reply.finish
        for i in sorted(calls):
            call = calls[i]
            call["id"] = call["id"] or f"call_{i}"
            reply.tool_calls.append(call)
        return reply


    def _post(self, body: dict):
        """The request, going again without the OPTIONAL parameters the engine refuses."""
        for key in self.refused:
            body.pop(key, None)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        while True:
            req = urllib.request.Request(completions_url(self.url), json.dumps(body).encode(), headers)
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                text = _error_text(e.read())
                sent = [k for k in OPTIONAL if k in body]
                named = [k for k in sent if k in text]
                if e.code == 400 and sent and not self.llama:
                    for key in named or sent:
                        body.pop(key)
                        self.refused.add(key)
                    continue
                raise ModelError(f"the engine answered {e.code}: {text}") from None
            except (urllib.error.URLError, OSError) as e:
                raise ModelError(f"the engine at {self.url} does not answer: {e}") from None


def _call_index(tc: dict, calls: dict[int, dict]) -> int:
    """Which call a streamed piece belongs to. Most engines say it (`index`); Gemini sends each call whole,
    without one: a new id is a new call, no id goes on with the last."""
    if tc.get("index") is not None:
        return int(tc["index"])
    for i, call in calls.items():
        if tc.get("id") and call["id"] == tc["id"]:
            return i
    if tc.get("id") or not calls:
        return max(calls, default=-1) + 1
    return max(calls)


def _error_text(data: bytes) -> str:
    try:
        err = json.loads(data).get("error")
        return str(err.get("message") if isinstance(err, dict) else err)[:500]
    except (ValueError, AttributeError):
        return data[:500].decode("utf-8", "replace")
