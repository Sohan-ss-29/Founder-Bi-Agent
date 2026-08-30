"""
normalizer.py — Data cleaning and normalization layer

Converts raw monday.com items into clean, structured dicts.
Tracks data quality issues in a DataQualityLog so the agent
can surface them to the user ("3 of 42 deals had no close date").
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)


# ─── Data quality tracking ────────────────────────────────────────────────────

@dataclass
class DataQualityIssue:
    board: str          # "work_orders" | "deals"
    item_id: str
    item_name: str
    field: str
    issue: str          # human-readable description
    raw_value: Any = None


@dataclass
class DataQualityLog:
    issues: list[DataQualityIssue] = field(default_factory=list)

    def add(self, board: str, item_id: str, item_name: str,
            field: str, issue: str, raw_value: Any = None):
        self.issues.append(DataQualityIssue(
            board=board, item_id=item_id, item_name=item_name,
            field=field, issue=issue, raw_value=raw_value,
        ))

    def summary(self) -> str:
        if not self.issues:
            return "No data quality issues detected."
        counts: dict[str, int] = {}
        for issue in self.issues:
            key = f"{issue.board}/{issue.field}: {issue.issue}"
            counts[key] = counts.get(key, 0) + 1
        lines = [f"Data quality notes:"]
        for desc, count in counts.items():
            lines.append(f"  • {count} item(s) — {desc}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "total_issues": len(self.issues),
            "issues": [
                {
                    "board": i.board,
                    "item": i.item_name,
                    "field": i.field,
                    "issue": i.issue,
                }
                for i in self.issues
            ],
        }


# ─── Category canonicalization ────────────────────────────────────────────────

# Central mapping from raw variants → canonical name.
# Add more mappings here as new variants are discovered in real data.
SECTOR_CANONICAL: dict[str, str] = {
    # Energy
    "energy": "Energy",
    "energy sector": "Energy",
    "energy - oil & gas": "Energy",
    "oil & gas": "Energy",
    "oil and gas": "Energy",
    "o&g": "Energy",
    # Manufacturing
    "manufacturing": "Manufacturing",
    "mfg": "Manufacturing",
    "mfg.": "Manufacturing",
    "industrial": "Manufacturing",
    # Technology
    "tech": "Technology",
    "technology": "Technology",
    "it": "Technology",
    "information technology": "Technology",
    "software": "Technology",
    # Infrastructure
    "infrastructure": "Infrastructure",
    "infra": "Infrastructure",
    "construction": "Infrastructure",
    # Healthcare
    "healthcare": "Healthcare",
    "health": "Healthcare",
    "pharma": "Healthcare",
    "pharmaceutical": "Healthcare",
    # Finance
    "finance": "Finance",
    "financial services": "Finance",
    "fintech": "Finance",
    "banking": "Finance",
    # Agriculture
    "agriculture": "Agriculture",
    "agri": "Agriculture",
    "agro": "Agriculture",
    "farming": "Agriculture",
}

STATUS_CANONICAL_DEALS: dict[str, str] = {
    "won": "Won",
    "closed won": "Won",
    "closed - won": "Won",
    "lost": "Lost",
    "closed lost": "Lost",
    "closed - lost": "Lost",
    "active": "Active",
    "open": "Active",
    "in progress": "Active",
    "proposal": "Proposal",
    "proposal sent": "Proposal",
    "negotiation": "Negotiation",
    "negotiating": "Negotiation",
    "discovery": "Discovery",
    "qualified": "Discovery",
    "new": "Discovery",
}

STATUS_CANONICAL_WO: dict[str, str] = {
    "done": "Done",
    "complete": "Done",
    "completed": "Done",
    "finished": "Done",
    "in progress": "In Progress",
    "wip": "In Progress",
    "active": "In Progress",
    "started": "In Progress",
    "stuck": "Stuck",
    "blocked": "Stuck",
    "on hold": "Stuck",
    "not started": "Not Started",
    "pending": "Not Started",
    "new": "Not Started",
    "cancelled": "Cancelled",
    "canceled": "Cancelled",
}


def canonicalize_sector(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = raw.strip().lower()
    return SECTOR_CANONICAL.get(key, raw.strip().title())


def canonicalize_deal_status(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = raw.strip().lower()
    return STATUS_CANONICAL_DEALS.get(key, raw.strip().title())


def canonicalize_wo_status(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = raw.strip().lower()
    return STATUS_CANONICAL_WO.get(key, raw.strip().title())


# ─── Date parsing ──────────────────────────────────────────────────────────────

def parse_date(raw: Optional[str]) -> Optional[date]:
    """
    Parse a date string in any reasonable format into a Python date.
    Returns None if parsing fails or input is empty.
    """
    if not raw or not raw.strip():
        return None
    raw = raw.strip()

    # Monday stores dates as JSON: {"date": "2024-01-15"} or just "2024-01-15"
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            raw = obj.get("date", "")
            if not raw:
                return None
        except json.JSONDecodeError:
            pass

    try:
        return dateutil_parser.parse(raw, dayfirst=False).date()
    except Exception:
        try:
            return dateutil_parser.parse(raw, dayfirst=True).date()
        except Exception:
            return None


def format_date(d: Optional[date]) -> Optional[str]:
    """Return ISO date string or None."""
    if d is None:
        return None
    return d.isoformat()


# ─── Numeric parsing ───────────────────────────────────────────────────────────

def parse_number(raw: Optional[str]) -> Optional[float]:
    """
    Parse a number from a string, handling currency symbols, commas, etc.
    Returns None if not parseable.
    """
    if not raw or not raw.strip():
        return None
    # Remove currency symbols, commas, spaces
    cleaned = re.sub(r"[₹$€£,\s]", "", raw.strip())
    # Handle "K" / "M" / "L" / "Cr" suffixes (Indian numbering)
    cleaned_upper = cleaned.upper()
    multiplier = 1.0
    if cleaned_upper.endswith("CR"):
        multiplier = 10_000_000
        cleaned = cleaned[:-2]
    elif cleaned_upper.endswith("L"):
        multiplier = 100_000
        cleaned = cleaned[:-1]
    elif cleaned_upper.endswith("M"):
        multiplier = 1_000_000
        cleaned = cleaned[:-1]
    elif cleaned_upper.endswith("K"):
        multiplier = 1_000
        cleaned = cleaned[:-1]
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


# ─── Column value extraction ───────────────────────────────────────────────────

def extract_column_value(cv: dict) -> Any:
    """
    Extract the most useful value from a monday.com column_value object.
    Prefers the human-readable `text` field; falls back to parsed `value` JSON.
    """
    text = cv.get("text", "").strip() if cv.get("text") else ""
    value_raw = cv.get("value", "")
    col_type = cv.get("type", "") or (cv.get("column") or {}).get("type", "")

    if text:
        return text

    if value_raw and value_raw != "null":
        try:
            parsed = json.loads(value_raw)
            if isinstance(parsed, dict):
                # Date columns: {"date": "2024-01-15", "time": null}
                if "date" in parsed:
                    return parsed["date"]
                # Person columns
                if "personsAndTeams" in parsed:
                    persons = parsed["personsAndTeams"]
                    return ", ".join(p.get("name", "") for p in persons if p.get("name"))
                # Status columns
                if "label" in parsed:
                    return parsed["label"]
                # Number columns
                if "number" in parsed:
                    return str(parsed["number"])
            elif isinstance(parsed, (str, int, float)):
                return str(parsed)
        except (json.JSONDecodeError, TypeError):
            return value_raw

    return None


def item_to_flat_dict(item: dict) -> dict:
    """
    Convert a raw monday.com item to a flat dict keyed by column title (lowercased).
    {
        "_id": "...",
        "_name": "...",
        "_state": "...",
        "_created_at": "...",
        "_updated_at": "...",
        "column title": value,
        ...
    }
    """
    flat = {
        "_id": item.get("id", ""),
        "_name": item.get("name", ""),
        "_state": item.get("state", ""),
        "_created_at": item.get("created_at", ""),
        "_updated_at": item.get("updated_at", ""),
    }
    for cv in item.get("column_values", []):
        col_title = (cv.get("column") or {}).get("title", cv.get("id", ""))
        flat[col_title.lower().strip()] = extract_column_value(cv)
    return flat


# ─── Board-specific normalization ─────────────────────────────────────────────

# Column name aliases: maps common variations → canonical internal key
# These are checked against the lowercased column titles from monday.com
DEAL_FIELD_ALIASES = {
    # canonical_key: [list of monday.com column title variants (lowercased)]
    "name": ["name", "deal name", "opportunity", "title"],
    "status": ["status", "stage", "deal stage", "deal status"],
    "company": ["company", "client", "account", "customer", "organization"],
    "sector": ["sector", "industry", "vertical", "segment"],
    "value": ["value", "deal value", "amount", "revenue", "contract value", "arr", "acv"],
    "close_date": ["close date", "closing date", "expected close", "close by", "target date"],
    "owner": ["owner", "deal owner", "sales rep", "account executive", "ae", "assigned to"],
    "probability": ["probability", "win probability", "chance", "likelihood", "%"],
    "notes": ["notes", "note", "description", "comments"],
}

WO_FIELD_ALIASES = {
    "name": ["name", "work order", "project", "title", "task"],
    "status": ["status", "state", "work order status"],
    "assignee": ["assignee", "assigned to", "owner", "person", "team member"],
    "deal": ["deal", "linked deal", "project deal", "opportunity", "client"],
    "sector": ["sector", "industry", "vertical"],
    "start_date": ["start date", "start", "begin date", "kickoff"],
    "due_date": ["due date", "deadline", "target date", "expected completion", "end date"],
    "completion_date": ["completion date", "actual completion", "finished date", "done date"],
    "budget": ["budget", "allocated budget", "planned cost", "contract value"],
    "spent": ["spent", "actual cost", "cost", "invoiced", "billed"],
    "notes": ["notes", "note", "description", "comments"],
}


def _resolve_field(flat: dict, aliases: list[str]) -> Optional[Any]:
    """Find the first matching alias in a flat dict."""
    for alias in aliases:
        if alias in flat and flat[alias] is not None and flat[alias] != "":
            return flat[alias]
    return None


def normalize_deal(raw_item: dict, dq_log: DataQualityLog) -> dict:
    """
    Normalize a raw monday.com Deals item into a canonical deal dict.
    Logs data quality issues to dq_log.
    """
    flat = item_to_flat_dict(raw_item)
    item_id = flat["_id"]
    item_name = flat["_name"] or f"(item {item_id})"

    def get(field: str) -> Optional[Any]:
        aliases = DEAL_FIELD_ALIASES.get(field, [field])
        return _resolve_field(flat, aliases)

    def log_issue(field: str, issue: str, raw_val: Any = None):
        dq_log.add("deals", item_id, item_name, field, issue, raw_val)

    # --- Name
    name = item_name

    # --- Status
    raw_status = get("status")
    status = canonicalize_deal_status(raw_status)
    if not status:
        log_issue("status", "missing status", raw_status)

    # --- Company
    company = get("company")
    if not company:
        log_issue("company", "missing company name")

    # --- Sector
    raw_sector = get("sector")
    sector = canonicalize_sector(raw_sector)
    if not sector:
        log_issue("sector", "missing sector", raw_sector)

    # --- Value
    raw_value = get("value")
    value = parse_number(str(raw_value)) if raw_value is not None else None
    if value is None:
        log_issue("value", "missing or unparseable deal value", raw_value)

    # --- Close date
    raw_close = get("close_date")
    close_date = parse_date(str(raw_close)) if raw_close else None
    if not close_date:
        log_issue("close_date", "missing or unparseable close date", raw_close)

    # --- Owner
    owner = get("owner")

    # --- Probability
    raw_prob = get("probability")
    probability = parse_number(str(raw_prob)) if raw_prob is not None else None
    if probability is not None and not (0 <= probability <= 100):
        log_issue("probability", f"probability out of range: {probability}", raw_prob)
        probability = max(0, min(100, probability))

    return {
        "id": item_id,
        "name": name,
        "status": status,
        "company": company,
        "sector": sector,
        "value": value,
        "close_date": format_date(close_date),
        "owner": owner,
        "probability": probability,
        "notes": get("notes"),
        "_raw": flat,  # keep raw for debugging
    }


def normalize_work_order(raw_item: dict, dq_log: DataQualityLog) -> dict:
    """
    Normalize a raw monday.com Work Orders item into a canonical dict.
    """
    flat = item_to_flat_dict(raw_item)
    item_id = flat["_id"]
    item_name = flat["_name"] or f"(item {item_id})"

    def get(field: str) -> Optional[Any]:
        aliases = WO_FIELD_ALIASES.get(field, [field])
        return _resolve_field(flat, aliases)

    def log_issue(field: str, issue: str, raw_val: Any = None):
        dq_log.add("work_orders", item_id, item_name, field, issue, raw_val)

    name = item_name

    raw_status = get("status")
    status = canonicalize_wo_status(raw_status)
    if not status:
        log_issue("status", "missing status", raw_status)

    assignee = get("assignee")
    deal = get("deal")

    raw_sector = get("sector")
    sector = canonicalize_sector(raw_sector)

    raw_start = get("start_date")
    start_date = parse_date(str(raw_start)) if raw_start else None
    if not start_date:
        log_issue("start_date", "missing or unparseable start date", raw_start)

    raw_due = get("due_date")
    due_date = parse_date(str(raw_due)) if raw_due else None
    if not due_date:
        log_issue("due_date", "missing due date", raw_due)

    raw_comp = get("completion_date")
    completion_date = parse_date(str(raw_comp)) if raw_comp else None

    raw_budget = get("budget")
    budget = parse_number(str(raw_budget)) if raw_budget is not None else None
    if budget is None:
        log_issue("budget", "missing budget", raw_budget)

    raw_spent = get("spent")
    spent = parse_number(str(raw_spent)) if raw_spent is not None else None

    # Derived: overdue?
    overdue = False
    if due_date and not completion_date and status not in ("Done", "Cancelled"):
        overdue = due_date < date.today()

    return {
        "id": item_id,
        "name": name,
        "status": status,
        "assignee": assignee,
        "deal": deal,
        "sector": sector,
        "start_date": format_date(start_date),
        "due_date": format_date(due_date),
        "completion_date": format_date(completion_date),
        "budget": budget,
        "spent": spent,
        "overdue": overdue,
        "notes": get("notes"),
        "_raw": flat,
    }


def normalize_board_data(
    board_type: str,
    raw_items: list[dict],
) -> tuple[list[dict], DataQualityLog]:
    """
    Normalize all items from a board.

    Args:
        board_type: "deals" or "work_orders"
        raw_items: list of raw monday.com item dicts

    Returns:
        (normalized_items, data_quality_log)
    """
    dq_log = DataQualityLog()
    normalize_fn = normalize_deal if board_type == "deals" else normalize_work_order

    normalized = []
    for item in raw_items:
        try:
            normalized.append(normalize_fn(item, dq_log))
        except Exception as e:
            logger.warning("Failed to normalize item %s: %s", item.get("id"), e)
            dq_log.add(board_type, item.get("id", "?"), item.get("name", "?"),
                       "_item", f"normalization error: {e}")

    logger.info(
        "Normalized %d/%d %s items, %d quality issues",
        len(normalized), len(raw_items), board_type, len(dq_log.issues),
    )
    return normalized, dq_log
