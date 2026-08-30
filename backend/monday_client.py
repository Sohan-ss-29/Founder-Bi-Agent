"""
monday_client.py — Monday.com GraphQL API client

Schema-tolerant: fetches column definitions dynamically at runtime.
Never hardcodes column IDs or board IDs.
"""

import os
import asyncio
import logging
from typing import Any, Optional
import httpx

logger = logging.getLogger(__name__)

MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_API_VERSION = "2024-01"  # stable API version


class MondayAPIError(Exception):
    """Raised when monday.com API returns an error."""
    def __init__(self, message: str, status_code: int = None, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class MondayClient:
    """
    Thin async client for monday.com GraphQL API v2.

    Design principles:
    - Schema-tolerant: discovers columns dynamically, never hardcodes IDs
    - Paginated: handles boards with >500 items automatically
    - Read-only: no mutations
    """

    def __init__(self):
        self.token = os.getenv("MONDAY_API_TOKEN")
        if not self.token:
            raise ValueError("MONDAY_API_TOKEN environment variable is not set")

        self.client = httpx.AsyncClient(
            base_url=MONDAY_API_URL,
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "API-Version": MONDAY_API_VERSION,
            },
            timeout=30.0,
        )

    async def _execute(self, query: str, variables: dict = None) -> dict:
        """Execute a GraphQL query and return the data payload."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            response = await self.client.post("/", json=payload)
        except httpx.TimeoutException:
            raise MondayAPIError("monday.com API request timed out after 30s")
        except httpx.ConnectError as e:
            raise MondayAPIError(f"Could not connect to monday.com API: {e}")

        if response.status_code == 401:
            raise MondayAPIError(
                "monday.com authentication failed. Check your MONDAY_API_TOKEN.",
                status_code=401,
            )
        if response.status_code == 429:
            raise MondayAPIError(
                "monday.com API rate limit exceeded. Please try again in a moment.",
                status_code=429,
            )
        if response.status_code >= 500:
            raise MondayAPIError(
                f"monday.com API server error (HTTP {response.status_code})",
                status_code=response.status_code,
            )

        try:
            body = response.json()
        except Exception:
            raise MondayAPIError(
                f"monday.com returned non-JSON response (HTTP {response.status_code})"
            )

        if "errors" in body and body["errors"]:
            errors = body["errors"]
            messages = "; ".join(e.get("message", str(e)) for e in errors)
            raise MondayAPIError(f"monday.com GraphQL error: {messages}", details=errors)

        return body.get("data", {})

    async def list_boards(self) -> list[dict]:
        """
        List all accessible boards with their IDs and names.
        Used to discover board IDs by name.
        """
        query = """
        query {
            boards(limit: 50) {
                id
                name
                description
                items_count
            }
        }
        """
        data = await self._execute(query)
        return data.get("boards", [])

    async def get_board_by_name(self, name: str) -> Optional[dict]:
        """Find a board by name (case-insensitive substring match)."""
        boards = await self.list_boards()
        name_lower = name.lower()
        for board in boards:
            if name_lower in board.get("name", "").lower():
                return board
        return None

    async def get_board_columns(self, board_id: str) -> list[dict]:
        """
        Fetch column definitions for a board.
        Returns list of {id, title, type, settings_str}.
        """
        query = """
        query($board_id: ID!) {
            boards(ids: [$board_id]) {
                columns {
                    id
                    title
                    type
                    settings_str
                }
            }
        }
        """
        data = await self._execute(query, {"board_id": board_id})
        boards = data.get("boards", [])
        if not boards:
            raise MondayAPIError(f"Board {board_id} not found or not accessible")
        return boards[0].get("columns", [])

    async def get_board_items(
        self,
        board_id: str,
        limit: int = 500,
    ) -> list[dict]:
        """
        Fetch all items from a board with pagination.

        Returns raw item list: [{id, name, column_values: [{id, text, value}]}]
        Handles boards with more than 500 items automatically.
        """
        all_items = []
        cursor = None

        while True:
            if cursor:
                query = """
                query($board_id: ID!, $limit: Int!, $cursor: String!) {
                    boards(ids: [$board_id]) {
                        items_page(limit: $limit, cursor: $cursor) {
                            cursor
                            items {
                                id
                                name
                                state
                                created_at
                                updated_at
                                column_values {
                                    id
                                    text
                                    value
                                    type
                                    column {
                                        title
                                        type
                                    }
                                }
                            }
                        }
                    }
                }
                """
                variables = {"board_id": board_id, "limit": limit, "cursor": cursor}
            else:
                query = """
                query($board_id: ID!, $limit: Int!) {
                    boards(ids: [$board_id]) {
                        items_page(limit: $limit) {
                            cursor
                            items {
                                id
                                name
                                state
                                created_at
                                updated_at
                                column_values {
                                    id
                                    text
                                    value
                                    type
                                    column {
                                        title
                                        type
                                    }
                                }
                            }
                        }
                    }
                }
                """
                variables = {"board_id": board_id, "limit": limit}

            data = await self._execute(query, variables)
            boards = data.get("boards", [])
            if not boards:
                break

            items_page = boards[0].get("items_page", {})
            items = items_page.get("items", [])
            all_items.extend(items)

            cursor = items_page.get("cursor")
            if not cursor:
                break  # no more pages

            logger.info(
                "Fetched %d items so far from board %s, continuing...",
                len(all_items),
                board_id,
            )

        logger.info("Fetched total %d items from board %s", len(all_items), board_id)
        return all_items

    async def get_board_full(self, board_id: str) -> dict:
        """
        Convenience method: returns both column schema and all items.
        {
            "board_id": str,
            "columns": [...],
            "items": [...],
        }
        """
        columns, items = await asyncio.gather(
            self.get_board_columns(board_id),
            self.get_board_items(board_id),
        )
        return {
            "board_id": board_id,
            "columns": columns,
            "items": items,
        }

    async def close(self):
        await self.client.aclose()


# ─── Board ID resolution helper ──────────────────────────────────────────────

async def resolve_board_ids(client: MondayClient) -> tuple[str, str]:
    """
    Resolve Work Orders and Deals board IDs.

    Priority:
    1. MONDAY_BOARD_WORK_ORDERS / MONDAY_BOARD_DEALS env vars
    2. Auto-discover by board name (substring match)

    Returns (work_orders_id, deals_id)
    """
    wo_id = os.getenv("MONDAY_BOARD_WORK_ORDERS")
    deals_id = os.getenv("MONDAY_BOARD_DEALS")

    if wo_id and deals_id:
        logger.info("Using board IDs from env vars: WO=%s, Deals=%s", wo_id, deals_id)
        return wo_id, deals_id

    logger.info("Board IDs not in env, discovering by name...")
    boards = await client.list_boards()
    board_map = {b["name"].lower(): b["id"] for b in boards}

    if not wo_id:
        for name, bid in board_map.items():
            if "work order" in name or "workorder" in name:
                wo_id = bid
                logger.info("Found Work Orders board: '%s' (id=%s)", name, bid)
                break

    if not deals_id:
        for name, bid in board_map.items():
            if "deal" in name:
                deals_id = bid
                logger.info("Found Deals board: '%s' (id=%s)", name, bid)
                break

    if not wo_id:
        raise MondayAPIError(
            "Could not find a 'Work Orders' board. "
            "Set MONDAY_BOARD_WORK_ORDERS env var or rename your board to contain 'Work Orders'."
        )
    if not deals_id:
        raise MondayAPIError(
            "Could not find a 'Deals' board. "
            "Set MONDAY_BOARD_DEALS env var or rename your board to contain 'Deals'."
        )

    return wo_id, deals_id


# ─── Standalone test (run: python monday_client.py) ─────────────────────────

if __name__ == "__main__":
    import json
    import sys
    from dotenv import load_dotenv

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()

    async def main():
        print("=== Monday.com Client Test ===\n")
        client = MondayClient()

        try:
            print("1. Listing all accessible boards...")
            boards = await client.list_boards()
            for b in boards:
                print(f"   Board: '{b['name']}' (id={b['id']}, items={b.get('items_count', '?')})")

            print("\n2. Resolving Work Orders + Deals board IDs...")
            wo_id, deals_id = await resolve_board_ids(client)
            print(f"   Work Orders ID: {wo_id}")
            print(f"   Deals ID:       {deals_id}")

            print("\n3. Fetching Work Orders columns...")
            wo_cols = await client.get_board_columns(wo_id)
            for c in wo_cols:
                print(f"   [{c['type']:15s}] {c['title']} (id={c['id']})")

            print("\n4. Fetching Deals columns...")
            deal_cols = await client.get_board_columns(deals_id)
            for c in deal_cols:
                print(f"   [{c['type']:15s}] {c['title']} (id={c['id']})")

            print("\n5. Fetching first 5 Work Orders items (raw)...")
            wo_items = await client.get_board_items(wo_id, limit=500)
            for item in wo_items[:5]:
                print(f"\n   Item: {item['name']} (id={item['id']})")
                for cv in item.get("column_values", []):
                    if cv.get("text"):
                        print(f"     {cv['column']['title']}: {cv['text']}")

            print(f"\n   Total Work Orders: {len(wo_items)}")

            print("\n6. Fetching first 5 Deals items (raw)...")
            deal_items = await client.get_board_items(deals_id, limit=500)
            for item in deal_items[:5]:
                print(f"\n   Item: {item['name']} (id={item['id']})")
                for cv in item.get("column_values", []):
                    if cv.get("text"):
                        print(f"     {cv['column']['title']}: {cv['text']}")

            print(f"\n   Total Deals: {len(deal_items)}")
            print("\n✅ All checks passed!")

        except MondayAPIError as e:
            print(f"\n❌ Monday.com API Error: {e}", file=sys.stderr)
            if e.details:
                print(f"   Details: {json.dumps(e.details, indent=2)}", file=sys.stderr)
            sys.exit(1)
        finally:
            await client.close()

    asyncio.run(main())
