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
