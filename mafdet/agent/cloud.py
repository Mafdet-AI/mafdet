"""Models in the cloud for the agent: Qwen on Alibaba Cloud, Google Gemini, anything on OpenRouter.

All three speak the OpenAI chat API, so they go through the same ChatModel as a local engine; a provider
here is only where it is, which variable holds its key, the context to assume, and how it is told how
much to reason. `--provider gemini --model gemini-…` is `--url <its address> --engine openai --api-key …`
said shorter. The key comes from --api-key, the provider's environment variable, or the system's
credential store where `/model` keeps one typed in (keys.py): it is never written to agent.json.

Unlike the rest of MAFDET, this sends the conversation, files the model reads and tool outputs to the
provider. Nothing is sent before the user picks a provider.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    title: str
    url: str                    # the base, version included: /chat/completions and /models go after it
    keys: tuple[str, ...]       # environment variables that may hold the key, in order
    ctx: int                    # context assumed when --ctx does not say
    thinking: str               # how it is told how much to reason (model.reasoning_params)
    signup: str                 # where to get a key


PROVIDERS = {p.name: p for p in (
    Provider("qwen", "Qwen on Alibaba Cloud", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             ("DASHSCOPE_API_KEY", "QWEN_API_KEY"), 131072, "enable_thinking",
             "https://modelstudio.console.alibabacloud.com (Model Studio, API keys)"),
    Provider("qwen-cn", "Qwen on Alibaba Cloud (China)", "https://dashscope.aliyuncs.com/compatible-mode/v1",
             ("DASHSCOPE_API_KEY", "QWEN_API_KEY"), 131072, "enable_thinking",
             "https://bailian.console.aliyun.com (API keys)"),
    Provider("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
             ("GEMINI_API_KEY", "GOOGLE_API_KEY"), 131072, "reasoning_effort",
             "https://aistudio.google.com/apikey"),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
             ("OPENROUTER_API_KEY",), 131072, "reasoning", "https://openrouter.ai/keys"),
)}


def api_key(provider: Provider, given: str = "") -> str:
    """--api-key, else the environment, else the credential store (keys.py)."""
    if given:
        return given
    from . import keys
    return keys.get(provider.name, provider.keys)[0]


def list_models(provider: Provider, key: str, timeout: float = 20.0) -> list[str]:
    """The ids of the models the provider serves to this key (GET /models)."""
    req = urllib.request.Request(provider.url + "/models", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{provider.title} answered {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SystemExit(f"{provider.title} does not answer: {e}") from None
    ids = [str(m.get("id", "")) for m in data.get("data") or [] if isinstance(m, dict)]
    return sorted(i.removeprefix("models/") for i in ids if i)


def resolve(args) -> Provider | None:
    """With --provider, fill in what --url, --engine, --api-key and --ctx would say; print the models
    and exit with --list-models, or when no --model is given. Without --provider, nothing changes. Done
    once: called again, it gives back the provider."""
    if hasattr(args, "cloud"):
        return args.cloud
    args.cloud = None
    name = getattr(args, "provider", "") or ""
    if not name or name == "local":
        return None
    provider = PROVIDERS.get(name)
    if provider is None:
        raise SystemExit(f"Unknown provider {name!r}: {', '.join(PROVIDERS)}.")
    key = api_key(provider, getattr(args, "api_key", ""))
    if not key:
        raise SystemExit(f"{provider.title} needs an API key: set {provider.keys[0]}, pass --api-key, or add it "
                         f"in the agent with /model.\n"
                         f"Get one at {provider.signup}.")
    if getattr(args, "list_models", False) or not args.model:
        models = list_models(provider, key)
        if not args.model:
            print(f"Which model of {provider.title}? Pass it with --model, for example:", file=sys.stderr)
        print("\n".join(models) if models else "(the provider listed no models)")
        raise SystemExit(0 if getattr(args, "list_models", False) else 2)
    args.url, args.engine, args.api_key = provider.url, "openai", key
    args.ctx = args.ctx or provider.ctx
    args.thinking = provider.thinking
    print(f"{provider.title}: {args.model}. The conversation, the files the model reads and the tools' outputs "
          f"go to the provider.", file=sys.stderr, flush=True)
    args.cloud = provider
    return provider
