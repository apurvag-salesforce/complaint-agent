# Complaint Agent: an AI agent with tool calling and human approval

An AI agent that processes customer complaint emails (German) for an online shop.
It extracts structured data, decides what to do using tools, and **asks a human
before any risky action** such as a refund.

*Kurzbeschreibung (DE):* Ein KI-Agent, der Reklamations-E-Mails liest, strukturierte Daten
extrahiert, Bestellungen prüft, Tickets anlegt und Erstattungen nur nach menschlicher
Freigabe auslöst. Alle Aktionen werden protokolliert.

## How it works

```
Email (.txt)
   │
   ▼
1. Extraction ── forced tool call ──► structured JSON (name, order ID, issue, urgency ...)
   │
   ▼
2. Agent loop (Claude + tool calling, max 8 steps)
   ├─ get_order          (read)               runs automatically
   ├─ create_ticket      (write)              runs automatically, audited
   ├─ escalate_to_human  (write)              runs automatically, audited
   └─ issue_refund       (approval_required)  a human must approve first
   │
   ▼
3. German draft reply + audit trail (runtime/audit.jsonl)
```

## Features

- **Tool / function calling** with JSON schemas
- **Structured data extraction** from free-text emails
- **Human-in-the-loop** approval for risky actions (terminal prompt; easy to swap for Slack or email)
- **Permission model**: each tool has a risk level (`read`, `write`, `approval_required`)
- **Safe actions**: refunds are validated (max. order total, no double refunds)
- **Error handling**: tool errors are returned to the model instead of crashing; API errors escalate to a human; max-steps safety net
- **Logging and audit trail** of every tool call (`runtime/agent.log`, `runtime/audit.jsonl`)
- **Tests** for the safety logic that run without an API key

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here

python agent.py data/emails/01_beschaedigt.txt        # asks for approval in the terminal
python agent.py --all --approval deny                 # run all sample emails, deny all refunds
python -m pytest                                      # run the tests (no API key needed)
```

Set `AGENT_MODEL` to use a different Claude model.

## Sample emails

| File | Expected behaviour |
|---|---|
| `01_beschaedigt.txt` | Damaged item within 14 days → asks approval → refund |
| `02_verspaetet.txt` | Late delivery → ticket, no refund |
| `03_unbekannt_rechtlich.txt` | Unknown order + legal threat → escalate to human |
| `04_zu_spaet.txt` | Reported after 14 days → no refund → escalate |

## Limitations and next steps

- Orders, refunds and tickets are **mocked** with local files. Replace the functions in `tools.py` with real REST API calls.
- Next steps: receive emails via a **webhook** (FastAPI), add more channels (WhatsApp), retrieve policy text with **RAG**, and add a small web UI for approvals.

## Example run

Input: `data/emails/01_beschaedigt.txt` (customer reports a broken water tank on a coffee machine, order A-1001).

```
  >> Approval required: issue_refund({"order_id": "A-1001", "amount_eur": 129.9, "reason": "Beschädigter Artikel (gebrochener Wassertank), innerhalb von 14 Tagen nach Lieferung gemeldet."})
  >> Approve? [y/N] y

Agent result:
**Ausgeführte Aktionen:**
- Ich habe die Bestellung A-1001 geprüft. Die Lieferung war vor 3 Tagen, die Meldung liegt also innerhalb der 14-Tage-Frist.
- Ich habe eine Rückerstattung von 129,90 € veranlasst (Referenz R-2A0D74). Der Status ist „refunded“. Ein Ticket war nicht nötig.

**Antwortentwurf an die Kundin:**

Betreff: Ihre Bestellung A-1001 – Rückerstattung

Hallo Frau Weber,

vielen Dank für Ihre Nachricht, und entschuldigen Sie bitte die Unannehmlichkeiten mit Ihrer Kaffeemaschine Barista Pro. Da der Wassertank bei der Lieferung beschädigt war, haben wir Ihnen den Kaufpreis von 129,90 € erstattet. Die Referenz lautet R-2A0D74. Der Betrag wird in Kürze auf Ihrem ursprünglichen Zahlungsweg gutgeschrieben.

Bei Fragen melden Sie sich gerne jederzeit bei uns.

Freundliche Grüße
Ihr Kundenservice
```

The audit trail (`runtime/audit.jsonl`) records every tool call. Excerpt from this run:

```
{"tool": "get_order", "args": {"order_id": "A-1001"}, "risk": "read", "approved": null,
 "result": {"customer": "Anna Weber", "item": "Kaffeemaschine Barista Pro", "total_eur": 129.9, "status": "delivered", "delivered_days_ago": 3}}
{"tool": "issue_refund", "args": {"order_id": "A-1001", "amount_eur": 129.9}, "risk": "approval_required", "approved": true,
 "result": {"refund_id": "R-2A0D74", "status": "refunded", "amount_eur": 129.9}}
```

When the reviewer answers `n` instead (run with `--approval deny`), the refund is blocked (`"approved": false`) and the agent creates a ticket for a human instead. Orders that are unknown or reported after the 14-day window are escalated to a human.

## Example run

Input: `data/emails/01_beschaedigt.txt` (customer reports a broken water tank on a coffee machine, order A-1001).

```
  >> Approval required: issue_refund({"order_id": "A-1001", "amount_eur": 129.9, "reason": "Beschädigter Artikel (gebrochener Wassertank), innerhalb von 14 Tagen nach Lieferung gemeldet."})
  >> Approve? [y/N] y

Agent result:
**Ausgeführte Aktionen:**
- Ich habe die Bestellung A-1001 geprüft. Die Lieferung war vor 3 Tagen, die Meldung liegt also innerhalb der 14-Tage-Frist.
- Ich habe eine Rückerstattung von 129,90 € veranlasst (Referenz R-2A0D74). Der Status ist „refunded“. Ein Ticket war nicht nötig.

**Antwortentwurf an die Kundin:**

Betreff: Ihre Bestellung A-1001 – Rückerstattung

Hallo Frau Weber,

vielen Dank für Ihre Nachricht, und entschuldigen Sie bitte die Unannehmlichkeiten mit Ihrer Kaffeemaschine Barista Pro. Da der Wassertank bei der Lieferung beschädigt war, haben wir Ihnen den Kaufpreis von 129,90 € erstattet. Die Referenz lautet R-2A0D74. Der Betrag wird in Kürze auf Ihrem ursprünglichen Zahlungsweg gutgeschrieben.

Bei Fragen melden Sie sich gerne jederzeit bei uns.

Freundliche Grüße
Ihr Kundenservice
```

The audit trail (`runtime/audit.jsonl`) records every tool call. Excerpt from this run:

```
{"tool": "get_order", "args": {"order_id": "A-1001"}, "risk": "read", "approved": null,
 "result": {"customer": "Anna Weber", "item": "Kaffeemaschine Barista Pro", "total_eur": 129.9, "status": "delivered", "delivered_days_ago": 3}}
{"tool": "issue_refund", "args": {"order_id": "A-1001", "amount_eur": 129.9}, "risk": "approval_required", "approved": true,
 "result": {"refund_id": "R-2A0D74", "status": "refunded", "amount_eur": 129.9}}
```

When the reviewer answers `n` instead (run with `--approval deny`), the refund is blocked (`"approved": false`) and the agent creates a ticket for a human instead. Orders that are unknown or reported after the 14-day window are escalated to a human.