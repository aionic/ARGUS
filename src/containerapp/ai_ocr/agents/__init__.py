"""Microsoft Agent Framework integration for ARGUS.

Provides a single chat entry point (`run_chat_sync` / `run_chat`) backed by the
Microsoft Agent Framework. The primary client targets Azure AI Foundry
(`FoundryChatClient`) using the project endpoint; when no Foundry project is
configured (e.g. pure local dev) it falls back to Azure OpenAI via
`OpenAIChatClient`. Both authenticate with a managed identity / developer
credential — no API keys.
"""

from .client import (
    ChatResult,
    run_chat,
    run_chat_sync,
    user_message,
    assistant_message,
    text_content,
    image_content,
)

__all__ = [
    "ChatResult",
    "run_chat",
    "run_chat_sync",
    "user_message",
    "assistant_message",
    "text_content",
    "image_content",
]
