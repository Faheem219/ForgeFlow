"""The learned-quirk memo (phase-57).

The key is **(endpoint, model)**, not endpoint alone. That is a correctness requirement, not a
refinement: `stream_options` was an endpoint-wide schema fact, but `max_tokens` is a *model* fact —
the same base URL answers differently for `gpt-4o` and `gpt-5*`. A coarser key would teach one
model's quirk to another and break a provider that was working.

The other property under test is that a corrupt or stale memo can never break a model call: it
degrades to "learn it again", never to an exception on the hot path.
"""

from __future__ import annotations

from app.agents.provider_compat import (
    Adaptation,
    ParamMemo,
    Repair,
    parse_quirks,
)

ENDPOINT = "https://api.openai.com/v1"
RENAME = Repair(Adaptation.rename, "max_tokens", replacement="max_completion_tokens", reason="why")


def test_a_quirk_learned_for_one_model_does_not_leak_to_another() -> None:
    """The whole reason the key is (endpoint, model): both live at the same base URL."""
    memo = ParamMemo()
    memo.learn(ENDPOINT, "gpt-5.4-mini", RENAME)

    assert memo.get(ENDPOINT, "gpt-5.4-mini") == [RENAME]
    assert memo.get(ENDPOINT, "gpt-4o") == []


def test_the_same_endpoint_and_model_at_a_different_base_url_is_separate() -> None:
    memo = ParamMemo()
    memo.learn(ENDPOINT, "gpt-5.4-mini", RENAME)

    assert memo.get("http://localhost:11434/v1", "gpt-5.4-mini") == []


def test_learning_reports_whether_the_repair_was_new() -> None:
    """A duplicate means the previous attempt did not work — the caller stops instead of looping."""
    memo = ParamMemo()

    assert memo.learn(ENDPOINT, "m", RENAME) is True
    assert memo.learn(ENDPOINT, "m", RENAME) is False
    assert len(memo.get(ENDPOINT, "m")) == 1


def test_a_different_repair_for_the_same_model_accumulates() -> None:
    memo = ParamMemo()
    memo.learn(ENDPOINT, "m", RENAME)
    memo.learn(ENDPOINT, "m", Repair(Adaptation.drop, "temperature"))

    assert {r.signature for r in memo.get(ENDPOINT, "m")} == {
        ("rename", "max_tokens"),
        ("drop", "temperature"),
    }


def test_snapshot_and_load_round_trip() -> None:
    memo = ParamMemo()
    memo.learn(ENDPOINT, "gpt-5.4-mini", RENAME)
    memo.learn(ENDPOINT, "gpt-5.4-mini", Repair(Adaptation.drop, "temperature"))

    restored = ParamMemo()
    restored.load(memo.snapshot())

    assert restored.get(ENDPOINT, "gpt-5.4-mini") == memo.get(ENDPOINT, "gpt-5.4-mini")


def test_get_returns_a_copy_so_a_caller_cannot_corrupt_the_memo() -> None:
    memo = ParamMemo()
    memo.learn(ENDPOINT, "m", RENAME)

    memo.get(ENDPOINT, "m").append(Repair(Adaptation.drop, "nonsense"))

    assert len(memo.get(ENDPOINT, "m")) == 1


# ---------------------------------------------------------------- degrading, never raising


def test_merge_keeps_what_this_process_already_learned() -> None:
    """The transport is rebuilt per turn and re-reads the setting; replacing would discard a repair
    learned seconds ago but not yet persisted, and the call would fail again."""
    memo = ParamMemo()
    memo.learn(ENDPOINT, "m", RENAME)

    memo.merge({f"{ENDPOINT}|m": [Repair(Adaptation.drop, "top_p").to_dict()]})

    assert memo.get(ENDPOINT, "m") == [RENAME]  # the fresher in-memory entry wins


def test_merge_adds_entries_for_models_not_yet_seen() -> None:
    memo = ParamMemo()
    memo.merge({f"{ENDPOINT}|other": [RENAME.to_dict()]})

    assert memo.get(ENDPOINT, "other") == [RENAME]


def test_a_corrupt_persisted_memo_degrades_to_empty() -> None:
    """A hand-edited or truncated setting must never be able to break a model call."""
    for raw in ("", "   ", "not json", "[]", "{", '{"k": "not a list"}', "null"):
        memo = ParamMemo()
        memo.load(parse_quirks(raw))
        assert memo.snapshot() == {}, raw


def test_unrecognised_entries_are_skipped_and_the_rest_survive() -> None:
    memo = ParamMemo()
    memo.load(
        {
            f"{ENDPOINT}|m": [
                {"adaptation": "teleport", "param": "x"},  # not an Adaptation
                "not a dict",
                RENAME.to_dict(),
            ]
        }
    )

    assert memo.get(ENDPOINT, "m") == [RENAME]


def test_parse_quirks_accepts_a_well_formed_map() -> None:
    parsed = parse_quirks('{"e|m": [{"adaptation": "drop", "param": "temperature"}]}')

    memo = ParamMemo()
    memo.load(parsed)
    assert memo.get("e", "m")[0].adaptation is Adaptation.drop
