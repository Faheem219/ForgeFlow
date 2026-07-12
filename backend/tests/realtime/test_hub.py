from __future__ import annotations

import asyncio

from app.realtime.hub import RealtimeHub
from app.realtime.schemas import EventType


async def test_events_arrive_in_seq_order() -> None:
    hub = RealtimeHub(ring_size=100)
    queue = hub.subscribe("p1")
    for i in range(5):
        await hub.emit("p1", EventType.progress, {"i": i})

    seqs = [queue.get_nowait().seq for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


async def test_replay_returns_events_after_last_seq() -> None:
    hub = RealtimeHub(ring_size=100)
    for i in range(5):
        await hub.emit("p1", EventType.progress, {"i": i})

    missed = hub.replay("p1", last_seq=2)
    assert [e.seq for e in missed] == [3, 4, 5]


async def test_replay_beyond_window_is_graceful() -> None:
    hub = RealtimeHub(ring_size=3)  # retains only the last 3
    for i in range(6):
        await hub.emit("p1", EventType.progress, {"i": i})

    # Asking from the very start returns only what's retained — no crash, no duplicates.
    missed = hub.replay("p1", last_seq=0)
    assert [e.seq for e in missed] == [4, 5, 6]


async def test_replay_unknown_project_is_empty() -> None:
    hub = RealtimeHub(ring_size=10)
    assert hub.replay("never-seen", last_seq=0) == []


async def test_unsubscribe_stops_delivery() -> None:
    hub = RealtimeHub(ring_size=10)
    queue = hub.subscribe("p1")
    hub.unsubscribe("p1", queue)
    await hub.emit("p1", EventType.progress, {})
    assert queue.empty()


async def test_subscription_context_manager_delivers() -> None:
    hub = RealtimeHub(ring_size=10)
    async with hub.subscription("p1") as queue:
        await hub.emit("p1", EventType.progress, {"x": 1})
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event.payload == {"x": 1}
        assert event.seq == 1


async def test_seq_is_per_project() -> None:
    hub = RealtimeHub(ring_size=10)
    a = await hub.emit("p1", EventType.progress, {})
    b = await hub.emit("p2", EventType.progress, {})
    assert a.seq == 1 and b.seq == 1  # independent counters
