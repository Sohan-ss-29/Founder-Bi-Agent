"""
tools.py — Claude tool definitions and implementations

Each tool is defined as:
1. A JSON schema (passed to Claude's API for tool_use)
2. An async implementation function

Tools are intentionally lean — they fetch, normalize, and filter data,
then return structured dicts that Claude reasons over.
"""

import logging
from datetime import date, datetime
from typing import Any, Optional

from monday_client import MondayClient, MondayAPIError, resolve_board_ids
from normalizer import normalize_board_data, DataQualityLog

logger = logging.getLogger(__name__)

# ─── Tool schemas (passed to Claude) ─────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "get_deals",
        "description": (
            "Fetch and analyze deals from the Deals pipeline board on monday.com. "
            "Use this to answer questions about revenue, pipeline value, deal status, "
            "sector performance, win rates, or anything related to sales. "
            "Returns normalized deal records plus data quality notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of statuses to include. "
                        "Valid values: Won, Lost, Active, Proposal, Negotiation, Discovery. "
                        "Leave empty to get all deals."
                    ),
                },
                "sector_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of sectors to filter by (e.g. ['Energy', 'Manufacturing']).",
                },
                "date_from": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Filter deals with close_date >= this date.",
                },
                "date_to": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Filter deals with close_date <= this date.",
                },
                "min_value": {
                    "type": "number",
                    "description": "Minimum deal value (in local currency) to include.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_work_orders",
        "description": (
            "Fetch and analyze work orders from the Work Orders board on monday.com. "
            "Use this to answer questions about project execution, operational status, "
            "delays, completion rates, budget vs. actuals, or team workload."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of statuses to include. "
                        "Valid values: Done, In Progress, Stuck, Not Started, Cancelled."
                    ),
                },
                "sector_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of sectors to filter by.",
                },
                "overdue_only": {
                    "type": "boolean",
                    "description": "If true, return only overdue work orders.",
                },
                "date_from": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Filter by due_date >= this date.",
                },
                "date_to": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Filter by due_date <= this date.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "cross_reference_boards",
        "description": (
            "Fetch data from BOTH boards simultaneously and correlate them. "
            "Use when a question requires comparing or joining deal and project data — "
            "e.g., 'which won deals have projects that are stuck?', "
            "'what's the execution health for our energy pipeline?'. "
            "Returns deals, work orders, and matched pairs where deal names align."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sector_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional sector filter applied to both boards.",
                },
                "deal_status_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional deal status filter.",
                },
                "wo_status_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional work order status filter.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "generate_leadership_update",
        "description": (
            "Generate a structured leadership/board update from live data. "
            "Use when the user asks for a 'leadership update', 'board update', "
            "'weekly summary', 'status report', or similar. "
            "Returns a formatted report with: pipeline snapshot, operational status, "
            "key risks and blockers, and recommended actions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": (
                        "Time period for the update, e.g. 'this quarter', 'this month', "
                        "'Q3 2024'. Defaults to current quarter."
                    ),
                },
            },
            "required": [],
        },
    },
]


# ─── Tool implementations ──────────────────────────────────────────────────────

