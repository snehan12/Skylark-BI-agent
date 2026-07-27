"""
monday.com API client.

Read-only wrapper around monday.com's GraphQL v2 API. Every call hits
monday.com live -- nothing here is cached to disk or hardcoded. If monday.com
is down or the token is bad, callers get a clear MondayAPIError instead of a
silent empty result, so the agent can tell the user what went wrong instead
of guessing.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import requests

MONDAY_API_URL = "https://api.monday.com/v2"


class MondayAPIError(Exception):
    """Raised on auth failures, rate limits, or malformed GraphQL responses."""


@dataclass
class BoardItem:
    """One row/item from a monday.com board, flattened for easy use."""

    id: str
    name: str
    board_id: str
    board_name: str
    columns: dict = field(default_factory=dict)  # {column_title: display_value}


class MondayClient:
    def __init__(self, api_token: str | None = None, timeout: int = 30):
        self.api_token = api_token or os.environ.get("MONDAY_API_TOKEN")
        if not self.api_token:
            raise MondayAPIError(
                "No monday.com API token found. Set MONDAY_API_TOKEN env var "
                "or pass api_token explicitly."
            )
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": self.api_token,
                "Content-Type": "application/json",
                "API-Version": "2024-10",
            }
        )

    def _run_query(self, query: str, variables: dict | None = None, retries: int = 2) -> dict:
        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = self._session.post(
                    MONDAY_API_URL,
                    json={"query": query, "variables": variables or {}},
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 429:
                # rate limited -- back off and retry
                wait = int(resp.headers.get("Retry-After", 5))
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                raise MondayAPIError(
                    f"monday.com API returned HTTP {resp.status_code}: {resp.text[:500]}"
                )

            data = resp.json()
            if "errors" in data:
                raise MondayAPIError(f"monday.com GraphQL error: {data['errors']}")
            return data["data"]

        raise MondayAPIError(f"monday.com API unreachable after retries: {last_err}")

    def list_boards(self) -> list[dict]:
        """Return all boards visible to this token (id + name)."""
        query = """
        query {
          boards (limit: 50) {
            id
            name
            items_count
          }
        }
        """
        data = self._run_query(query)
        return data["boards"]

    def get_board_items(self, board_id: str, page_limit: int = 100) -> list[BoardItem]:
        """
        Fetch ALL items from a board, following cursor pagination.
        This is the only path data enters the agent through -- always live.
        """
        items: list[BoardItem] = []
        cursor = None
        board_name = None

        while True:
            query = """
            query ($boardId: [ID!], $limit: Int!, $cursor: String) {
              boards (ids: $boardId) {
                name
                items_page (limit: $limit, cursor: $cursor) {
                  cursor
                  items {
                    id
                    name
                    column_values {
                      id
                      text
                      column {
                        title
                      }
                    }
                  }
                }
              }
            }
            """
            variables = {"boardId": [board_id], "limit": page_limit, "cursor": cursor}
            data = self._run_query(query, variables)

            boards = data.get("boards", [])
            if not boards:
                raise MondayAPIError(f"Board id {board_id} not found or not accessible.")

            board = boards[0]
            board_name = board["name"]
            page = board["items_page"]

            for raw_item in page["items"]:
                col_map = {}
                for cv in raw_item["column_values"]:
                    title = cv["column"]["title"] if cv.get("column") else cv["id"]
                    col_map[title] = cv["text"]  # text is monday's human-readable rendering
                items.append(
                    BoardItem(
                        id=raw_item["id"],
                        name=raw_item["name"],
                        board_id=board_id,
                        board_name=board_name,
                        columns=col_map,
                    )
                )

            cursor = page.get("cursor")
            if not cursor:
                break

        return items


def get_client() -> MondayClient:
    """Convenience factory reading MONDAY_API_TOKEN from the environment."""
    return MondayClient()
