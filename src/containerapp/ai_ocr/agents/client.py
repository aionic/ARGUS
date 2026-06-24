"""Agent Framework chat client + a thread-safe sync bridge.

All Agent Framework clients are async. ARGUS calls the LLM from both async
FastAPI handlers and synchronous worker threads (document processing). To keep a
single implementation and avoid event-loop/credential lifecycle problems, every
call runs on one persistent background event loop:

* `run_chat_sync(...)` submits the coroutine to that loop and blocks for the
  result — safe to call from synchronous code (e.g. `ai_ocr/chains.py`).
* `run_chat(...)` is an async wrapper that offloads to a worker thread via
  `asyncio.to_thread`, so async handlers never block their own event loop.
"""

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from agent_framework import Agent, ChatOptions, Content, Message

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ChatResult:
    """Normalized result of an Agent Framework run."""

    text: str
    value: Any = None
    finish_reason: Optional[str] = None
    tool_calls: list = field(default_factory=list)
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    model: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Message / content builders
# ─────────────────────────────────────────────────────────────────────────────
def text_content(text: str) -> Content:
    return Content.from_text(text)


def image_content(b64_png: str) -> Content:
    """Build an image content part from a base64-encoded PNG."""
    return Content.from_uri(uri=f"data:image/png;base64,{b64_png}", media_type="image/png")


def user_message(contents: Sequence[Content]) -> Message:
    return Message("user", list(contents))


def assistant_message(contents: Sequence[Content]) -> Message:
    return Message("assistant", list(contents))


def _extract_tool_calls(response: Any) -> list:
    """Best-effort extraction of function calls auto-invoked during the run."""
    calls: list = []
    try:
        messages = getattr(response, "messages", None) or []
        for msg in messages:
            for content in getattr(msg, "contents", None) or []:
                if getattr(content, "type", None) == "function_call":
                    args = getattr(content, "arguments", None)
                    calls.append(
                        {
                            "tool": getattr(content, "name", None),
                            "arguments": args if isinstance(args, dict) else str(args),
                        }
                    )
    except Exception:  # noqa: BLE001 - diagnostics only
        pass
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# Background event loop (single, persistent)
# ─────────────────────────────────────────────────────────────────────────────
class _LoopThread:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="argus-agents-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


_loop_thread: Optional[_LoopThread] = None
_loop_lock = threading.Lock()


def _get_loop() -> _LoopThread:
    global _loop_thread
    if _loop_thread is None:
        with _loop_lock:
            if _loop_thread is None:
                _loop_thread = _LoopThread()
    return _loop_thread


# ─────────────────────────────────────────────────────────────────────────────
# Credential + chat clients (cached per model deployment, on the background loop)
# ─────────────────────────────────────────────────────────────────────────────
_credential = None
# One chat client per model-deployment name. Keyed by the resolved deployment
# name so callers can route specific stages (e.g. the summary) to a cheaper
# model without rebuilding clients on every call.
_clients: dict[str, Any] = {}
_client_lock = asyncio.Lock()


def _get_credential():
    global _credential
    if _credential is None:
        from azure.identity.aio import DefaultAzureCredential

        # Honours AZURE_CLIENT_ID for user-assigned managed identity in the
        # container; falls back to the developer credential locally.
        _credential = DefaultAzureCredential()
    return _credential


async def _get_client(model: Optional[str] = None):
    """Build (and cache) an Agent Framework chat client for ``model``.

    ``model`` is a model-deployment name. When ``None`` the default deployment
    (``AZURE_OPENAI_MODEL_DEPLOYMENT_NAME``) is used. Clients are cached per
    deployment so different pipeline stages can target different models.

    Prefers Azure AI Foundry (project endpoint); falls back to Azure OpenAI.
    """
    resolved_model = model or os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME")
    cache_key = resolved_model or "__default__"

    cached = _clients.get(cache_key)
    if cached is not None:
        return cached

    async with _client_lock:
        cached = _clients.get(cache_key)
        if cached is not None:
            return cached

        credential = _get_credential()
        project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")

        if project_endpoint:
            from agent_framework_foundry import FoundryChatClient

            logger.info("Agent Framework: using Azure AI Foundry project endpoint (model=%s)", resolved_model)
            client = FoundryChatClient(
                project_endpoint=project_endpoint,
                model=resolved_model,
                credential=credential,
            )
        else:
            from agent_framework_openai import OpenAIChatClient

            azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
            api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
            logger.info(
                "Agent Framework: no Foundry project set, falling back to Azure OpenAI endpoint (model=%s)",
                resolved_model,
            )
            client = OpenAIChatClient(
                model=resolved_model,
                azure_endpoint=azure_endpoint,
                api_version=api_version,
                credential=credential,
            )

        _clients[cache_key] = client

    return client


