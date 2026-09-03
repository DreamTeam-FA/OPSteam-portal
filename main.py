# Hi, Amy! - Course Assistant Backend
# FastAPI app serving the chat UI and AI responses.
# AI backend: Groq (qwen/qwen3.8-27b)

import os
import re
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq
from database import init_db, search_chunks

load_dotenv()

# ── Init ──────────────────────────────────────────────────────────────────────
groq_client  = Groq(api_key=os.getenv(“GROQ_API_KEY”))
ACTIVE_MODEL = “qwen/qwen3.8-27b”

def strip_thinking(text: str) -> str:
    “””Remove <think>...</think> blocks that qwen emits before its answer.”””
    return re.sub(r”<think>[\s\S]*?</think>”, “”, text, flags=re.IGNORECASE).strip()

def generate(system_prompt, user_prompt, max_tokens=3000, json_mode=False):
    kwargs = dict(
        model=ACTIVE_MODEL,
        messages=[
            {“role”: “system”, “content”: system_prompt},
            {“role”: “user”,   “content”: user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.4 if json_mode else 0.85,
    )
    if json_mode:
        kwargs[“response_format”] = {“type”: “json_object”}
    resp = groq_client.chat.completions.create(**kwargs)
    content = resp.choices[0].message.content or “”
    return strip_thinking(content)

app = FastAPI(title=”Hi, Amy!”)

@app.on_event(“startup”)
def startup():
    try:
        init_db()
    except Exception as e:
        print(f”[startup] DB init warning: {e} — continuing without DB”)

# â”€â”€ Amy's Persona â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
AMY_SYSTEM_PROMPT = """You are Amy â€” a warm, knowledgeable AI assistant built from Amy Porterfield's training course materials.

YOUR PERSONALITY:
- Warm, encouraging, and deeply practical â€” just like Amy Porterfield herself
- You speak with genuine enthusiasm: "You've got this!", "Let's dive in!", "Here's the thing..."
- You break every concept into clear, actionable steps
- You celebrate wins and make complex things feel achievable
- You're equally comfortable in English and Filipino

YOUR RULES:
- Answer ONLY based on the course content provided below
- If a topic isn't in the course materials, say: "That's a great question! I don't have specific course material on that, but based on what Amy teaches about [related topic], here's what I'd suggest..."
- Always give practical, specific, actionable advice â€” not vague generalities
- When relevant, reference which part of the course the information comes from
- For task instructions, give clear numbered steps
- Keep answers focused and digestible â€” don't overwhelm

COURSE CONTENT (use this as your knowledge base):
{context}

Remember: You ARE Amy's assistant â€” speak with her warmth, her clarity, and her "you can do this" energy!"""

# â”€â”€ Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str

class ContentWeekRequest(BaseModel):
    system_prompt: str
    user_prompt: str
    max_tokens: int = 3000
    json_mode: bool = False

class WatermarkRewriteRequest(BaseModel):
    text: str

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        context = search_chunks(req.message)
        system  = AMY_SYSTEM_PROMPT.format(context=context)
        resp    = generate(system, req.message, max_tokens=2000)
        return ChatResponse(response=resp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok", "app": "Hi, Amy!", "model": ACTIVE_MODEL}

@app.get("/content-week/scrape")
async def content_week_scrape(url: str):
    """Scrape a URL server-side (avoids CORS, more reliable than client-side proxies)."""
    import requests as req_lib
    from bs4 import BeautifulSoup
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        r = req_lib.get(url, headers=headers, timeout=12, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        if len(text) < 80:
            raise HTTPException(status_code=422, detail="Page has too little readable text.")
        return {"text": text[:8000]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read that URL: {e}")

@app.post("/content-week/generate")
async def content_week_generate(req: ContentWeekRequest):
    """Proxy Groq calls for the Content Week tool â€” key stays server-side."""
    for attempt in range(5):
        try:
            text = generate(
                req.system_prompt,
                req.user_prompt,
                max_tokens=req.max_tokens,
                json_mode=req.json_mode,
            )
            if not text:
                raise HTTPException(status_code=500, detail="Model returned an empty response. Please try again.")
            return {"text": text}
        except HTTPException:
            raise
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                wait = 30 * (attempt + 1)
                await asyncio.sleep(wait)
            else:
                raise HTTPException(status_code=500, detail=err)
    raise HTTPException(status_code=429, detail="Rate limit exceeded after retries")

@app.post("/watermark/rewrite")
async def watermark_rewrite(req: WatermarkRewriteRequest):
    """Rewrite text via Groq to defeat word-choice AI watermarks (e.g. SynthID)."""
    system = (
        "You are a neutral rewriting assistant. Your only job is to rewrite the provided text "
        "in natural, human-sounding language. Preserve the full meaning and all facts exactly. "
        "Do NOT add commentary, headers, or explanations â€” output only the rewritten text. "
        "Vary sentence structure, word choices, and phrasing so the result reads as naturally "
        "human-written. Do not start your reply with phrases like 'Here is' or 'Sure'."
    )
    for attempt in range(5):
        try:
            text = generate(system, f"Rewrite this text:\n\n{req.text}", max_tokens=4096)
            return {"text": text}
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
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

# Serve static files (chat UI) â€” must be last
app.mount("/", StaticFiles(directory="static", html=True), name="static")
