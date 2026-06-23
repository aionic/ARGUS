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

from agent_framework import Agent, ChatOptions, Content, Message, Role

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


# ─────────────────────────────────────────────────────────────────────────────
# Message / content builders
# ─────────────────────────────────────────────────────────────────────────────
def text_content(text: str) -> Content:
    return Content.from_text(text)


def image_content(b64_png: str) -> Content:
    """Build an image content part from a base64-encoded PNG."""
    return Content.from_uri(uri=f"data:image/png;base64,{b64_png}", media_type="image/png")


def user_message(contents: Sequence[Content]) -> Message:
    return Message(Role.USER, list(contents))


def assistant_message(contents: Sequence[Content]) -> Message:
    return Message(Role.ASSISTANT, list(contents))


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
# Credential + chat client (created once, on the background loop)
# ─────────────────────────────────────────────────────────────────────────────
_credential = None
_client = None
_client_lock = asyncio.Lock()


def _get_credential():
    global _credential
    if _credential is None:
        from azure.identity.aio import DefaultAzureCredential

        # Honours AZURE_CLIENT_ID for user-assigned managed identity in the
        # container; falls back to the developer credential locally.
        _credential = DefaultAzureCredential()
    return _credential


async def _get_client():
    """Build (and cache) the Agent Framework chat client.

    Prefers Azure AI Foundry (project endpoint); falls back to Azure OpenAI.
    """
    global _client
    if _client is not None:
        return _client

    async with _client_lock:
        if _client is not None:
            return _client

        credential = _get_credential()
        model = os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME")
        project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")

        if project_endpoint:
            from agent_framework_foundry import FoundryChatClient

            logger.info("Agent Framework: using Azure AI Foundry project endpoint")
            _client = FoundryChatClient(
                project_endpoint=project_endpoint,
                model=model,
                credential=credential,
            )
        else:
            from agent_framework_openai import OpenAIChatClient

            azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
            api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
            logger.info("Agent Framework: no Foundry project set, falling back to Azure OpenAI endpoint")
            _client = OpenAIChatClient(
                model=model,
                azure_endpoint=azure_endpoint,
                api_version=api_version,
                credential=credential,
            )

    return _client


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
) -> ChatResult:
    client = await _get_client()
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
    return ChatResult(
        text=response.text or "",
        value=getattr(response, "value", None),
        finish_reason=_extract_finish_reason(response),
        tool_calls=_extract_tool_calls(response),
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
) -> ChatResult:
    """Run an Agent Framework chat turn synchronously (safe from worker threads)."""
    return _get_loop().run(
        _arun_chat(
            messages,
            instructions=instructions,
            response_format=response_format,
            tools=tools,
            seed=seed,
            temperature=temperature,
            max_tokens=max_tokens,
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
    )
