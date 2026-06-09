"""
Unit tests for GUIDED/AUTO mode enforcement: the provenance checker
(engine/provenance.py) and the _value_gate that fill/select_option run through.

The whole point of guided mode is that it is enforced in CODE — so the
enforcement itself must be regression-tested offline.
"""

import asyncio
import types

import pytest

from provenance import ProvenanceTracker, find_provenance, settings_values
import agent_chat as ac


# ──────────────────────────────────────────────────────────────────────────────
# find_provenance — pure matching rules
# ──────────────────────────────────────────────────────────────────────────────

def test_empty_value_is_always_known():
    assert find_provenance("", [], []) == "empty"
    assert find_provenance("   ", [], []) == "empty"


def test_known_value_exact_normalized():
    kv = [("input-registry", "Testing Ajay Seeds Nantes")]
    assert find_provenance("testing ajay seeds nantes", [], kv) == "input-registry"
    assert find_provenance("Testing  Ajay Seeds Nantes ", [], kv) == "input-registry"


def test_text_containment_for_long_values():
    texts = [("user-message", "Create a cart for customer Supertech Limited with COD payment")]
    assert find_provenance("Supertech Limited", texts, []) == "user-message"
    assert find_provenance("Acme Corp", texts, []) is None


def test_short_values_need_word_boundary():
    texts = [("user-message", "use quantity 10 for the order")]
    assert find_provenance("10", texts, []) == "user-message"
    # '10' must NOT match inside '100'
    texts2 = [("user-message", "the order total is 100 rupees")]
    assert find_provenance("10", texts2, []) is None


def test_unknown_value_returns_none():
    assert find_provenance("Invented Value", [("user-message", "do the cart flow")], []) is None


def test_tracker_accumulates_sources():
    t = ProvenanceTracker()
    assert t.check("Supertech") is None
    t.add_text("please use Supertech as the customer", "user-message")
    assert t.check("Supertech") == "user-message"
    t.add_value("SKU-99", "page-option")
    assert t.check("sku-99") == "page-option"


def test_settings_values_flattening():
    cfg = {"platforms": {"seller": {"users": [{"email": "a@b.com", "password": "Pw1"}],
                                    "urls": {"dev": "https://x"}},
                         "admin": {"users": [{"email": "c@d.com"}]}}}
    vals = settings_values(cfg)
    assert "a@b.com" in vals and "Pw1" in vals and "c@d.com" in vals


# ──────────────────────────────────────────────────────────────────────────────
# _value_gate — tool-level enforcement
# ──────────────────────────────────────────────────────────────────────────────

def _fake_ctx(mode, tracker=None, queue=None):
    session = types.SimpleNamespace(assumptions=[], by_ref={})
    deps = types.SimpleNamespace(mode=mode, provenance=tracker,
                                 user_input_q=queue, _ask_state={}, session=session)
    return types.SimpleNamespace(deps=deps)


def test_gate_passthrough_when_no_tracker():
    ctx = _fake_ctx("guided", tracker=None)
    out = asyncio.run(ac._value_gate(ctx, "field", "anything", "fill"))
    assert out == "anything"


def test_gate_known_value_passes_in_guided():
    t = ProvenanceTracker()
    t.add_text("fill the customer with Supertech Limited")
    q = asyncio.Queue()                     # must stay untouched
    ctx = _fake_ctx("guided", t, q)
    out = asyncio.run(ac._value_gate(ctx, "customer", "Supertech Limited", "fill"))
    assert out == "Supertech Limited"
    assert q.qsize() == 0
    assert ctx.deps.session.assumptions == []


def test_gate_auto_mode_records_assumption():
    t = ProvenanceTracker()
    ctx = _fake_ctx("auto", t)
    out = asyncio.run(ac._value_gate(ctx, "qty", "12345", "fill"))
    assert out == "12345"
    assert ctx.deps.session.assumptions == [
        {"field": "qty", "value": "12345", "action": "fill"}]
    # same value again -> no duplicate flag (it became 'assumed' provenance)
    asyncio.run(ac._value_gate(ctx, "qty", "12345", "fill"))
    assert len(ctx.deps.session.assumptions) == 1


def test_gate_guided_pauses_and_uses_user_reply():
    t = ProvenanceTracker()
    q = asyncio.Queue()
    q.put_nowait("User Supplied Co.")       # the user's answer, pre-queued
    ctx = _fake_ctx("guided", t, q)
    out = asyncio.run(ac._value_gate(ctx, "customer", "Made Up Ltd", "fill"))
    assert out == "User Supplied Co."
    assert ctx.deps.session.assumptions == []          # not an assumption — user answered
    assert t.check("User Supplied Co.") is not None     # reply becomes provenance


def test_gate_guided_ok_approves_proposed_value():
    t = ProvenanceTracker()
    q = asyncio.Queue()
    q.put_nowait("ok")
    ctx = _fake_ctx("guided", t, q)
    out = asyncio.run(ac._value_gate(ctx, "customer", "Proposed Value", "fill"))
    assert out == "Proposed Value"
    assert t.check("Proposed Value") == "user-approved"


def test_gate_guided_without_input_channel_falls_back_to_auto():
    # run_explore.py mode: no queue -> can't pause; behave like auto (flag it).
    t = ProvenanceTracker()
    ctx = _fake_ctx("guided", t, queue=None)
    out = asyncio.run(ac._value_gate(ctx, "f", "NoChannel Val", "fill"))
    assert out == "NoChannel Val"
    assert len(ctx.deps.session.assumptions) == 1
