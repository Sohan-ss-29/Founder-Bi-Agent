# Decision Log — Founder BI Agent

---

## 1. Key Assumptions

### Data and Board Structure
- **Currency**: Deal values assumed to be in ₹ (INR) based on brief context. The normalizer strips currency symbols and handles K/L/Cr suffixes (Indian numbering system). If the data is in USD, only the display string in the system prompt needs changing.
- **Fiscal vs. calendar quarter**: The agent is instructed to ask a clarifying question on the first ambiguous time reference (e.g., "this quarter") rather than guess. Calendar quarter is the stated default if not asked.
- **Column names are approximations**: monday.com column names vary by how they were set up. The `normalizer.py` file contains `DEAL_FIELD_ALIASES` and `WO_FIELD_ALIASES` dicts that map many common variants (e.g., "Deadline" → `due_date`, "Contract Value" → `value`). Adding new mappings there requires no other code changes.
- **Board naming**: The client auto-discovers boards by name (substring match). Boards must be named to include "Work Orders" and "Deals" respectively, or explicit IDs must be provided via env vars.
- **Status values**: Status canonicalization maps known variants (e.g., "Closed Won" → "Won", "WIP" → "In Progress"). Unknown statuses pass through as-is rather than crashing.

### Scope
- **Read-only**: The agent never writes to monday.com. This was explicitly specified and simplifies auth, error handling, and safety.
- **In-memory sessions**: Each conversation session is kept in a Python dict keyed by UUID. No database. This is sufficient for a demo/evaluation context; a Redis store would be the next step for multi-replica production.
- **No streaming**: The API returns complete responses, not SSE streams. This adds ~2–5s latency for complex multi-tool queries but is simpler to implement correctly.

---

## 2. Trade-offs

### GraphQL API directly vs. MCP (Model Context Protocol)
**Choice**: Direct GraphQL API via a thin `monday_client.py` module.

**Rationale**: MCP would add a separate server process, additional network hops, and debugging complexity under time pressure. The direct API approach is more transparent — the GraphQL queries are readable, errors are clear, and the client module is independently testable (`python monday_client.py`). The brief explicitly mentioned "more debuggable" as a reason to prefer this approach, which I agree with.

**Trade-off**: A future MCP implementation would be more reusable across different Claude deployments and wouldn't require custom tool definitions. If this becomes a long-lived product, MCP is worth revisiting.

### Claude claude-sonnet-4-5 (not claude-opus-4-5)
**Choice**: `claude-sonnet-4-5` as default; `claude-opus-4-5` configurable via env var.

**Rationale**: Sonnet has excellent tool-use reliability and is significantly faster and cheaper than Opus, which matters for a chat interface where response latency is user-facing. Opus would be worth testing if the agent is giving poor reasoning on complex cross-board questions.

### Normalization layer (centralised) vs. ad-hoc string checks
**Choice**: Central `normalizer.py` with explicit alias dicts and canonical value maps.

**Rationale**: Scattered `if "energy" in x.lower()` checks are unmaintainable. A central `SECTOR_CANONICAL` dict is easy to extend, easy to audit, and makes the normalizer independently testable. The data quality log (`DataQualityLog`) gives Claude precise, structured information about what was missing or cleaned — enabling better user-facing messages ("3 of 42 deals had no close date").

### Frontend: No-build HTML/JS vs. React/Vite
**Choice**: Plain HTML + vanilla JS, no build step.

**Rationale**: The brief says "nothing over-engineered" for the frontend. A no-build approach means: no `npm install`, no Webpack, no separate build step in CI/CD. The Dockerfile just copies files. The UX is clean and functional. React would add value if we needed complex state management (e.g., rich table views, real-time updates), which we don't.

### What was skipped (and why)
- **Streaming responses**: Would improve perceived latency. Skipped to keep the backend simpler — SSE or WebSocket adds non-trivial frontend/backend coordination. Easy to add later.
- **Persistent conversation storage**: In-memory only. A production system would store history in Redis or PostgreSQL so conversations survive restarts. Skipped for time.
- **Authentication**: No user login. Acceptable for an internal tool evaluated by one person. Would need OAuth or at minimum an API key auth layer before exposing publicly.
- **Caching**: Board data is re-fetched on every tool call. For large boards this could add latency. A short TTL cache (30–60s) would help but adds complexity. Skipped for correctness — always-live data is a core requirement.

---

## 3. What I'd Do Differently With More Time

1. **Add caching with TTL**: A 30–60 second cache on board data would dramatically improve response speed for follow-up questions without meaningfully staling the data.

2. **Stream Claude's response**: Use the Anthropic streaming API to show the response as it's generated. The typing indicator is a reasonable placeholder but streaming feels dramatically more responsive.

3. **Richer board matching**: The `cross_reference_boards` matching currently uses substring matching between deal names and WO "deal" references. With more time, I'd implement fuzzy matching (e.g., `rapidfuzz`) to handle typos and abbreviations.

4. **Automated tests**: The normalizer and client are structured to be independently testable, but I didn't write tests under time pressure. The monday client's standalone test script is the closest thing. Would add pytest unit tests for the normalizer (date parsing edge cases, category canonicalization, null handling).

5. **Schema adaptation feedback loop**: If the agent gets multiple "missing field" quality issues for a given column, it could tell the user "I'm not finding a 'close_date' column — your board might have it named differently. Check DEAL_FIELD_ALIASES in normalizer.py."

6. **Multi-turn clarification tracking**: Currently the agent asks clarifying questions inline. A better UX would visually distinguish clarifying questions from answers (e.g., a different bubble color or an "awaiting your answer" state).

---

## 4. Interpretation of "Leadership Updates"

The brief deliberately leaves "leadership update" open-ended, asking me to reason about it rather than treat it as a solved problem. My interpretation:

**What a founder/leadership team actually needs in a weekly/board update:**

A leadership update is not a data dump — it's a **curated signal**: what matters, what's changed, and what requires a decision. Based on this, I structured the `generate_leadership_update` tool to return:

1. **Pipeline Snapshot**: Total pipeline value, by-stage breakdown, sector mix, win rate (trailing). This answers "how is the business growing?" without overwhelming with every deal.

2. **Operational Status**: Active project count, completion rate, overdue/stuck items. This answers "are we delivering on what we've sold?" — the execution side that pipeline data alone misses.

3. **Key Risks & Blockers**: Concentration risks (>20% of pipeline in one deal), stuck projects, and data quality gaps. Risks are more decision-relevant than averages.

4. **Recommended Actions**: 1–3 specific, actionable items derived from the data, not generic advice. Claude generates these based on what the data actually shows.

**What I explicitly excluded:**
- Raw item lists (too granular for leadership)
- Every data quality issue (only surface ones affecting key metrics)
- Historical trend data (would require time-series storage we don't have)

**Alternative interpretations I considered:**
- *Just a pipeline summary*: Too narrow — misses operational/delivery risk
- *A full board deck*: Too broad — out of scope for a chat interface
- *User-configurable sections*: Good idea, but adds complexity; the current four sections are the common denominator for most founder contexts

The "recommended actions" section is the most opinionated part of this interpretation. A more conservative approach would omit it and let the founder draw their own conclusions. I kept it because the brief emphasizes "real business insight, not just numbers" — and a good BI assistant should be willing to make a recommendation, not just present data.
