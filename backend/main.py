"""
main.py — FastAPI application

Routes:
  POST /api/chat          - Send a message, get agent response
  POST /api/reset         - Clear conversation history for a session
  GET  /api/health        - Health check + connectivity test
  GET  /                  - Serve frontend index.html
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from agent import ConversationAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Session store (in-memory) ────────────────────────────────────────────────
# Each browser tab gets a unique session_id; the server keeps one agent per session.
# Not production-scalable, but correct for this demo deployment.

sessions: dict[str, ConversationAgent] = {}
MAX_SESSIONS = 100  # prevent unbounded memory growth


def get_or_create_session(session_id: str) -> ConversationAgent:
    if session_id not in sessions:
        if len(sessions) >= MAX_SESSIONS:
            # Evict oldest session
            oldest = next(iter(sessions))
            del sessions[oldest]
            logger.info("Evicted old session %s", oldest)
        sessions[session_id] = ConversationAgent()
        logger.info("Created new session %s", session_id)
    return sessions[session_id]


# ─── App setup ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Founder BI Agent starting up")
    # Verify env vars on startup
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY not set — agent will fail on first use")
    if not os.getenv("MONDAY_API_TOKEN"):
        logger.warning("MONDAY_API_TOKEN not set — monday.com calls will fail")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Founder BI Agent",
    description="Conversational AI agent for monday.com business intelligence",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request / Response models ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""  # empty = generate new

class ChatResponse(BaseModel):
    response: str
    session_id: str

class ResetRequest(BaseModel):
    session_id: str

class ResetResponse(BaseModel):
    success: bool
    session_id: str


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """Health check. Returns env var status (not their values)."""
    return {
        "status": "ok",
        "anthropic_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
        "monday_token_set": bool(os.getenv("MONDAY_API_TOKEN")),
        "active_sessions": len(sessions),
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Send a message to the agent and get a response.
    Creates a new session if session_id is empty.
    """
    session_id = req.session_id or str(uuid.uuid4())

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        agent = get_or_create_session(session_id)
        response_text = await agent.chat(req.message.strip())
        return ChatResponse(response=response_text, session_id=session_id)

    except ValueError as e:
        # Configuration / auth errors — tell the user clearly
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unhandled error in /api/chat for session %s", session_id)
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred. Please try again. ({type(e).__name__})",
        )


@app.post("/api/reset", response_model=ResetResponse)
async def reset_session(req: ResetRequest):
    """Clear conversation history for a session."""
    if req.session_id in sessions:
        sessions[req.session_id].reset()
    return ResetResponse(success=True, session_id=req.session_id)


# ─── Serve frontend ───────────────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def serve_index():
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"message": "Frontend not found"}, status_code=404)
else:
    @app.get("/")
    async def root():
        return {"message": "Founder BI Agent API. Frontend not found at expected path."}
