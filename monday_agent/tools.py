"""
Tool layer: the functions Claude is allowed to call.

Each tool fetches LIVE data from monday.com (via MondayClient), cleans it
(via cleaning.py), and returns a compact JSON-able summary -- never raw
uncleaned board dumps, and never a cached/hardcoded snapshot. The LLM
reasons over the *output* of these tools; it does not see raw monday.com
API responses.
"""

from __future__ import annotations

import os
from datetime import datetime

from . import cleaning
from .monday_client import MondayClient, MondayAPIError

DEALS_BOARD_ID = os.environ.get("MONDAY_DEALS_BOARD_ID", "")
WORK_ORDERS_BOARD_ID = os.environ.get("MONDAY_WORK_ORDERS_BOARD_ID", "")

# ---------------------------------------------------------------------------
# Known valid filter values, taken from the real board data. These exist so
# a bad/guessed filter value (e.g. sector="Energy", status="Overdue") can be
# caught and reported explicitly, instead of silently filtering everything
# out and reporting an empty result as if it were a genuine zero. Update
# these if new sectors/statuses/stages get added on the monday.com side.
# ---------------------------------------------------------------------------
KNOWN_SECTORS = {
    "renewables", "mining", "railways", "powerline", "construction",
    "dsp", "manufacturing", "security and surveillance", "aviation", "others",
}

KNOWN_WORK_ORDER_STATUSES = {
    "completed", "ongoing", "executed until current month", "not started",
    "pause / struck", "partial completed", "details pending from client",
}


def _get_client() -> MondayClient:
    return MondayClient()


def _fetch_and_clean_deals() -> list[cleaning.CleanedRecord]:
    client = _get_client()
    items = client.get_board_items(DEALS_BOARD_ID)
    # Drop rows where a column's value is literally its own header text
    # (embedded duplicate-header rows found in the source data)
    items = [
        i for i in items
        if not any(str(v).strip() == k.strip() for k, v in i.columns.items() if v)
    ]
    return [cleaning.clean_deal_record(i) for i in items]


def _fetch_and_clean_work_orders() -> list[cleaning.CleanedRecord]:
    client = _get_client()
    items = client.get_board_items(WORK_ORDERS_BOARD_ID)
    return [cleaning.clean_work_order_record(i) for i in items]


def _current_quarter_bounds(today: datetime | None = None) -> tuple[str, str, str]:
    today = today or datetime.utcnow()
    q = (today.month - 1) // 3 + 1
    start_month = 3 * (q - 1) + 1
    start = datetime(today.year, start_month, 1)
    end_month = start_month + 2
    if end_month == 12:
        end = datetime(today.year, 12, 31)
    else:
        end = datetime(today.year, end_month + 1, 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), f"Q{q} {today.year}"


def _invalid_sector_error(sector: str) -> dict:
    return {
        "error": (
            f"'{sector}' is not a recognized sector. This filter matched "
            "zero records, which likely means the sector name is wrong "
            "rather than the true count being zero."
        ),
        "valid_sectors": sorted(s.title() for s in KNOWN_SECTORS),
    }


def _invalid_status_error(status: str) -> dict:
    return {
        "error": (
            f"'{status}' is not a recognized work order status. Note: "
            "'overdue' is NOT a status value -- it's computed automatically "
            "from the end date. Call this tool with no status filter and "
            "read the 'overdue_count' / 'overdue_examples' fields instead."
        ),
        "valid_statuses": sorted(s.title() for s in KNOWN_WORK_ORDER_STATUSES),
    }


# ---------------------------------------------------------------------------
# Tools -- these are the functions registered with the Claude tool-use API.
# Keep return payloads compact: totals + a few illustrative rows + caveats,
# not the entire board, so the model doesn't have to eyeball hundreds of rows.
# ---------------------------------------------------------------------------

