# Skylark Drones — Monday.com BI Agent

A conversational agent that answers founder-level business questions by
querying two monday.com boards (Deals + Work Orders) live, cleaning the
messy underlying data on the fly, and reasoning across both boards.

## Architecture

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────────┐
│  Streamlit Chat  │─────▶│  SkylarkAgent     │─────▶│  Claude API          │
│  (app.py)        │◀─────│  (agent.py)       │◀─────│  (tool-use loop)     │
└─────────────────┘      └──────────────────┘      └─────────────────────┘
                                   │
                                   ▼ calls tools
                          ┌──────────────────┐
                          │  tools.py         │  (get_pipeline_summary,
                          │                   │   get_work_order_summary,
                          │                   │   cross_reference_deals_and_delivery,
                          │                   │   generate_leadership_brief)
                          └──────────────────┘
                                   │
                       ┌───────────┴────────────┐
                       ▼                        ▼
              ┌─────────────────┐      ┌──────────────────┐
              │ monday_client.py │      │  cleaning.py      │
              │ (GraphQL API,    │─────▶│  (dates, sectors,  │
              │  live, paginated)│      │   fuzzy matching,  │
              └─────────────────┘      │   quality flags)   │
                       │                └──────────────────┘
                       ▼
              monday.com (Deals board, Work Orders board)
```

**Key design decision:** the LLM never sees raw board data and never cleans
data itself. `monday_client.py` fetches live rows every call (no caching to
disk, no hardcoded CSVs). `cleaning.py` deterministically normalizes dates,
sectors, currency and names, and attaches `quality_flags` to every record.
`tools.py` aggregates cleaned records into compact summaries. Claude's job is
purely to decide which tool(s) to call and turn the structured summary into a
founder-readable answer — not to parse or guess at messy fields itself.

## Repo layout

```
app.py                        Streamlit entrypoint (the hosted prototype)
monday_agent/
  monday_client.py            GraphQL API wrapper, pagination, retries
  cleaning.py                 Deterministic normalization + fuzzy matching
  tools.py                    Business-logic functions exposed to Claude
  agent.py                    Claude tool-use conversation loop + system prompt
tests/
  test_cleaning.py            Unit tests for the cleaning logic (no network)
requirements.txt
.env.example
```

## Setting up monday.com

1. Create/open a monday.com workspace.
2. Create two boards, e.g. named **Deals** and **Work Orders**.
3. Import the provided CSVs: **Board → ⋮ menu → Import data → From Excel/CSV**.
   Map columns roughly as follows (exact names don't matter — `cleaning.py`
   matches on several common aliases per field):
   - **Deals board:** Client / Client Name, Sector / Industry, Stage / Deal
     Stage, Deal Value / Amount, Close Date / Expected Close.
   - **Work Orders board:** Client / Client Name, Sector / Industry, Status,
     Order Value / Amount, Start Date, End Date / Completion Date.
4. Get your board IDs: open each board, the ID is the number in the URL
   (`https://yourcompany.monday.com/boards/1234567890`).
5. Generate an API token: **Avatar → Admin → API → Generate token** (or
   **Profile → Developers → My Access Tokens** on non-admin accounts). This
   assignment only needs **read** access.

## Configuration

Copy `.env.example` to `.env` for local runs, or set these as **Streamlit
Cloud → Settings → Secrets** (TOML format) when deployed:

```
ANTHROPIC_API_KEY = "sk-ant-..."
MONDAY_API_TOKEN = "eyJhbGci..."
MONDAY_DEALS_BOARD_ID = "1234567890"
MONDAY_WORK_ORDERS_BOARD_ID = "1234567891"
```

## Running locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values
export $(cat .env | xargs)   # or use python-dotenv / your shell's preferred method
streamlit run app.py
```

## Running tests

```bash
pip install pytest
pytest tests/ -v
```

## Deploying (Streamlit Community Cloud)

1. Push this repo to GitHub.
2. On share.streamlit.io, "New app" → point at the repo, entrypoint `app.py`.
3. Add the four secrets above under app Settings → Secrets.
4. Deploy. First load may take ~30s (cold start + dependency install).

## Extending

- To add a new business question type, add a function + tool schema entry in
  `tools.py`, then register it in `TOOL_DISPATCH`. No changes needed to
  `agent.py` or the system prompt beyond what's already generic.
- To change the sector/stage/status taxonomies (e.g. add a sector), edit the
  `CANONICAL_*` lists at the top of `cleaning.py`.
