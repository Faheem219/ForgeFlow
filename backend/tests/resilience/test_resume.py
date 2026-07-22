"""Resumability (phase-48, §7): reconnect mid-task and lose nothing.

Long tasks emit incremental, seq-numbered events into a per-project replay ring (phase-05). A client
that drops mid-build/mid-repair/mid-deploy reconnects with the last seq it saw and replays exactly
what it missed — no duplicates, no gaps (within the ring window). This is what makes a flaky network
survivable rather than a reason to restart a run.
"""

from __future__ import annotations

import pytest

from app.realtime.hub import RealtimeHub
from app.realtime.schemas import EventType

pytestmark = pytest.mark.usefixtures("mongo_db")

PID = "proj-resume"


async def _emit_build_steps(hub: RealtimeHub, count: int) -> list[int]:
    seqs = []
    for i in range(1, count + 1):
        event = await hub.emit(PID, EventType.progress, {"step": i, "of": count})
        seqs.append(event.seq)
    return seqs


async def test_reconnect_replays_exactly_what_was_missed() -> None:
    hub = RealtimeHub(ring_size=256)
    seqs = await _emit_build_steps(hub, 5)

    # The client saw the first two events, then dropped.
    last_seen = seqs[1]
    missed = hub.replay(PID, last_seen)

    assert [e.payload["step"] for e in missed] == [3, 4, 5]
    assert all(e.seq > last_seen for e in missed)


async def test_a_fresh_subscriber_replays_the_whole_run() -> None:
    hub = RealtimeHub(ring_size=256)
    await _emit_build_steps(hub, 4)

    # last_seq=0 → everything still in the ring.
    replayed = hub.replay(PID, 0)
    assert [e.payload["step"] for e in replayed] == [1, 2, 3, 4]


async def test_replay_after_the_latest_seq_is_empty() -> None:
    hub = RealtimeHub(ring_size=256)
    seqs = await _emit_build_steps(hub, 3)

    assert hub.replay(PID, seqs[-1]) == []


async def test_live_events_after_reconnect_have_strictly_higher_seq() -> None:
    """A reconnecting client dedups replay vs live by seq — the two must not overlap or gap."""
    hub = RealtimeHub(ring_size=256)
    await _emit_build_steps(hub, 3)

    # Reconnect: subscribe live, then replay what came before.
    async with hub.subscription(PID) as queue:
        replayed = hub.replay(PID, 0)
        highest_replayed = replayed[-1].seq

        live = await hub.emit(PID, EventType.progress, {"step": 4, "of": 4})
        received = await queue.get()

    assert received.seq == live.seq
    assert received.seq > highest_replayed  # strictly higher → no dedup ambiguity


async def test_seq_is_monotonic_across_a_whole_run() -> None:
    hub = RealtimeHub(ring_size=256)
    seqs = await _emit_build_steps(hub, 10)
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)  # unique — a resume key must never repeat


async def test_a_slow_reconnect_past_the_ring_window_degrades_gracefully() -> None:
    """If more events elapsed than the ring holds, replay returns what survives — never crashes."""
    hub = RealtimeHub(ring_size=3)  # tiny window
    await _emit_build_steps(hub, 10)

    # The client's last_seq (2) has been evicted; replay gives the surviving tail, not an error.
    surviving = hub.replay(PID, 2)
    assert [e.payload["step"] for e in surviving] == [8, 9, 10]
    assert all(e.seq > 2 for e in surviving)


async def test_two_projects_have_independent_resume_streams() -> None:
    hub = RealtimeHub(ring_size=256)
    await hub.emit("proj-a", EventType.progress, {"step": 1})
    await hub.emit("proj-b", EventType.progress, {"step": 1})
    await hub.emit("proj-a", EventType.progress, {"step": 2})

    a = hub.replay("proj-a", 0)
    b = hub.replay("proj-b", 0)
    assert [e.payload["step"] for e in a] == [1, 2]
    assert [e.payload["step"] for e in b] == [1]
