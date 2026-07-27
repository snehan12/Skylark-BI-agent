"""
Data cleaning & normalization.

Deliberately NOT delegated to the LLM: date parsing, taxonomy mapping and
missing-value detection are done here in plain code so the agent's numbers
are reproducible and testable, not vibes-based. Every record keeps a
`_quality_flags` list so the agent can be transparent about what it had to
guess or drop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from dateutil import parser as dateparser
from rapidfuzz import fuzz, process

# ---------------------------------------------------------------------------
# Canonical taxonomies. In a real deployment these would come from monday.com
# dropdown/status column definitions themselves; here we define them once so
# messy free-text values ("Energy ", "energy sector", "Power/Energy") map to
# one clean label instead of silently fragmenting into 5 buckets.
# ---------------------------------------------------------------------------

CANONICAL_SECTORS = [
    "Renewables",
    "Mining",
    "Railways",
    "Others",
    "Powerline",
    "Construction",
    "DSP",
    "Tender",
    "Manufacturing",
    "Security and Surveillance",
    "Aviation",
]

CANONICAL_DEAL_STAGES = [
    "A. Lead Generated",
    "B. Sales Qualified Leads",
    "C. Demo Done",
    "D. Feasibility",
    "E. Proposal/Commercials Sent",
    "F. Negotiations",
    "G. Project Won",
    "H. Work Order Received",
    "I. POC",
    "J. Invoice sent",
    "K. Amount Accrued",
    "L. Project Lost",
    "M. Projects On Hold",
    "N. Not relevant at the moment",
    "O. Not Relevant at all",
    "Project Completed",
]

CANONICAL_WORK_ORDER_STATUS = [
    "Completed",
    "Ongoing",
    "Executed until current month",
    "Not Started",
    "Pause / struck",
    "Partial Completed",
    "Details pending from Client",
]

_FUZZY_THRESHOLD = 70  # below this, we don't guess -- we flag "Unmapped" instead


def normalize_category(raw_value: str | None, canonical_list: list[str]) -> tuple[str, bool]:
    """
    Map a messy free-text category to the closest canonical label.
    Returns (label, was_guessed). was_guessed=True means fuzzy-matched below
    100 score -- worth surfacing as a caveat, not hiding.
    """
    if not raw_value or not raw_value.strip():
        return "Unknown", True

    cleaned = raw_value.strip()
    match = process.extractOne(cleaned, canonical_list, scorer=fuzz.WRatio)
    if match is None:
        return "Unknown", True

    label, score, _ = match
    if score < _FUZZY_THRESHOLD:
        return "Unmapped: " + cleaned, True
    return label, score < 100


def normalize_date(raw_value: str | None) -> tuple[str | None, bool]:
    """
    Parse an inconsistent date string into ISO 'YYYY-MM-DD'.
    Returns (iso_date_or_None, is_unparseable).
    """
    if not raw_value or not raw_value.strip():
        return None, True
    try:
        dt = dateparser.parse(raw_value, dayfirst=False, fuzzy=True)
        if dt is None:
            return None, True
        # Sanity bound -- monday.com project dates shouldn't be wildly out of range
        if dt.year < 2015 or dt.year > 2035:
            return None, True
        return dt.strftime("%Y-%m-%d"), False
    except (ValueError, OverflowError):
        return None, True


def normalize_currency(raw_value: str | None) -> tuple[float | None, bool]:
    """Strip currency symbols/commas/text, return a float. Flags unparseable amounts."""
    if not raw_value or not raw_value.strip():
        return None, True
    stripped = re.sub(r"[^\d.\-]", "", raw_value)
    if not stripped or stripped in {"-", "."}:
        return None, True
    try:
        return float(stripped), False
    except ValueError:
        return None, True


def normalize_name(raw_value: str | None) -> str:
    """Normalize free-text names (clients, companies) for matching/grouping."""
    if not raw_value:
        return ""
    return re.sub(r"\s+", " ", raw_value.strip()).lower()


@dataclass
class CleanedRecord:
    id: str
    name: str
    fields: dict = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)


def clean_deal_record(item) -> CleanedRecord:
    """
    Clean a single Deals board item. Column title lookups use `.get(...)`
    with common aliases because CSV/board column naming is not guaranteed
    consistent (e.g. 'Deal Value' vs 'Amount' vs 'Value ($)').
    """
    cols = item.columns
    flags: list[str] = []

    sector_raw = _first(cols, ["Sector", "Industry", "Vertical"])
    sector, sector_guessed = normalize_category(sector_raw, CANONICAL_SECTORS)
    if sector_guessed:
        flags.append(f"sector unclear/guessed from '{sector_raw}'")

    stage_raw = _first(cols, ["Stage", "Deal Stage", "Status"])
    stage, stage_guessed = normalize_category(stage_raw, CANONICAL_DEAL_STAGES)
    if stage_guessed:
        flags.append(f"stage unclear/guessed from '{stage_raw}'")

    value_raw = _first(cols, ["Deal Value", "Value", "Amount", "Value ($)"])
    value, value_missing = normalize_currency(value_raw)
    if value_missing:
        flags.append("deal value missing/unparseable")

    close_date_raw = _first(cols, ["Close Date", "Expected Close", "Closing Date"])
    close_date, date_missing = normalize_date(close_date_raw)
    if date_missing:
        flags.append("close date missing/unparseable")

    client_raw = _first(cols, ["Client", "Client Name", "Account", "Company"])
    if not client_raw or not client_raw.strip():
        flags.append("client name missing")

    return CleanedRecord(
        id=item.id,
        name=item.name,
        fields={
            "client": client_raw or "Unknown",
            "client_key": normalize_name(client_raw),
            "client_code": _first(cols, ["Client Code"]),
            "sector": sector,
            "stage": stage,
            "value": value,
            "close_date": close_date,
        },
        quality_flags=flags,
    )


def clean_work_order_record(item) -> CleanedRecord:
    cols = item.columns
    flags: list[str] = []

    status_raw = _first(cols, ["Execution Status"])
    status, status_guessed = normalize_category(status_raw, CANONICAL_WORK_ORDER_STATUS)
    if status_guessed:
        flags.append(f"status unclear/guessed from '{status_raw}'")

    start_raw = _first(cols, ["Start Date"])
    start_date, start_missing = normalize_date(start_raw)
    if start_missing:
        flags.append("start date missing/unparseable")

    end_raw = _first(cols, ["End Date", "Completion Date", "Delivery Date"])
    end_date, end_missing = normalize_date(end_raw)
    if end_missing:
        flags.append("end date missing/unparseable")

    value_raw = _first(cols, ["Order Value", "Value", "Amount", "Contract Value"])
    value, value_missing = normalize_currency(value_raw)
    if value_missing:
        flags.append("order value missing/unparseable")

    client_raw = _first(cols, ["Client", "Client Name", "Account", "Company"])
    if not client_raw or not client_raw.strip():
        flags.append("client name missing")

    sector_raw = _first(cols, ["Sector", "Industry", "Vertical"])
    sector, sector_guessed = normalize_category(sector_raw, CANONICAL_SECTORS)
    if sector_guessed:
        flags.append(f"sector unclear/guessed from '{sector_raw}'")

    return CleanedRecord(
        id=item.id,
        name=item.name,
        fields={
            "client": client_raw or "Unknown",
            "client_key": normalize_name(client_raw),
            "client_code": _first(cols, ["Customer Name Code"]),
            "sector": sector,
            "status": status,
            "value": value,
            "start_date": start_date,
            "end_date": end_date,
        },
        quality_flags=flags,
    )


def _first(cols: dict, candidate_titles: list[str]) -> str | None:
    """Return the first matching column value from a list of possible header names."""
    for title in candidate_titles:
        if title in cols and cols[title]:
            return cols[title]
    # fallback: case-insensitive partial match against actual column titles
    lowered = {k.lower(): v for k, v in cols.items()}
    for title in candidate_titles:
        for k, v in lowered.items():
            if title.lower() in k and v:
                return v
    return None


def _extract_numeric_id(code: str | None) -> str | None:
    """Pull the trailing digits from a code like 'COMPANY002' or 'WOCOMPANY_002' -> '002'."""
    if not code:
        return None
    match = re.search(r"(\d+)$", code.strip())
    return match.group(1).lstrip("0") or "0" if match else None


def link_deal_to_work_orders(deal: CleanedRecord, work_orders: list[CleanedRecord], threshold: int = 85):
    """
    Match a deal to its work order(s) via exact numeric client-code ID
    (Deals 'Client Code' e.g. COMPANY002 <-> Work Orders 'Customer Name Code'
    e.g. WOCOMPANY_002). Confirmed via data audit: 50/51 WO codes match a
    Deals code this way -- far more reliable than fuzzy name matching, since
    'Deal Name' fields are anonymized codenames reused across unrelated rows.
    """
    deal_id = _extract_numeric_id(deal.fields.get("client_code"))
    if not deal_id:
        return []
    return [
        wo for wo in work_orders
        if _extract_numeric_id(wo.fields.get("client_code")) == deal_id
    ]
