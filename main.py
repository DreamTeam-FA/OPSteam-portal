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
from database import (init_db, search_chunks,
                       search_library_chunks, get_library_docs,
                       get_document_content, get_library_videos,
                       library_already_processed, library_video_exists)

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
        resp    = generate(system, req.message, max_tokens=800)
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
            text = generate(system, "Rewrite this text:\n\n" + req.text, max_tokens=1500)
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

# ── Library endpoints ──────────────────────────────────────────────────────────

LIBRARIAN_SYSTEM_PROMPT = """You are the AI Librarian — a fun, energetic, friendly but professional and intelligent assistant for the OPSteam knowledge library.

YOUR PERSONALITY:
- Energetic, warm, and engaging — like a brilliant colleague who loves sharing knowledge
- Professional and precise — you give accurate, well-organized answers
- You love helping people find exactly what they need
- You are equally comfortable in English and Filipino

YOUR KNOWLEDGE BASE:
You have access to a library of documents including:
- Donald Miller frameworks (StoryBrand, Hero on a Mission, etc.)
- AI Advantage masterclasses and summit recordings
- SOPs and process documents
- AI prompts and templates
- Sales and marketing resources
- Category of One sessions
- And much more

YOUR RULES:
- Answer based on the library content provided
- When you reference a document, mention its name so users can find it in the library
- Keep answers practical and actionable
- If content isn't in the library, say so honestly

LIBRARY CONTENT:
{context}"""

class LibrarianChatRequest(BaseModel):
    message: str
    category: str = None
    search_web: bool = False   # True when user explicitly asks to search online

class LibrarySyncRequest(BaseModel):
    force: bool = False

# Keywords that trigger automatic web search
WEB_SEARCH_TRIGGERS = [
    "search online", "search the web", "search web", "look it up",
    "find online", "google it", "browse", "web search", "search for",
    "find me ", "look up", "check online", "what's the latest",
    "current news", "recent ", "up to date", "latest on",
]

def do_web_search(query: str, max_results: int = 5):
    """Search the web using DuckDuckGo. Returns (text_context, sources_list)."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return None, []
        formatted = []
        sources = []
        for r in results:
            formatted.append(f"Title: {r['title']}\nURL: {r['href']}\nSummary: {r['body']}")
            sources.append({"title": r["title"], "url": r["href"]})
        return "\n\n".join(formatted), sources
    except Exception as e:
        return None, []

@app.post("/librarian/chat")
async def librarian_chat(req: LibrarianChatRequest):
    try:
        # 1. Library search
        lib_context = search_library_chunks(req.message, top_n=6, category=req.category)
        lib_has_content = bool(lib_context and len(lib_context) > 150)

        # 2. Decide whether to search the web
        msg_lower = req.message.lower()
        wants_web = req.search_web or any(t in msg_lower for t in WEB_SEARCH_TRIGGERS)

        web_context, sources = None, []
        if wants_web:
            web_context, sources = do_web_search(req.message)

        # 3. Build combined context
        parts = []
        if lib_has_content:
            parts.append(f"LIBRARY CONTENT:\n{lib_context}")
        if web_context:
            parts.append(f"WEB SEARCH RESULTS:\n{web_context}")
        if not parts:
            parts.append("No matching library content found.")

        combined = "\n\n---\n\n".join(parts)

        # 4. Prompt — tell it to offer web search only when library is thin
        offer_hint = ""
        if not wants_web and not lib_has_content:
            offer_hint = (
                "\n\nIf you cannot fully answer from the library content, "
                "end your reply with exactly: [[OFFER_SEARCH]] so the UI can show a search button."
            )

        web_note = ""
        if sources:
            web_note = "\n\nWhen using web results, cite sources by name and URL."

        system = LIBRARIAN_SYSTEM_PROMPT.format(context=combined) + offer_hint + web_note
        resp = generate(system, req.message, max_tokens=800)

        # 5. Parse offer flag
        offer_search = "[[OFFER_SEARCH]]" in resp
        clean_resp = resp.replace("[[OFFER_SEARCH]]", "").strip()

        return {
            "response": clean_resp,
            "sources": sources,
            "searched_web": bool(web_context),
            "offer_search": offer_search and not wants_web,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/library/docs")
async def library_docs(category: str = None):
    try:
        docs = get_library_docs(category=category)
        return {"docs": docs, "total": len(docs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/library/doc/{file_id}")
async def library_doc_content(file_id: str):
    try:
        content = get_document_content(file_id)
        if not content:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"content": content}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/library/videos")
async def library_videos(category: str = None):
    try:
        videos = get_library_videos(category=category)
        return {"videos": videos, "total": len(videos)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/library/categories")
async def library_categories():
    try:
        from database import SessionLocal
        from sqlalchemy import text as sql_text
        with SessionLocal() as db:
            doc_cats = db.execute(
                sql_text("SELECT DISTINCT category FROM library_chunks ORDER BY category")
            ).fetchall()
            vid_cats = db.execute(
                sql_text("SELECT DISTINCT category FROM library_videos ORDER BY category")
            ).fetchall()
        all_cats = sorted(set(
            [r.category for r in doc_cats] + [r.category for r in vid_cats]
        ))
        return {"categories": all_cats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/library/sync")
async def library_sync():
    """Check Google Drive for new files and ingest them (called on page load + manual refresh)."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build as gdrive_build
        import io

        sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "hi-amy-service-account.json")
        if not os.path.exists(sa_file):
            return {"synced": 0, "message": "Service account not available on this server"}

        creds = service_account.Credentials.from_service_account_file(
            sa_file, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        drive = gdrive_build("drive", "v3", credentials=creds)

        LIBRARY_FOLDER_ID = "1hJI4zz7u3rh8kxKwvC3p8-Rl4vOs5iE3"

        # Import ingest helpers
        from ingest_library import (walk_folder, list_folder)
        docs_done, vids_done, skipped = [], [], []
        walk_folder(LIBRARY_FOLDER_ID, [], True, docs_done, vids_done, skipped)

        return {
            "synced_docs": len(docs_done),
            "synced_videos": len(vids_done),
            "skipped": len(skipped),
            "message": f"Sync complete: {len(docs_done)} docs, {len(vids_done)} videos added"
        }
    except Exception as e:
        return {"synced": 0, "message": f"Sync unavailable: {str(e)}"}

@app.get("/library/stats")
async def library_stats():
    try:
        from database import SessionLocal
        from sqlalchemy import text as sql_text
        with SessionLocal() as db:
            doc_count = db.execute(
                sql_text("SELECT COUNT(DISTINCT file_id) FROM library_chunks")
            ).scalar()
            vid_count = db.execute(
                sql_text("SELECT COUNT(*) FROM library_videos")
            ).scalar()
        return {"documents": doc_count or 0, "videos": vid_count or 0}
    except Exception as e:
        return {"documents": 0, "videos": 0}

app.mount("/", StaticFiles(directory="static", html=True), name="static")