def tool_get_pipeline_summary(sector: str | None = None, quarter: str | None = None) -> dict:
    """
    Summarize sales pipeline (Deals board): counts and value by stage,
    optionally filtered by sector and/or a specific quarter's close date.
    """
    if sector and sector.lower() not in KNOWN_SECTORS:
        return _invalid_sector_error(sector)

    try:
        deals = _fetch_and_clean_deals()
    except MondayAPIError as e:
        return {"error": str(e)}

    q_start, q_end, q_label = _current_quarter_bounds()
    if quarter:
        q_label = quarter  # note: caller-supplied label is descriptive only here

    filtered = deals
    if sector:
        filtered = [d for d in filtered if d.fields["sector"].lower() == sector.lower()]

    in_quarter = [
        d for d in filtered
        if d.fields.get("close_date") and q_start <= d.fields["close_date"] <= q_end
    ]

    def _agg(records):
        by_stage: dict[str, dict] = {}
        for r in records:
            stage = r.fields["stage"]
            bucket = by_stage.setdefault(stage, {"count": 0, "total_value": 0.0, "unvalued_count": 0})
            bucket["count"] += 1
            if r.fields["value"] is not None:
                bucket["total_value"] += r.fields["value"]
            else:
                bucket["unvalued_count"] += 1
        return by_stage

    missing_close_date = sum(1 for d in filtered if not d.fields.get("close_date"))
    missing_value = sum(1 for d in filtered if d.fields.get("value") is None)

    return {
        "filter": {"sector": sector or "all", "quarter_used": q_label},
        "total_deals_matching_filter": len(filtered),
        "deals_with_close_date_in_quarter": len(in_quarter),
        "by_stage_all_matching_deals": _agg(filtered),
        "by_stage_closing_this_quarter": _agg(in_quarter),
        "data_quality": {
            "deals_missing_close_date": missing_close_date,
            "deals_missing_value": missing_value,
            "note": (
                "Deals missing a close date are excluded from the "
                "'closing this quarter' figures but included in overall totals."
            ),
        },
    }


def tool_get_work_order_summary(sector: str | None = None, status: str | None = None) -> dict:
    """Summarize project execution (Work Orders board): counts/value by status, optional filters."""
    if sector and sector.lower() not in KNOWN_SECTORS:
        return _invalid_sector_error(sector)
    if status and status.lower() not in KNOWN_WORK_ORDER_STATUSES:
        return _invalid_status_error(status)

    try:
        wos = _fetch_and_clean_work_orders()
    except MondayAPIError as e:
        return {"error": str(e)}

    filtered = wos
    if sector:
        filtered = [w for w in filtered if w.fields["sector"].lower() == sector.lower()]
    if status:
        filtered = [w for w in filtered if w.fields["status"].lower() == status.lower()]

    by_status: dict[str, dict] = {}
    for w in filtered:
        s = w.fields["status"]
        bucket = by_status.setdefault(s, {"count": 0, "total_value": 0.0})
        bucket["count"] += 1
        if w.fields["value"] is not None:
            bucket["total_value"] += w.fields["value"]

    overdue = [
        w for w in filtered
        if w.fields["status"] not in ("Completed", "Cancelled")
        and w.fields.get("end_date")
        and w.fields["end_date"] < datetime.utcnow().strftime("%Y-%m-%d")
    ]

    return {
        "filter": {"sector": sector or "all", "status": status or "all"},
        "total_work_orders_matching_filter": len(filtered),
        "by_status": by_status,
        "overdue_count": len(overdue),
        "overdue_examples": [w.name for w in overdue[:5]],
        "data_quality": {
            "missing_value_count": sum(1 for w in filtered if w.fields["value"] is None),
            "missing_end_date_count": sum(1 for w in filtered if not w.fields.get("end_date")),
        },
    }


def tool_cross_reference_deals_and_delivery(sector: str | None = None) -> dict:
    """
    Cross-board view: for won deals, find matching work orders (fuzzy client
    name match) to show conversion from 'sold' to 'delivered'.
    """
    if sector and sector.lower() not in KNOWN_SECTORS:
        return _invalid_sector_error(sector)

    try:
        deals = _fetch_and_clean_deals()
        wos = _fetch_and_clean_work_orders()
    except MondayAPIError as e:
        return {"error": str(e)}

    if sector:
        deals = [d for d in deals if d.fields["sector"].lower() == sector.lower()]

    won_deals = [d for d in deals if d.fields["stage"] == "G. Project Won"]

    matched, unmatched = [], []
    for d in won_deals:
        links = cleaning.link_deal_to_work_orders(d, wos)
        if links:
            matched.append({"deal": d.name, "client": d.fields["client"], "work_orders": [w.name for w in links]})
        else:
            unmatched.append({"deal": d.name, "client": d.fields["client"]})

    return {
        "filter": {"sector": sector or "all"},
        "won_deals_total": len(won_deals),
        "won_deals_with_matched_work_order": len(matched),
        "won_deals_without_matched_work_order": len(unmatched),
        "unmatched_examples": unmatched[:5],
        "note": (
            "Matching is done by exact numeric client-code ID (Deals 'Client "
            "Code' vs Work Orders 'Customer Name Code'). Unmatched deals may "
            "simply not have started execution yet."
        ),
    }


