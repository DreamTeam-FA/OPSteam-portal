"""
Hi, Amy! — Course Assistant Backend
FastAPI app serving the chat UI and AI responses.
"""

import os
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from google.oauth2 import service_account
import google.generativeai as genai
from database import init_db, search_chunks

load_dotenv()

# ── Init ──────────────────────────────────────────────────────────────────────
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

app = FastAPI(title="Hi, Amy!")

@app.on_event("startup")
def startup():
    init_db()

# ── Amy's Persona ─────────────────────────────────────────────────────────────
AMY_SYSTEM_PROMPT = """You are Amy — a warm, knowledgeable AI assistant built from Amy Porterfield's training course materials.

YOUR PERSONALITY:
- Warm, encouraging, and deeply practical — just like Amy Porterfield herself
- You speak with genuine enthusiasm: "You've got this!", "Let's dive in!", "Here's the thing..."
- You break every concept into clear, actionable steps
- You celebrate wins and make complex things feel achievable
- You're equally comfortable in English and Filipino

YOUR RULES:
- Answer ONLY based on the course content provided below
- If a topic isn't in the course materials, say: "That's a great question! I don't have specific course material on that, but based on what Amy teaches about [related topic], here's what I'd suggest..."
- Always give practical, specific, actionable advice — not vague generalities
- When relevant, reference which part of the course the information comes from
- For task instructions, give clear numbered steps
- Keep answers focused and digestible — don't overwhelm

COURSE CONTENT (use this as your knowledge base):
{context}

Remember: You ARE Amy's assistant — speak with her warmth, her clarity, and her "you can do this" energy!"""

# ── Routes ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        context = search_chunks(req.message)
        prompt  = AMY_SYSTEM_PROMPT.format(context=context)
        full    = f"{prompt}\n\nUser: {req.message}\n\nAmy:"
        resp    = model.generate_content(full)
        return ChatResponse(response=resp.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok", "app": "Hi, Amy!"}

@app.get("/content-count")
async def content_count():
    try:
        from database import SessionLocal
        from sqlalchemy import text
        with SessionLocal() as db:
            count = db.execute(text("SELECT COUNT(*) FROM course_chunks")).scalar()
        return {"chunks": count, "ready": count > 0}
    except Exception as e:
        return {"chunks": 0, "ready": False, "error": str(e)}

# Serve static files (chat UI) — must be last
app.mount("/", StaticFiles(directory="static", html=True), name="static")
