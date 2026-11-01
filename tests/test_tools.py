"""Tests for the tool layer. They need no API key: the safety logic is plain Python."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import tools  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Every test writes tickets/refunds/audit into its own temp folder."""
    monkeypatch.setattr(tools, "STATE_DIR", tmp_path)


def audit_records():
    path = tools.STATE_DIR / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_get_order_found():
    result = tools.execute_tool("get_order", {"order_id": "A-1001"})
    assert result["customer"] == "Anna Weber"


def test_get_order_not_found_returns_error_not_exception():
    result = tools.execute_tool("get_order", {"order_id": "A-9999"})
    assert "error" in result


def test_refund_needs_approval_and_is_blocked_when_denied():
    args = {"order_id": "A-1001", "amount_eur": 129.90, "reason": "damaged"}
    result = tools.execute_tool("issue_refund", args, approver=tools.always_deny)
    assert result["status"] == "denied"
    assert not (tools.STATE_DIR / "refunds.jsonl").exists()  # nothing was paid out


def test_refund_runs_when_approved():
    args = {"order_id": "A-1001", "amount_eur": 129.90, "reason": "damaged"}
    result = tools.execute_tool("issue_refund", args, approver=tools.always_approve)
    assert result["status"] == "refunded"


def test_refund_cannot_exceed_order_total():
    args = {"order_id": "A-1001", "amount_eur": 500, "reason": "too much"}
    result = tools.execute_tool("issue_refund", args, approver=tools.always_approve)
    assert "error" in result


def test_refund_cannot_happen_twice():
    args = {"order_id": "A-1001", "amount_eur": 50, "reason": "first"}
    tools.execute_tool("issue_refund", args, approver=tools.always_approve)
    result = tools.execute_tool("issue_refund", args, approver=tools.always_approve)
    assert "already refunded" in result["error"]


def test_read_tool_does_not_ask_for_approval():
    def must_not_be_called(*_):
        raise AssertionError("approver should not be called for read tools")

    tools.execute_tool("get_order", {"order_id": "A-1001"}, approver=must_not_be_called)


def test_unknown_tool_and_bad_arguments_do_not_crash():
    assert "error" in tools.execute_tool("delete_database", {})
    assert "error" in tools.execute_tool("get_order", {"wrong_arg": 1})


def test_every_call_is_audited():
    tools.execute_tool("get_order", {"order_id": "A-1001"})
    tools.execute_tool("issue_refund", {"order_id": "A-1001", "amount_eur": 10, "reason": "x"},
                       approver=tools.always_deny)
    records = audit_records()
    assert [r["tool"] for r in records] == ["get_order", "issue_refund"]
    assert records[1]["approved"] is False
