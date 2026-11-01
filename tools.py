"""Tools the agent may call, with a simple permission model and an audit log.

Every tool has a risk level:
  read              -> runs automatically
  write             -> runs automatically, but is always audited
  approval_required -> a human must approve before it runs

The "backend systems" (orders, payments, ticketing) are mocked with local
files so the project runs without external services. In a real setup,
each function would call the company's real REST API instead.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
STATE_DIR = BASE_DIR / "runtime"  # tickets, refunds, audit log (gitignored)

log = logging.getLogger("agent.tools")

RISK = {
    "get_order": "read",
    "create_ticket": "write",
    "escalate_to_human": "write",
    "issue_refund": "approval_required",
}

TOOL_SCHEMAS = [
    {
        "name": "get_order",
        "description": "Look up an order by its ID. Always call this before deciding anything.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "e.g. A-1001"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "create_ticket",
        "description": "Create a support ticket for the service team.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": ["damaged_item", "wrong_item", "late_delivery", "other"],
                },
                "summary": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["order_id", "category", "summary", "priority"],
        },
    },
    {
        "name": "issue_refund",
        "description": (
            "Refund an amount to the customer. A human must approve this action "
            "before it is executed. The amount may not exceed the order total."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount_eur": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["order_id", "amount_eur", "reason"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Hand the case to a human employee. Use for unknown orders, legal threats, "
            "or anything outside the policy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["reason", "summary"],
        },
    },
]


# ---------- helpers ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_file(name: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / name


def _append_jsonl(name: str, record: dict) -> None:
    with _state_file(name).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(name: str) -> list[dict]:
    path = _state_file(name)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _load_orders() -> dict:
    return json.loads((DATA_DIR / "orders.json").read_text(encoding="utf-8"))


# ---------- tool implementations ----------

def get_order(order_id: str) -> dict:
    order = _load_orders().get(order_id)
    if order is None:
        return {"error": f"Order {order_id} not found"}
    return {"order_id": order_id, **order}


def create_ticket(order_id: str, category: str, summary: str, priority: str) -> dict:
    ticket_id = "T-" + uuid.uuid4().hex[:6].upper()
    _append_jsonl("tickets.jsonl", {
        "ticket_id": ticket_id, "order_id": order_id, "category": category,
        "summary": summary, "priority": priority, "created_at": _now(),
    })
    return {"ticket_id": ticket_id, "status": "created"}


def escalate_to_human(reason: str, summary: str) -> dict:
    _append_jsonl("escalations.jsonl", {"reason": reason, "summary": summary, "created_at": _now()})
    return {"status": "escalated", "message": "A human employee will take over this case."}


def issue_refund(order_id: str, amount_eur: float, reason: str) -> dict:
    order = _load_orders().get(order_id)
    if order is None:
        return {"error": f"Order {order_id} not found"}
    if amount_eur <= 0 or amount_eur > order["total_eur"]:
        return {"error": f"Invalid amount. Must be between 0 and {order['total_eur']} EUR"}
    if any(r["order_id"] == order_id for r in _read_jsonl("refunds.jsonl")):
        return {"error": f"Order {order_id} was already refunded"}
    refund_id = "R-" + uuid.uuid4().hex[:6].upper()
    _append_jsonl("refunds.jsonl", {
        "refund_id": refund_id, "order_id": order_id, "amount_eur": amount_eur,
        "reason": reason, "created_at": _now(),
    })
    return {"refund_id": refund_id, "status": "refunded", "amount_eur": amount_eur}


TOOLS = {
    "get_order": get_order,
    "create_ticket": create_ticket,
    "escalate_to_human": escalate_to_human,
    "issue_refund": issue_refund,
}


# ---------- human-in-the-loop approval ----------

def cli_approver(tool_name: str, args: dict) -> bool:
    """Ask a human in the terminal. Replace with Slack/email/UI in production."""
    print(f"\n  >> Approval required: {tool_name}({json.dumps(args, ensure_ascii=False)})")
    return input("  >> Approve? [y/N] ").strip().lower() in {"y", "yes", "j", "ja"}


def always_approve(tool_name: str, args: dict) -> bool:
    return True


def always_deny(tool_name: str, args: dict) -> bool:
    return False


# ---------- central execution with permissions, error handling, audit ----------

def execute_tool(name: str, args: dict, approver=cli_approver) -> dict:
    """Run a tool call safely. Never raises: errors are returned to the model."""
    if name not in TOOLS:
        result = {"error": f"Unknown tool: {name}"}
        _audit(name, args, "unknown", None, result)
        return result

    risk = RISK[name]
    approved = None
    if risk == "approval_required":
        approved = bool(approver(name, args))
        if not approved:
            result = {"status": "denied", "message": "A human reviewer rejected this action."}
            _audit(name, args, risk, approved, result)
            return result

    try:
        result = TOOLS[name](**args)
    except TypeError as exc:  # wrong or missing arguments from the model
        result = {"error": f"Invalid arguments: {exc}"}
    except Exception as exc:  # never crash the agent loop
        log.exception("Tool %s failed", name)
        result = {"error": f"Tool failed: {exc}"}

    _audit(name, args, risk, approved, result)
    return result


def _audit(name: str, args: dict, risk: str, approved, result: dict) -> None:
    record = {"ts": _now(), "tool": name, "args": args, "risk": risk,
              "approved": approved, "result": result}
    _append_jsonl("audit.jsonl", record)
    log.info("tool=%s risk=%s approved=%s result=%s", name, risk, approved, result)
