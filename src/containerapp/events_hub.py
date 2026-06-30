"""
Server-Sent Events (SSE) hub for live document-update push.

Architecture
------------
Each backend replica runs its OWN Cosmos change-feed reader and fans changes out
to the SSE clients connected to *that* replica. Azure Container Apps has no session
affinity, so a browser's EventSource may be pinned to a different replica than the
one that processed a given blob. Because every replica tails the same change feed,
every connected client still receives every document update -- no cross-replica
message bus required.

Flow:  Cosmos change feed --(per-replica poller)--> in-process async hub
       --(asyncio.Queue per client)--> EventSourceResponse --> browser EventSource
"""

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_POLL_INTERVAL = float(os.getenv("SSE_CHANGEFEED_POLL_SECONDS", "2"))
# Bound each subscriber queue so a slow/stalled client can't grow memory without
# limit. On overflow we drop the oldest event (the client will recover on its next
# full reload / targeted refetch).
_QUEUE_MAXSIZE = int(os.getenv("SSE_QUEUE_MAXSIZE", "1000"))


class EventHub:
    """In-process async publish/subscribe hub for document update events."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # -- subscription management ------------------------------------------------
    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.add(queue)
        logger.info("SSE client subscribed (total=%d)", len(self._subscribers))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
        logger.info("SSE client unsubscribed (total=%d)", len(self._subscribers))

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers)
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event to make room (best-effort liveness).
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:  # noqa: BLE001
                    pass

    # -- background change-feed poller -----------------------------------------
    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="sse-changefeed-poller")
            logger.info("SSE change-feed poller started (interval=%ss)", _POLL_INTERVAL)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        logger.info("SSE change-feed poller stopped")

    async def _run(self) -> None:
        # Imported lazily to avoid an import cycle at module load time.
        from dependencies import get_data_container

        continuation: str | None = None
        # Prime from "now" so we only emit changes that happen after startup.
        # NOTE: We must use the "Now" string literal, NOT a datetime object.
        # azure-cosmos 4.9.0 has a bug in the PointInTime (datetime) start mode:
        # ChangeFeedStartFromPointInTime serializes the start time as milliseconds
        # but deserializes it with datetime.fromtimestamp() (which expects seconds),
        # corrupting the internal state into "year 58466 is out of range" on every
        # poll. The "Now" mode uses a different, unaffected start-from subclass.
        start_time: str | None = "Now"

        while not self._stop.is_set():
            try:
                container = get_data_container()
                if container is None:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue
                changes, continuation = await asyncio.to_thread(_read_changes, container, continuation, start_time)
                start_time = None  # only used for the very first call
                for doc in changes:
                    event = _to_event(doc)
                    if event is not None:
                        await self.publish(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("SSE change-feed poll error: %s", exc)
            await asyncio.sleep(_POLL_INTERVAL)


def _read_changes(container, continuation: str | None, start_time):
    """Synchronous Cosmos change-feed read. Returns (docs, new_continuation)."""
    if continuation:
        iterator = container.query_items_change_feed(continuation=continuation)
    elif start_time is not None:
        iterator = container.query_items_change_feed(start_time=start_time)
    else:
        # First poll after startup with no captured start_time: read from now.
        iterator = container.query_items_change_feed(start_time="Now")
    docs = list(iterator)
    headers = getattr(container.client_connection, "last_response_headers", {}) or {}
    new_continuation = headers.get("etag", continuation)
    return docs, new_continuation


def _to_event(doc: Any) -> dict[str, Any] | None:
    """Map a changed Cosmos document to a compact SSE event payload.

    The client treats this as a notification and performs an authoritative
    single-document refetch, so only lightweight identity/hint fields are sent.
    """
    if not isinstance(doc, dict):
        return None
    doc_id = doc.get("id")
    if not doc_id:
        return None
    state = doc.get("state") or {}
    props = doc.get("properties") or {}
    flag = props.get("flag") or {}
    return {
        "id": doc_id,
        "dataset": props.get("dataset") or doc.get("dataset"),
        "blob_name": props.get("blob_name"),
        "ocr_completed": bool(state.get("ocr_completed")),
        "processing_completed": bool(state.get("processing_completed")),
        "extraction_backend_used": props.get("extraction_backend_used"),
        "flagged": bool(flag.get("flagged")),
        "ts": time.time(),
    }


# Module-level singleton hub used by the FastAPI app.
hub = EventHub()
