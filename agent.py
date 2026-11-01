"""Complaint-handling AI agent.

Pipeline for each incoming customer email:
  1. Extract structured data (forced tool call -> guaranteed JSON schema)
  2. Run an agent loop with tools (look up order, create ticket, refund, escalate)
  3. Risky actions (refunds) require human approval
  4. Everything is logged and audited

Usage:
  export ANTHROPIC_API_KEY=...
  python agent.py data/emails/01_beschaedigt.txt
  python agent.py --all --approval deny
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

import anthropic

import tools
from tools import TOOL_SCHEMAS, always_approve, always_deny, cli_approver, execute_tool

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5-5")
MAX_STEPS = 8

log = logging.getLogger("agent")

SYSTEM_PROMPT = """You are a customer-service agent for an online shop. You process complaint emails.

Policy:
- Damaged or wrong item, reported within 14 days of delivery: refund the item price (never more than the order total).
- Late delivery: create a ticket (category late_delivery). No refund.
- Reported later than 14 days after delivery: do not refund. Escalate to a human.
- Order not found, legal threats, or anything unclear: escalate to a human.

Rules:
- Always look up the order first. Never invent order data.
- Only use the provided tools. Refunds need human approval; if it is denied, say so and create a ticket instead.
- When you are done, write a short, polite draft reply to the customer in German and state which actions you took."""

EXTRACT_TOOL = {
    "name": "record_complaint",
    "description": "Record the structured data of a customer complaint email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_name": {"type": ["string", "null"]},
            "order_id": {"type": ["string", "null"], "description": "Order number like A-1001"},
            "issue_type": {
                "type": "string",
                "enum": ["damaged_item", "wrong_item", "late_delivery", "no_response", "other"],
            },
            "requested_resolution": {
                "type": "string",
                "enum": ["refund", "replacement", "none", "unclear"],
            },
            "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
            "summary": {"type": "string", "description": "One sentence in English"},
        },
        "required": ["customer_name", "order_id", "issue_type", "requested_resolution", "urgency", "summary"],
    },
}


def extract(client: anthropic.Anthropic, email_text: str) -> dict:
    """Step 1: turn a free-text email into validated structured data."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system="Extract structured data from the customer email. Use null for anything not stated. Do not guess.",
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_complaint"},
        messages=[{"role": "user", "content": email_text}],
    )
    for block in response.content:
        if block.type == "tool_use":
            data = block.input
            missing = [k for k in EXTRACT_TOOL["input_schema"]["required"] if k not in data]
            if missing:
                raise ValueError(f"Extraction incomplete, missing: {missing}")
            return data
    raise ValueError("Model returned no structured output")


def run_agent(client: anthropic.Anthropic, email_text: str, extracted: dict, approver) -> str:
    """Step 2: agent loop. The model decides which tools to call."""
    messages = [{
        "role": "user",
        "content": f"Customer email:\n{email_text}\n\nExtracted data:\n{json.dumps(extracted, ensure_ascii=False)}",
    }]

    for step in range(1, MAX_STEPS + 1):
        response = client.messages.create(
            model=MODEL, max_tokens=1024, system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS, messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                log.info("step=%d model called %s(%s)", step, block.name, block.input)
                result = execute_tool(block.name, block.input, approver)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                    "is_error": "error" in result,
                })
        messages.append({"role": "user", "content": tool_results})

    # Safety net: the agent did not finish in time -> hand over to a human.
    log.warning("Max steps reached, escalating")
    execute_tool("escalate_to_human", {
        "reason": "Agent exceeded max steps",
        "summary": extracted.get("summary", "unknown"),
    }, approver)
    return "Agent did not finish in time. The case was escalated to a human."


def process_email(client: anthropic.Anthropic, path: Path, approver) -> None:
    print(f"\n{'=' * 70}\nEMAIL: {path.name}\n{'=' * 70}")
    email_text = path.read_text(encoding="utf-8")
    try:
        extracted = extract(client, email_text)
        print("Extracted:", json.dumps(extracted, ensure_ascii=False, indent=2))
        reply = run_agent(client, email_text, extracted, approver)
        print("\nAgent result:\n" + reply)
    except anthropic.APIError as exc:
        log.error("API error for %s: %s", path.name, exc)
        tools.execute_tool("escalate_to_human", {
            "reason": f"API error: {exc}", "summary": f"Email {path.name} could not be processed"}, approver)
        print("API error. Case escalated to a human.")
    except Exception as exc:
        log.exception("Unexpected error for %s", path.name)
        print(f"Error: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Complaint-handling AI agent")
    parser.add_argument("email", nargs="?", help="path to an email .txt file")
    parser.add_argument("--all", action="store_true", help="process all emails in data/emails")
    parser.add_argument("--approval", choices=["ask", "approve", "deny"], default="ask",
                        help="how to handle approval requests (default: ask in terminal)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Please set ANTHROPIC_API_KEY first.")
        return 1
    if not args.email and not args.all:
        parser.print_help()
        return 1

    tools.STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(tools.STATE_DIR / "agent.log", encoding="utf-8")],
    )

    approver = {"ask": cli_approver, "approve": always_approve, "deny": always_deny}[args.approval]
    client = anthropic.Anthropic()

    files = sorted((tools.DATA_DIR / "emails").glob("*.txt")) if args.all else [Path(args.email)]
    for path in files:
        process_email(client, path, approver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
