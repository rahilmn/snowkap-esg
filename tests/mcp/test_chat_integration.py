"""Phase C — Chat-integration / verbatim-sign-off tests."""
from __future__ import annotations

from engine.mcp import MCPServerHandle, dispatch_tool, load_manifest


def _handle_with_handlers():
    """Build a manifest-bound handle with stub handlers for both kinds of tool."""
    manifest = load_manifest()
    handle = MCPServerHandle(
        manifest,
        handlers={
            "wiki-search":     lambda p: {"hits": ["stub"]},
            "advisor-resolve": lambda p: {"resolved": p["event_id"]},
        },
    )
    return handle


def test_dispatch_readonly_tool_runs_without_signoff():
    handle = _handle_with_handlers()
    res = dispatch_tool(handle, tool="wiki-search", payload={"q": "water"})
    assert res.state == "ok"
    assert res.result == {"hits": ["stub"]}


def test_dispatch_destructive_tool_requires_signoff():
    handle = _handle_with_handlers()
    res = dispatch_tool(
        handle,
        tool="advisor-resolve",
        payload={"event_id": "abc", "resolution": "approve", "rationale": ""},
        user_signoff="please go ahead",  # not the verbatim phrase
    )
    assert res.state == "signoff_required"
    assert res.signoff_phrase is not None
    assert res.result is None


def test_dispatch_destructive_tool_runs_with_verbatim_signoff():
    handle = _handle_with_handlers()
    res = dispatch_tool(
        handle,
        tool="advisor-resolve",
        payload={"event_id": "abc", "resolution": "approve", "rationale": "ok"},
        user_signoff="Confirm and execute",
    )
    assert res.state == "ok"
    assert res.result == {"resolved": "abc"}


def test_dispatch_destructive_with_signoff_in_longer_message():
    handle = _handle_with_handlers()
    res = dispatch_tool(
        handle,
        tool="advisor-resolve",
        payload={"event_id": "abc", "resolution": "approve", "rationale": "ok"},
        user_signoff="Yes please. Confirm and execute. Thanks.",
    )
    assert res.state == "ok"


def test_dispatch_error_state_on_invalid_input():
    handle = _handle_with_handlers()
    res = dispatch_tool(
        handle,
        tool="advisor-resolve",
        payload={"event_id": "x", "resolution": "defer", "rationale": ""},
        user_signoff="Confirm and execute",
    )
    assert res.state == "error"
    assert res.error is not None
    assert res.error["code"] == "input_validation_error"