def tool_generate_leadership_brief(period_label: str | None = None) -> dict:
    """
    Compose a structured leadership-update payload: pipeline health,
    delivery status, and flagged data quality issues, ready to paste into a
    weekly/monthly update doc.
    """
    pipeline = tool_get_pipeline_summary()
    delivery = tool_get_work_order_summary()
    cross = tool_cross_reference_deals_and_delivery()

    return {
        "period": period_label or pipeline["filter"]["quarter_used"],
        "pipeline_health": pipeline,
        "delivery_status": delivery,
        "sold_to_delivered_conversion": cross,
        "generated_note": (
            "This is a data pull for a leadership update, not a finished doc. "
            "Ask the agent to turn this into prose/markdown if you want a "
            "ready-to-send summary."
        ),
    }


# Tool schema registered with the Claude API (see agent.py)
TOOL_DEFINITIONS = [
    {
        "name": "get_pipeline_summary",
        "description": (
            "Get sales pipeline data from the Deals board: counts and total value "
            "by deal stage, optionally filtered by sector and/or quarter. Use this "
            "for any question about pipeline, deals, revenue forecast, or sector performance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sector": {
                    "type": "string",
                    "description": (
                        "Optional. Must be one of the real sectors in the data: "
                        "Renewables, Mining, Railways, Powerline, Construction, DSP, "
                        "Manufacturing, Security and Surveillance, Aviation, Others. "
                        "Omit for all sectors. If the user names a sector that isn't in "
                        "this list (e.g. 'Energy'), ask them to clarify rather than guessing."
                    ),
                },
                "quarter": {
                    "type": "string",
                    "description": (
                        "Optional human label for the quarter being asked about, e.g. "
                        "'Q3 2026'. The tool always computes against the current "
                        "calendar quarter internally."
                    ),
                },
            },
        },
    },
    {
        "name": "get_work_order_summary",
        "description": (
            "Get project execution data from the Work Orders board: counts/value "
            "by status, overdue projects, optionally filtered by sector or status. "
            "Use this for operational/delivery questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sector": {
                    "type": "string",
                    "description": (
                        "Optional. Must be one of the real sectors in the data: "
                        "Renewables, Mining, Railways, Powerline, Construction, DSP, "
                        "Manufacturing, Security and Surveillance, Aviation, Others."
                    ),
                },
                "status": {
                    "type": "string",
                    "description": (
                        "Optional. Must be one of the real execution statuses: "
                        "Completed, Ongoing, Executed Until Current Month, Not Started, "
                        "Pause / Struck, Partial Completed, Details Pending From Client. "
                        "IMPORTANT: 'Overdue' is NOT a status -- it is computed "
                        "automatically. To answer an 'overdue' question, call this tool "
                        "with no status filter and read the 'overdue_count' and "
                        "'overdue_examples' fields in the response."
                    ),
                },
            },
        },
    },
    {
        "name": "cross_reference_deals_and_delivery",
        "description": (
            "Cross-reference the Deals board and Work Orders board to see whether "
            "won deals have a matching active/delivered work order. Use this for "
            "'sold vs delivered' or funnel-to-execution questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sector": {
                    "type": "string",
                    "description": (
                        "Optional. Must be one of the real sectors in the data: "
                        "Renewables, Mining, Railways, Powerline, Construction, DSP, "
                        "Manufacturing, Security and Surveillance, Aviation, Others. "
                        "Do not carry a sector filter over from a previous question "
                        "unless the user is still clearly asking about that sector."
                    ),
                }
            },
        },
    },
    {
        "name": "generate_leadership_brief",
        "description": (
            "Generate a structured data pull combining pipeline, delivery and "
            "cross-board conversion, intended as the raw material for a "
            "leadership/founder update. Use when the user asks to 'prepare an "
            "update', 'summarize for leadership', or similar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"period_label": {"type": "string"}},
        },
    },
]

TOOL_DISPATCH = {
    "get_pipeline_summary": tool_get_pipeline_summary,
    "get_work_order_summary": tool_get_work_order_summary,
    "cross_reference_deals_and_delivery": tool_cross_reference_deals_and_delivery,
    "generate_leadership_brief": tool_generate_leadership_brief,
}