class ToolExecutor:
    """
    Executes tool calls requested by Claude.
    Manages the monday.com client lifecycle and caches board IDs.
    """

    def __init__(self):
        self._client: Optional[MondayClient] = None
        self._wo_id: Optional[str] = None
        self._deals_id: Optional[str] = None

    async def _get_client(self) -> MondayClient:
        if self._client is None:
            self._client = MondayClient()
        return self._client

    async def _get_board_ids(self) -> tuple[str, str]:
        if self._wo_id and self._deals_id:
            return self._wo_id, self._deals_id
        client = await self._get_client()
        self._wo_id, self._deals_id = await resolve_board_ids(client)
        return self._wo_id, self._deals_id

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _filter_deals(
        self,
        deals: list[dict],
        status_filter: list[str] = None,
        sector_filter: list[str] = None,
        date_from: str = None,
        date_to: str = None,
        min_value: float = None,
    ) -> list[dict]:
        result = deals
        if status_filter:
            sf_lower = [s.lower() for s in status_filter]
            result = [d for d in result if (d.get("status") or "").lower() in sf_lower]
        if sector_filter:
            sf_lower = [s.lower() for s in sector_filter]
            result = [d for d in result if (d.get("sector") or "").lower() in sf_lower]
        if date_from:
            result = [d for d in result if d.get("close_date") and d["close_date"] >= date_from]
        if date_to:
            result = [d for d in result if d.get("close_date") and d["close_date"] <= date_to]
        if min_value is not None:
            result = [d for d in result if d.get("value") is not None and d["value"] >= min_value]
        return result

    def _filter_work_orders(
        self,
        wos: list[dict],
        status_filter: list[str] = None,
        sector_filter: list[str] = None,
        overdue_only: bool = False,
        date_from: str = None,
        date_to: str = None,
    ) -> list[dict]:
        result = wos
        if status_filter:
            sf_lower = [s.lower() for s in status_filter]
            result = [w for w in result if (w.get("status") or "").lower() in sf_lower]
        if sector_filter:
            sf_lower = [s.lower() for s in sector_filter]
            result = [w for w in result if (w.get("sector") or "").lower() in sf_lower]
        if overdue_only:
            result = [w for w in result if w.get("overdue")]
        if date_from:
            result = [w for w in result if w.get("due_date") and w["due_date"] >= date_from]
        if date_to:
            result = [w for w in result if w.get("due_date") and w["due_date"] <= date_to]
        return result

    def _summarize_deals(self, deals: list[dict]) -> dict:
        """Compute aggregate stats for a list of deals."""
        total_value = sum(d["value"] for d in deals if d.get("value") is not None)
        won = [d for d in deals if d.get("status") == "Won"]
        lost = [d for d in deals if d.get("status") == "Lost"]
        active = [d for d in deals if d.get("status") not in ("Won", "Lost")]

        sector_breakdown: dict[str, dict] = {}
        for d in deals:
            s = d.get("sector") or "Unknown"
            if s not in sector_breakdown:
                sector_breakdown[s] = {"count": 0, "value": 0}
            sector_breakdown[s]["count"] += 1
            if d.get("value"):
                sector_breakdown[s]["value"] += d["value"]

        win_rate = (len(won) / (len(won) + len(lost)) * 100) if (won or lost) else None

        return {
            "total_deals": len(deals),
            "total_pipeline_value": total_value,
            "won_count": len(won),
            "won_value": sum(d["value"] for d in won if d.get("value")),
            "lost_count": len(lost),
            "active_count": len(active),
            "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
            "sector_breakdown": sector_breakdown,
            "deals": [
                {k: v for k, v in d.items() if k != "_raw"}
                for d in deals
            ],
        }

    def _summarize_work_orders(self, wos: list[dict]) -> dict:
        """Compute aggregate stats for a list of work orders."""
        total_budget = sum(w["budget"] for w in wos if w.get("budget") is not None)
        total_spent = sum(w["spent"] for w in wos if w.get("spent") is not None)
        overdue = [w for w in wos if w.get("overdue")]
        stuck = [w for w in wos if w.get("status") == "Stuck"]
        done = [w for w in wos if w.get("status") == "Done"]
        in_progress = [w for w in wos if w.get("status") == "In Progress"]

        completion_rate = (len(done) / len(wos) * 100) if wos else 0

        return {
            "total_work_orders": len(wos),
            "done_count": len(done),
            "in_progress_count": len(in_progress),
            "stuck_count": len(stuck),
            "overdue_count": len(overdue),
            "completion_rate_pct": round(completion_rate, 1),
            "total_budget": total_budget,
            "total_spent": total_spent,
            "budget_utilization_pct": (
                round(total_spent / total_budget * 100, 1)
                if total_budget else None
            ),
            "overdue_items": [
                {"name": w["name"], "due_date": w["due_date"], "status": w["status"]}
                for w in overdue
            ],
            "stuck_items": [
                {"name": w["name"], "due_date": w["due_date"], "notes": w.get("notes")}
                for w in stuck
            ],
            "work_orders": [
                {k: v for k, v in w.items() if k != "_raw"}
                for w in wos
            ],
        }

    # ── Tool: get_deals ───────────────────────────────────────────────────────

    async def get_deals(self, **kwargs) -> dict:
        try:
            client = await self._get_client()
            _, deals_id = await self._get_board_ids()

            raw_items = await client.get_board_items(deals_id)
            deals, dq_log = normalize_board_data("deals", raw_items)

            filtered = self._filter_deals(
                deals,
                status_filter=kwargs.get("status_filter"),
                sector_filter=kwargs.get("sector_filter"),
                date_from=kwargs.get("date_from"),
                date_to=kwargs.get("date_to"),
                min_value=kwargs.get("min_value"),
            )

            result = self._summarize_deals(filtered)
            result["data_quality"] = dq_log.to_dict()
            result["data_quality_summary"] = dq_log.summary()
            return result

        except MondayAPIError as e:
            return {"error": str(e), "error_type": "monday_api_error"}
        except Exception as e:
            logger.exception("Unexpected error in get_deals")
            return {"error": f"Unexpected error: {str(e)}", "error_type": "internal_error"}

    # ── Tool: get_work_orders ─────────────────────────────────────────────────

    async def get_work_orders(self, **kwargs) -> dict:
        try:
            client = await self._get_client()
            wo_id, _ = await self._get_board_ids()

            raw_items = await client.get_board_items(wo_id)
            wos, dq_log = normalize_board_data("work_orders", raw_items)

            filtered = self._filter_work_orders(
                wos,
                status_filter=kwargs.get("status_filter"),
                sector_filter=kwargs.get("sector_filter"),
                overdue_only=kwargs.get("overdue_only", False),
                date_from=kwargs.get("date_from"),
                date_to=kwargs.get("date_to"),
            )

            result = self._summarize_work_orders(filtered)
            result["data_quality"] = dq_log.to_dict()
            result["data_quality_summary"] = dq_log.summary()
            return result

        except MondayAPIError as e:
            return {"error": str(e), "error_type": "monday_api_error"}
        except Exception as e:
            logger.exception("Unexpected error in get_work_orders")
            return {"error": f"Unexpected error: {str(e)}", "error_type": "internal_error"}

    # ── Tool: cross_reference_boards ──────────────────────────────────────────

    async def cross_reference_boards(self, **kwargs) -> dict:
        try:
            client = await self._get_client()
            wo_id, deals_id = await self._get_board_ids()

            # Fetch both boards in parallel
            import asyncio
            raw_wo, raw_deals = await asyncio.gather(
                client.get_board_items(wo_id),
                client.get_board_items(deals_id),
            )

            wos, wo_dq = normalize_board_data("work_orders", raw_wo)
            deals, deal_dq = normalize_board_data("deals", raw_deals)

            # Apply filters
            if kwargs.get("sector_filter"):
                wos = self._filter_work_orders(wos, sector_filter=kwargs["sector_filter"])
                deals = self._filter_deals(deals, sector_filter=kwargs["sector_filter"])
            if kwargs.get("deal_status_filter"):
                deals = self._filter_deals(deals, status_filter=kwargs["deal_status_filter"])
            if kwargs.get("wo_status_filter"):
                wos = self._filter_work_orders(wos, status_filter=kwargs["wo_status_filter"])

            # Match work orders to deals by name similarity
            matched_pairs = []
            for deal in deals:
                deal_name_lower = (deal.get("name") or "").lower()
                deal_company_lower = (deal.get("company") or "").lower()
                related_wos = []
                for wo in wos:
                    wo_deal_ref = (wo.get("deal") or "").lower()
                    wo_name_lower = (wo.get("name") or "").lower()
                    # Match if WO references deal name, company, or vice versa
                    if (
                        (wo_deal_ref and (wo_deal_ref in deal_name_lower or deal_name_lower in wo_deal_ref))
                        or (deal_company_lower and deal_company_lower in wo_name_lower)
                        or (deal_name_lower and deal_name_lower in wo_name_lower)
                    ):
                        related_wos.append({k: v for k, v in wo.items() if k != "_raw"})
                if related_wos:
                    matched_pairs.append({
                        "deal": {k: v for k, v in deal.items() if k != "_raw"},
                        "work_orders": related_wos,
                    })

            return {
                "deals_summary": self._summarize_deals(deals),
                "work_orders_summary": self._summarize_work_orders(wos),
                "matched_pairs": matched_pairs,
                "unmatched_deals_count": len(deals) - len(matched_pairs),
                "data_quality": {
                    "deals": deal_dq.to_dict(),
                    "work_orders": wo_dq.to_dict(),
                },
                "data_quality_summary": (
                    f"Deals: {deal_dq.summary()}\n\nWork Orders: {wo_dq.summary()}"
                ),
            }

        except MondayAPIError as e:
            return {"error": str(e), "error_type": "monday_api_error"}
        except Exception as e:
            logger.exception("Unexpected error in cross_reference_boards")
            return {"error": f"Unexpected error: {str(e)}", "error_type": "internal_error"}

    # ── Tool: generate_leadership_update ──────────────────────────────────────

    async def generate_leadership_update(self, **kwargs) -> dict:
        """
        Fetch all data needed for a leadership update and return structured raw data.
        Claude then writes the actual narrative based on this.
        """
        try:
            # Reuse cross_reference to get everything
            all_data = await self.cross_reference_boards()
            if "error" in all_data:
                return all_data

            # Add current date for context
            all_data["report_date"] = date.today().isoformat()
            all_data["period_requested"] = kwargs.get("period", "current quarter")

            # Flag concentration risks in pipeline
            deals = all_data.get("deals_summary", {}).get("deals", [])
            total_val = all_data["deals_summary"].get("total_pipeline_value", 0)
            large_deals = []
            if total_val > 0:
                for d in deals:
                    if d.get("value") and d["value"] / total_val > 0.2:  # >20% of pipeline
                        large_deals.append({
                            "name": d["name"],
                            "value": d["value"],
                            "pct_of_pipeline": round(d["value"] / total_val * 100, 1),
                            "status": d.get("status"),
                        })
            all_data["concentration_risks"] = large_deals

            return all_data

        except Exception as e:
            logger.exception("Error in generate_leadership_update")
            return {"error": str(e), "error_type": "internal_error"}

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def execute(self, tool_name: str, tool_input: dict) -> Any:
        """Dispatch a tool call by name."""
        if tool_name == "get_deals":
            return await self.get_deals(**tool_input)
        elif tool_name == "get_work_orders":
            return await self.get_work_orders(**tool_input)
        elif tool_name == "cross_reference_boards":
            return await self.cross_reference_boards(**tool_input)
        elif tool_name == "generate_leadership_update":
            return await self.generate_leadership_update(**tool_input)
        else:
            return {"error": f"Unknown tool: {tool_name}"}
