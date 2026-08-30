# Founder BI Agent

A conversational AI agent that answers founder-level business intelligence questions by reading live data from monday.com. Powered by Claude's tool-use API.

---

## Architecture

```
Browser (React-free HTML/JS chat UI)
        │
        │ HTTP POST /api/chat
        ▼
FastAPI backend (Python)
  ├── agent.py          ← Claude claude-sonnet-4-5 + tool-use loop
  ├── tools.py          ← 4 tools: get_deals, get_work_orders,
  │                          cross_reference_boards, generate_leadership_update
  ├── normalizer.py     ← Date/text cleaning, DataQualityLog
  └── monday_client.py  ← GraphQL API client (schema-dynamic, paginated)
        │
        │ GraphQL (HTTPS)
        ▼
monday.com API v2
  ├── Work Orders board
  └── Deals board
```

**Key design choices:**
- Claude calls tools dynamically — it decides which tool to use based on the question
- Monday.com schema is discovered at runtime (never hardcoded column IDs)
- Messy data is normalized in `normalizer.py` with a `DataQualityLog` that surfaces issues to the user
- Conversation history is maintained per session (in-memory)

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Your Anthropic API key |
| `MONDAY_API_TOKEN` | ✅ | monday.com personal API token |
| `MONDAY_BOARD_WORK_ORDERS` | Optional | Board ID override (auto-discovered by name otherwise) |
| `MONDAY_BOARD_DEALS` | Optional | Board ID override |
| `CLAUDE_MODEL` | Optional | Claude model to use (default: `claude-sonnet-4-5`) |

---

## Monday.com Board Setup

The agent discovers boards by name. Name your boards exactly (or containing):
- **"Work Orders"** — for project execution data
- **"Deals"** — for sales pipeline data

### Work Orders Board — Expected Columns

| Column Name | monday.com Type | Notes |
|-------------|----------------|-------|
| Name | Item Name (built-in) | Work order title |
| Status | Status | Done / In Progress / Stuck / Not Started / Cancelled |
| Assignee | People | Team member assigned |
| Deal | Text | Name of linked deal/client |
| Sector | Dropdown or Text | Energy, Manufacturing, etc. |
| Start Date | Date | Project start date |
| Due Date | Date | Expected completion |
| Completion Date | Date | Actual completion (leave blank if ongoing) |
| Budget | Numbers | Allocated budget (in ₹ or your currency) |
| Spent | Numbers | Amount actually spent |
| Notes | Long Text | Free-form notes |

> **Flexible naming**: Column names don't need to match exactly. The normalizer maps common variants (e.g. "Deadline" → `due_date`, "Cost" → `spent`). See `normalizer.py` → `WO_FIELD_ALIASES` for all supported variants.

### Deals Board — Expected Columns

| Column Name | monday.com Type | Notes |
|-------------|----------------|-------|
| Name | Item Name (built-in) | Deal/opportunity name |
| Status | Status | Won / Lost / Active / Proposal / Negotiation / Discovery |
| Company | Text | Client company name |
| Sector | Dropdown or Text | Energy, Manufacturing, Technology, etc. |
| Value | Numbers | Deal value |
| Close Date | Date | Expected or actual close date |
| Owner | People | Deal owner / sales rep |
| Probability | Numbers | Win probability (0–100) |
| Notes | Long Text | Free-form notes |

---

## Running Locally

### Prerequisites
- Python 3.11+
- `pip`

### Setup

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd founder-bi-agent

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r backend/requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and fill in your API keys

# 5. Start the server
cd backend
uvicorn main:app --reload --port 8000
```

### Verify Step 1 (Monday.com client)

Before running the full app, verify the monday.com connection:

```bash
cd backend
python monday_client.py
```

Expected output:
```
=== Monday.com Client Test ===
1. Listing all accessible boards...
   Board: 'Work Orders' (id=..., items=...)
   Board: 'Deals' (id=..., items=...)
2. Resolving board IDs...
   ...
✅ All checks passed!
```

### Access the app

Open `http://localhost:8000` in your browser.

---

## Deployment (Render)

### One-time setup

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Settings:
   - **Environment**: Docker
   - **Build Command**: _(leave blank — Docker handles it)_
   - **Start Command**: _(leave blank — Dockerfile CMD handles it)_
5. Add Environment Variables:
   - `ANTHROPIC_API_KEY` = your key
   - `MONDAY_API_TOKEN` = your token
6. Click **Deploy**

Render gives you a public URL like `https://founder-bi-agent.onrender.com`.

> **Note on free tier**: Render free tier spins down after 15 minutes of inactivity. First request after spin-down takes ~30s. Upgrade to the $7/mo "Starter" plan for always-on.

---

## Sample Questions to Try

- "What's our total pipeline value right now?"
- "How's our energy sector pipeline this quarter?"
- "Which deals are most at risk of slipping?"
- "Give me a full leadership update"
- "Which work orders are stuck or overdue?"
- "What's our win rate in manufacturing vs energy?"
- "How many projects are running over budget?"
- "Which won deals have projects that are stuck?"

---

## Project Structure

```
founder-bi-agent/
├── backend/
│   ├── main.py           # FastAPI app + routes
│   ├── agent.py          # Claude agent + tool-use loop
│   ├── tools.py          # Tool schemas + implementations
│   ├── normalizer.py     # Data cleaning + quality tracking
│   ├── monday_client.py  # GraphQL client (standalone testable)
│   └── requirements.txt
├── frontend/
│   ├── index.html        # Chat UI
│   ├── style.css         # Dark theme styles
│   └── app.js            # Frontend logic
├── Dockerfile
├── .env.example
├── .gitignore
├── README.md
└── DECISION_LOG.md
```
