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

# -- Init
groq_client  = Groq(api_key=os.getenv("GROQ_API_KEY"))
ACTIVE_MODEL = "qwen/qwen3.8-27b"

def strip_thinking(text):
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

def generate(system_prompt, user_prompt, max_tokens=3000, json_mode=False):
    kwargs = dict(
        model=ACTIVE_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.4 if json_mode else 0.85,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = groq_client.chat.completions.create(**kwargs)
    content = resp.choices[0].message.content or ""
    return strip_thinking(content)

app = FastAPI(title="Hi, Amy!")

@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:
        print("[startup] DB init warning: " + str(e))

AMY_SYSTEM_PROMPT = """You are Amy - a warm, knowledgeable AI assistant built from Amy Porterfield's training course materials.

YOUR PERSONALITY:
- Warm, encouraging, and deeply practical
- You speak with genuine enthusiasm
- You break every concept into clear, actionable steps
- You are equally comfortable in English and Filipino

YOUR RULES:
- Answer ONLY based on the course content provided below
- Always give practical, specific, actionable advice
- Keep answers focused and digestible

COURSE CONTENT:
{context}"""

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
        raise HTTPException(status_code=422, detail="Could not read that URL: " + str(e))

@app.post("/content-week/generate")
async def content_week_generate(req: ContentWeekRequest):
    for attempt in range(5):
        try:
            text = generate(
                req.system_prompt,
                req.user_prompt,
                max_tokens=req.max_tokens,
                json_mode=req.json_mode,
            )
            if not text:
                raise HTTPException(status_code=500, detail="Model returned an empty response.")
            return {"text": text}
        except HTTPException:
            raise
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                await asyncio.sleep(30 * (attempt + 1))
            else:
                raise HTTPException(status_code=500, detail=err)
    raise HTTPException(status_code=429, detail="Rate limit exceeded after retries")

@app.post("/watermark/rewrite")
async def watermark_rewrite(req: WatermarkRewriteRequest):
    system = "You are a neutral rewriting assistant. Rewrite the provided text in natural, human-sounding language. Preserve the full meaning. Output only the rewritten text."
    for attempt in range(5):
        try:
            text = generate(system, "Rewrite this text:\n\n" + req.text, max_tokens=4096)
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

app.mount("/", StaticFiles(directory="static", html=True), name="static")