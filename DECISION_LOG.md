# Decision Log

## Key assumptions

- **No shared key between boards.** The Deals and Work Orders boards don't
  share an explicit ID, so cross-board queries (e.g. "did won deals get
  delivered?") link records by fuzzy-matching normalized client names
  (RapidFuzz, threshold 85). This is inherently approximate — documented as a
  caveat in every cross-board tool response rather than presented as fact.
- **Canonical taxonomies were invented, not given.** Sector (Energy, Mining,
  Agriculture, Infrastructure, Telecom, Government, Real Estate, Industrial,
  Other), deal stage, and work-order status lists in `cleaning.py` are my
  best read of the domain, not something monday.com or the CSVs specified
  explicitly. In production these should come from the actual monday.com
  dropdown/status column definitions so they can't drift from what founders
  see in the UI.
- **"Quarter" means calendar quarter (Jan–Mar, Apr–Jun, ...).** Skylark may
  run on a fiscal year that differs; if so, `_current_quarter_bounds()` in
  `tools.py` is the one place to change.
- **Column name matching is alias-based, not position-based.** Since the
  assignment explicitly allows me to structure monday.com columns "as I see
  fit," `_first()` in `cleaning.py` looks for several likely header names per
  field (e.g. "Client" / "Client Name" / "Account" / "Company") so the agent
  survives reasonable renaming without a schema migration.
- **monday.com column `.text` field, not raw `.value`.** I read the
  human-rendered text of each column rather than parsing monday's raw JSON
  column values, trading a little precision (e.g. exact dropdown IDs) for
  much simpler, more robust code given the 6-hour window.

## Trade-offs

| Decision | Chosen | Alternative considered | Why |
|---|---|---|---|
| Integration method | Direct GraphQL API calls | monday.com MCP server | Fewer moving parts to debug in 6 hours; MCP adds a process/transport layer I'd want more time to harden. Would migrate to MCP first if given more time — better tool-discovery story for future non-Claude clients. |
| Data cleaning | Deterministic Python (regex, dateutil, RapidFuzz) | Ask the LLM to clean/interpret raw fields | Reproducibility and testability. An LLM asked to parse "15/03/24" vs "March 15" vs "2024-03-15" repeatedly will be probabilistically fine, but I want the same input to always produce the same output, and I want unit tests I can trust (see `tests/test_cleaning.py`). |
| Hosting | Streamlit Community Cloud | Custom FastAPI + React frontend | Streamlit gives a working chat UI, secrets management, and one-click deploy in the same amount of time a bare backend alone would take, at the cost of UI polish/customizability. |
| Entity linking | Fuzzy name match, threshold 85 | Manual mapping table | No ground-truth join key exists in the sample data; fuzzy matching is transparent (score-based) and the threshold is a single tunable constant, versus a mapping table that would need manual upkeep as new deals/clients appear. |
| Tool granularity | 4 broad tools (pipeline, work orders, cross-reference, leadership brief) | Many narrow tools (get_by_sector, get_by_stage, etc.) | Keeps the tool-use loop shallow (1-2 calls per question) and each tool's output self-contained enough for Claude to reason over directly, rather than forcing multi-step chained calls for common questions. |

## What I'd do differently with more time

1. **Pull taxonomies from monday.com itself** (status/dropdown column
   settings via the API) instead of hardcoding `CANONICAL_*` lists, so the
   agent never drifts from what's actually configured on the boards.
2. **Add a lightweight caching/rate-limit layer** — monday.com's API has
   per-minute complexity limits; a chatty conversation could hit them. I'd
   add short-TTL in-memory caching (30–60s) so a burst of related questions
   doesn't re-fetch the whole board every time, while still keeping data
   "live" for the assignment's no-hardcoding requirement.
3. **Stronger entity resolution** — client-name fuzzy matching is a
   reasonable v1, but a real system would want a canonical client/account
   registry (possibly its own monday.com board) that both Deals and Work
   Orders reference by ID.
4. **Structured export for leadership updates** — right now
   `generate_leadership_brief` returns structured JSON that Claude turns into
   prose in the chat. Given more time I'd add a one-click "export to
   Markdown/Slide/PDF" action rather than only conversational output.
5. **Test coverage for the tools layer** using a mocked `MondayClient`
   (currently only `cleaning.py`'s pure functions are unit tested, since
   testing `tools.py`/`monday_client.py` needs live or mocked API access).
6. **Auth/error UX** — currently a bad token or board ID surfaces as a clear
   error string; with more time I'd add a startup self-check in the
   Streamlit app that pings monday.com and shows a friendly setup checklist
   if anything's misconfigured, rather than failing on the first query.

## How I interpreted "prepare data for leadership updates"

I read this as: **founders shouldn't have to manually reassemble a
pipeline-and-delivery snapshot every week.** The agent exposes a
`generate_leadership_brief` tool that pulls pipeline health, delivery status,
and sold-vs-delivered conversion in one call, and the system prompt instructs
Claude to turn that into clean prose/markdown suitable for pasting into a
slide or doc — including explicit data-quality caveats, since a leadership
update with silently-wrong numbers is worse than no update. I deliberately
did *not* build automatic slide/PDF generation or a scheduled/recurring
report, since the assignment marks this requirement optional and ambiguous
by design — the conversational "ask for it when you need it" version fits the
6-hour scope better than an unrequested automation pipeline, and is
straightforward to extend (see README "Extending" section) if that's the
direction that's actually wanted.
