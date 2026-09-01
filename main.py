"""
Hi, Amy! — Course Assistant Backend
FastAPI app serving the chat UI and AI responses.
"""

import os
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from database import init_db, search_chunks

load_dotenv()

# ── Init ──────────────────────────────────────────────────────────────────────
gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def _pick_model():
    for m in ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]:
        try:
            gemini.models.generate_content(model=m, contents="hi")
            return m
        except Exception:
            continue
    return "gemini-3.6-flash"

ACTIVE_MODEL = _pick_model()

def generate(contents):
    return gemini.models.generate_content(model=ACTIVE_MODEL, contents=contents).text

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

class ContentWeekRequest(BaseModel):
    system_prompt: str
    user_prompt: str
    max_tokens: int = 3000

class WatermarkRewriteRequest(BaseModel):
    text: str

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        context = search_chunks(req.message)
        prompt  = AMY_SYSTEM_PROMPT.format(context=context)
        full    = f"{prompt}\n\nUser: {req.message}\n\nAmy:"
        return ChatResponse(response=generate(full))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok", "app": "Hi, Amy!"}

@app.post("/content-week/generate")
async def content_week_generate(req: ContentWeekRequest):
    """Proxy Gemini calls for the Content Week tool — key stays server-side."""
    from google.genai import types
    config = types.GenerateContentConfig(
        system_instruction=req.system_prompt,
        max_output_tokens=req.max_tokens,
        temperature=0.9,
    )
    for attempt in range(5):
        try:
            resp = gemini.models.generate_content(
                model=ACTIVE_MODEL,
                contents=req.user_prompt,
                config=config,
            )
            return {"text": resp.text}
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait = 30 * (attempt + 1)
                await asyncio.sleep(wait)
            else:
                raise HTTPException(status_code=500, detail=err)
    raise HTTPException(status_code=429, detail="Rate limit exceeded after retries")

@app.post("/watermark/rewrite")
async def watermark_rewrite(req: WatermarkRewriteRequest):
    """Rewrite text via Gemini to defeat word-choice AI watermarks (e.g. SynthID)."""
    from google.genai import types
    system = (
        "You are a neutral rewriting assistant. Your only job is to rewrite the provided text "
        "in natural, human-sounding language. Preserve the full meaning and all facts exactly. "
        "Do NOT add commentary, headers, or explanations — output only the rewritten text. "
        "Vary sentence structure, word choices, and phrasing so the result reads as naturally "
        "human-written. Do not start your reply with phrases like 'Here is' or 'Sure'."
    )
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=4096,
        temperature=0.85,
    )
    for attempt in range(5):
        try:
            resp = gemini.models.generate_content(
                model=ACTIVE_MODEL,
                contents=f"Rewrite this text:\n\n{req.text}",
                config=config,
            )
            return {"text": resp.text}
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                await asyncio.sleep(30 * (attempt + 1))
            else:
                raise HTTPException(status_code=500, detail=err)
    raise HTTPException(status_code=429, detail="Rate limit exceeded after retries")

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
