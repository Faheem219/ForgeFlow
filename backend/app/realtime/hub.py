"""In-process realtime pub/sub with per-project monotonic seq + replay ring buffer.

Single-instance dev/demo hub. To scale horizontally, swap the pub/sub for Redis behind this
same interface (noted in the phase plan; not built).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.core.config import get_config
from app.db.models.enums import Stage
from app.realtime.schemas import Event, EventType


class RealtimeHub:
    def __init__(self, ring_size: int | None = None) -> None:
        self._ring_size = ring_size
        self._seq: dict[str, int] = defaultdict(int)
        self._ring: dict[str, deque[Event]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)

    def _window(self) -> int:
        if self._ring_size is not None:
            return self._ring_size
        return int(get_config().get("realtime_ring_size"))

    def next_seq(self, project_id: str) -> int:
        self._seq[project_id] += 1
        return self._seq[project_id]

    async def publish(self, event: Event) -> None:
        ring = self._ring.get(event.project_id)
        if ring is None:
            ring = deque(maxlen=self._window())
            self._ring[event.project_id] = ring
        ring.append(event)
        for queue in list(self._subscribers.get(event.project_id, ())):
            queue.put_nowait(event)

    def replay(self, project_id: str, last_seq: int) -> list[Event]:
        """Return retained events with seq > last_seq (may skip evicted ones — graceful)."""
        ring = self._ring.get(project_id)
        if ring is None:
            return []
        return [event for event in ring if event.seq > last_seq]

    def subscribe(self, project_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[project_id].add(queue)
        return queue

    def unsubscribe(self, project_id: str, queue: asyncio.Queue[Event]) -> None:
        subs = self._subscribers.get(project_id)
        if subs is not None:
            subs.discard(queue)

    @asynccontextmanager
    async def subscription(self, project_id: str) -> AsyncIterator[asyncio.Queue[Event]]:
        queue = self.subscribe(project_id)
        try:
            yield queue
        finally:
            self.unsubscribe(project_id, queue)

    async def emit(
        self,
        project_id: str,
        event_type: EventType | str,
        payload: dict[str, Any] | None = None,
        stage: Stage | None = None,
    ) -> Event:
        event = Event(
            event=str(event_type),
            project_id=project_id,
            stage=stage,
            payload=payload or {},
            seq=self.next_seq(project_id),
        )
        await self.publish(event)
        return event

    def reset(self) -> None:
        self._seq.clear()
        self._ring.clear()
        self._subscribers.clear()


_hub = RealtimeHub()


def get_hub() -> RealtimeHub:
    return _hub


def reset_hub() -> None:
    _hub.reset()


async def emit(
    project_id: str,
    event_type: EventType | str,
    payload: dict[str, Any] | None = None,
    stage: Stage | None = None,
) -> Event:
    """Thin helper used by services to publish an event on the process-wide hub."""
    return await _hub.emit(project_id, event_type, payload, stage)