def _extract_finish_reason(response: Any) -> Optional[str]:
    """Best-effort finish_reason extraction from the underlying response."""
    try:
        messages = getattr(response, "messages", None) or []
        for msg in reversed(messages):
            raw = getattr(msg, "raw_representation", None)
            choices = getattr(raw, "choices", None)
            if choices:
                fr = getattr(choices[0], "finish_reason", None)
                if fr:
                    return str(fr)
    except Exception:  # noqa: BLE001 - diagnostics only
        pass
    return None


def _usage_value(source: Any, *names: str) -> Optional[int]:
    """Read a usage value from dict-like or attribute-based provider shapes."""
    for name in names:
        try:
            value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
            if value is not None:
                return int(value)
        except Exception:  # noqa: BLE001 - diagnostics only
            continue
    return None


def _extract_usage(response: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Best-effort token usage extraction from Agent Framework/OpenAI responses."""
    try:
        usage_details = getattr(response, "usage_details", None)
        if usage_details:
            return (
                _usage_value(usage_details, "input_token_count"),
                _usage_value(usage_details, "output_token_count"),
                _usage_value(usage_details, "total_token_count"),
            )
    except Exception:  # noqa: BLE001 - diagnostics only
        pass

    try:
        candidates = [response]
        raw = getattr(response, "raw_representation", None)
        if raw is not None:
            candidates.append(raw)
            nested_raw = getattr(raw, "raw_representation", None)
            if nested_raw is not None:
                candidates.append(nested_raw)

        for candidate in candidates:
            usage = getattr(candidate, "usage", None)
            if not usage:
                continue
            return (
                _usage_value(usage, "prompt_tokens", "input_tokens", "input_token_count"),
                _usage_value(usage, "completion_tokens", "output_tokens", "output_token_count"),
                _usage_value(usage, "total_tokens", "total_token_count"),
            )
    except Exception:  # noqa: BLE001 - diagnostics only
        pass

    return None, None, None


# ─────────────────────────────────────────────────────────────────────────────
# Core run
# ─────────────────────────────────────────────────────────────────────────────
async def _arun_chat(
    messages: Sequence[Message],
    *,
    instructions: Optional[str],
    response_format: Any,
    tools: Optional[Sequence[Callable[..., Any]]],
    seed: Optional[int],
    temperature: Optional[float],
    max_tokens: Optional[int],
    model: Optional[str] = None,
) -> ChatResult:
    resolved_model = model or os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME")
    client = await _get_client(resolved_model)
    agent = Agent(client=client, instructions=instructions, tools=tools)

    opts: dict[str, Any] = {}
    if response_format is not None:
        opts["response_format"] = response_format
    if seed is not None:
        opts["seed"] = seed
    if temperature is not None:
        opts["temperature"] = temperature
    if max_tokens is not None:
        opts["max_tokens"] = max_tokens
    options = ChatOptions(**opts) if opts else None

    response = await agent.run(list(messages), options=options)
    input_tokens, output_tokens, total_tokens = _extract_usage(response)
    return ChatResult(
        text=response.text or "",
        value=getattr(response, "value", None),
        finish_reason=_extract_finish_reason(response),
        tool_calls=_extract_tool_calls(response),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        model=resolved_model,
    )


def run_chat_sync(
    messages: Sequence[Message],
    *,
    instructions: Optional[str] = None,
    response_format: Any = None,
    tools: Optional[Sequence[Callable[..., Any]]] = None,
    seed: Optional[int] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model: Optional[str] = None,
) -> ChatResult:
    """Run an Agent Framework chat turn synchronously (safe from worker threads).

    ``model`` optionally overrides the model deployment for this call (e.g. to
    route the summary stage to a cheaper deployment); ``None`` uses the default.
    """
    return _get_loop().run(
        _arun_chat(
            messages,
            instructions=instructions,
            response_format=response_format,
            tools=tools,
            seed=seed,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )
    )


async def run_chat(
    messages: Sequence[Message],
    *,
    instructions: Optional[str] = None,
    response_format: Any = None,
    tools: Optional[Sequence[Callable[..., Any]]] = None,
    seed: Optional[int] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model: Optional[str] = None,
) -> ChatResult:
    """Async wrapper for use inside FastAPI handlers without blocking the loop."""
    return await asyncio.to_thread(
        run_chat_sync,
        messages,
        instructions=instructions,
        response_format=response_format,
        tools=tools,
        seed=seed,
        temperature=temperature,
        max_tokens=max_tokens,
        model=model,
    )
