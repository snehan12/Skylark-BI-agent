# Skylark BI Agent

A conversational business-intelligence agent for founders/executives,
answering questions across two live monday.com boards: **Deals** (sales
pipeline) and **Work Orders** (project execution).

**Live app:** <PASTE YOUR STREAMLIT COMMUNITY CLOUD LINK HERE>

## Architecture

```
Streamlit (app.py)
      │  user message
      ▼
SkylarkAgent (agent.py)
      │  tool-use loop against Groq (Llama 3.3 70B)
      ▼
tools.py  ── validates filters, calls ──▶  MondayClient (monday_client.py)
      │                                          │
      ▼                                          ▼
cleaning.py (normalizes raw records)   monday.com GraphQL API (read-only)
```

- **`app.py`** — Streamlit chat UI. Loads config (API keys, board IDs) from
  `.env` locally or `st.secrets` on Streamlit Cloud, and renders the
  conversation.
- **`agent.py`** — Orchestrates the tool-use loop: sends the conversation +
  tool definitions to Groq, executes whatever tool the model calls, feeds the
  result back, and repeats until the model responds in plain text. Includes
  retry handling for Groq/Llama's occasional malformed tool-call generation.
- **`tools.py`** — The functions the model is allowed to call
  (`get_pipeline_summary`, `get_work_order_summary`,
  `cross_reference_deals_and_delivery`, `generate_leadership_brief`).
  Each one fetches live data, validates any filter against known real values
  (sectors/statuses), and returns a compact summary with data-quality
  caveats — never raw board dumps, never a cached snapshot.
- **`cleaning.py`** — Normalizes raw monday.com column values: parses
  inconsistent date formats, strips duplicate-header rows embedded in the
  source data, and maps board columns to consistent field names.
- **`monday_client.py`** — Thin wrapper around monday.com's GraphQL API for
  read-only board access.

## monday.com Setup

1. Create two boards in your monday.com workspace and import the provided
   CSVs into them:
   - **Deals** board ← `Deal Funnel` CSV
   - **Work Orders** board ← `Work Order Tracker` CSV
2. Note each board's ID (visible in the board URL, or via monday.com's API
   explorer).
3. Generate a personal API token: monday.com → Avatar → Admin →
   API → **read-only** scope is sufficient (this agent never writes).

## Environment Variables

Set these locally in a `.env` file, or as secrets in Streamlit Community
Cloud:

```
GROQ_API_KEY=gsk_...
MONDAY_API_TOKEN=...
MONDAY_DEALS_BOARD_ID=...
MONDAY_WORK_ORDERS_BOARD_ID=...
```

## Running Locally

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```


