"""
agent.py — Claude-powered conversational BI agent

Manages conversation history, tool use loop, and response generation.
"""

import json
import logging
import os
from typing import AsyncIterator

import anthropic

from tools import ToolExecutor, TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a founder-facing business intelligence assistant for a company that manages deals (sales pipeline) and work orders (project execution) tracked in monday.com.

Your role is to help the founder and leadership team answer business questions quickly, with real insight — not just numbers.

## Your behavior

**Fetch data dynamically**: Always use your tools to get live data from monday.com. Never make up data.

**Give business insight, not just numbers**: Don't just say "₹50L in pipeline". Say "₹50L in pipeline, but 60% of that is in 2 deals — concentration risk if either slips."

**Handle messy data gracefully**: The data may have missing fields, inconsistent formats, or duplicate entries. When data is incomplete, still give the best answer you can, and explicitly tell the user what was missing or excluded (e.g., "3 of 12 work orders had no due date and were excluded from the delay analysis").

**Ask clarifying questions when genuinely ambiguous**: If a question is unclear (e.g., "this quarter" — calendar or fiscal? "pipeline" — all deals or active only?), ask ONE specific clarifying question. Don't ask multiple questions at once. Don't guess silently on ambiguous inputs.

**Maintain conversation context**: Remember what was discussed earlier in this conversation. Follow-up questions like "now break that down by sector" should work naturally.

**Leadership updates**: When asked for a "leadership update" or "board update", generate a structured report with:
- Pipeline Snapshot (total value, stage breakdown, win rate, sector mix)
- Operational Status (active projects, completion rate, overdue/stuck items)
- Key Risks & Blockers (concentration risks, delayed projects, data gaps)
- Recommended Actions (1-3 specific, actionable items)

## Tone and format
- Concise but complete. Founders are busy — lead with the key insight.
- Use bullet points and numbers for clarity.
- Use ₹ for currency unless you see evidence of another currency in the data.
- Flag data quality issues briefly, inline (not as a separate section), unless there are many.
- For leadership updates, use clear section headers (## Pipeline Snapshot etc.)

## What you DON'T do
- Never write to monday.com — read-only only.
- Never hallucinate data — if a tool returns no results, say so.
- Never ignore a tool error — surface it clearly and suggest what the user should check.
- Never give a raw data dump without interpretation.

Today's date: {today}
"""


class ConversationAgent:
    """
    Manages a single conversation session with the Claude agent.
    Each chat session creates one ConversationAgent instance.
    """

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")

        self.client = anthropic.Anthropic(api_key=api_key)
        self.tool_executor = ToolExecutor()
        self.history: list[dict] = []
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")

    def _system_prompt(self) -> str:
        from datetime import date
        return SYSTEM_PROMPT.format(today=date.today().isoformat())

    async def chat(self, user_message: str) -> str:
        """
        Process a user message and return the agent's response.
        Handles the full tool-use loop internally.
        """
        # Add user message to history
        self.history.append({
            "role": "user",
            "content": user_message,
        })

        max_iterations = 6  # prevent infinite tool loops
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            # Call Claude
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=self._system_prompt(),
                    messages=self.history,
                    tools=TOOL_DEFINITIONS,
                )
            except anthropic.AuthenticationError:
                raise ValueError(
                    "Anthropic API authentication failed. Check your ANTHROPIC_API_KEY."
                )
            except anthropic.BadRequestError as e:
                err_str = str(e)
                if "credit balance" in err_str.lower():
                    raise ValueError(
                        "Your Anthropic API account credit balance is too low ($0). "
                        "Please purchase credits at https://console.anthropic.com/settings/billing or provide a new ANTHROPIC_API_KEY in .env."
                    )
                raise ValueError(f"Anthropic API Bad Request: {err_str}")
            except anthropic.RateLimitError:
                raise ValueError(
                    "Anthropic API rate limit reached. Please try again in a moment."
                )
            except anthropic.APIError as e:
                raise ValueError(f"Anthropic API error: {str(e)}")

            # Add assistant response to history
            self.history.append({
                "role": "assistant",
                "content": response.content,
            })

            # Check stop reason
            if response.stop_reason == "end_turn":
                # Extract text response
                text_parts = [
                    block.text
                    for block in response.content
                    if hasattr(block, "text")
                ]
                return "\n".join(text_parts)

            if response.stop_reason != "tool_use":
                # Unexpected stop reason
                text_parts = [
                    block.text
                    for block in response.content
                    if hasattr(block, "text")
                ]
                return "\n".join(text_parts) if text_parts else "(no response)"

            # Handle tool calls
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                tool_use_id = block.id

                logger.info("Tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])

                try:
                    result = await self.tool_executor.execute(tool_name, tool_input)
                except Exception as e:
                    logger.exception("Tool execution failed: %s", tool_name)
                    result = {"error": f"Tool execution failed: {str(e)}"}

                logger.info("Tool result for %s: %d chars", tool_name, len(json.dumps(result)))

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result),
                })

            # Add tool results to history
            self.history.append({
                "role": "user",
                "content": tool_results,
            })

        # Fallback if max iterations hit
        return (
            "I hit the maximum number of tool calls while processing your request. "
            "Please try rephrasing your question."
        )

    def reset(self):
        """Clear conversation history (start fresh)."""
        self.history = []
        logger.info("Conversation history cleared")
