"""The model behind the agent: one streamed chat completion with tools, on an OpenAI-compatible
endpoint (llama-server's, or any other engine's).

llama-server parses the tool calls of the model's own chat template and constrains them with a
grammar, so any GGUF with a tool-calling template works, not only Qwen3. The conversation is pinned
to one slot and its prompt cached: an agent's prompt only grows, so each step reads just the last
tool result.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class ModelError(RuntimeError):
    pass


@dataclass
class Reply:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)     # {"id", "name", "arguments" (a JSON string)}
    finish: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def message(self) -> dict:
        """The assistant message as it goes back into the history: the reasoning stays out, as in the
        chat templates (and as the app does for past turns: the context lasts several times longer)."""
        msg: dict = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["name"], "arguments": c["arguments"]}}
                                 for c in self.tool_calls]
        return msg


class ChatModel:
    def __init__(self, url: str, model: str = "default", *, llama: bool = True, api_key: str = "", slot: int = 0,
                 timeout: float = 3600.0):
        self.url = url.rstrip("/")
        self.model = model
        self.llama = llama
        self.api_key = api_key
        self.slot = slot
        self.timeout = timeout

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, max_tokens: int = 4096,
             temperature: float = 0.6, think: bool = True, on_delta=None) -> Reply:
        """One assistant turn. `on_delta(kind, text)` gets "think" and "text" pieces as they are written."""
        body: dict = {"model": self.model, "messages": messages, "stream": True, "max_tokens": max_tokens,
                      "temperature": temperature, "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = tools
        if self.llama:
            body["cache_prompt"] = True
            body["id_slot"] = self.slot
            if not think:
                body["chat_template_kwargs"] = {"enable_thinking": False}
        elif not think:
            body["reasoning_effort"] = "none"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url + "/v1/chat/completions", json.dumps(body).encode(), headers)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise ModelError(f"the engine answered {e.code}: {_error_text(e.read())}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ModelError(f"the engine at {self.url} does not answer: {e}") from None
        reply = Reply()
        calls: dict[int, dict] = {}
        with resp:
            for raw in resp:
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
                        call = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                        fn = tc.get("function") or {}
                        call["id"] = tc.get("id") or call["id"]
                        call["name"] += fn.get("name") or ""
                        call["arguments"] += fn.get("arguments") or ""
                    reply.finish = choice.get("finish_reason") or reply.finish
        for i in sorted(calls):
            call = calls[i]
            call["id"] = call["id"] or f"call_{i}"
            reply.tool_calls.append(call)
        return reply


def _error_text(data: bytes) -> str:
    try:
        err = json.loads(data).get("error")
        return str(err.get("message") if isinstance(err, dict) else err)[:500]
    except (ValueError, AttributeError):
        return data[:500].decode("utf-8", "replace")
