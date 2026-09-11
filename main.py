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
MODEL_SMART  = "llama-3.3-70b-versatile"   # deep reasoning, strategy, multi-step
MODEL_FAST   = "qwen/qwen3.8-27b"           # quick lookups, simple questions

# Keywords that signal a complex question needing the smarter model
_COMPLEX_SIGNALS = [
    "how do i", "how can i", "help me", "step by step", "walk me through",
    "strategy", "plan", "framework", "build", "create", "launch", "design",
    "explain", "analyze", "compare", "difference between", "why", "should i",
    "what's the best", "what is the best", "how to", "advice", "recommend",
    "outline", "structure", "write", "draft", "sequence", "roadmap",
    "funnel", "email", "webinar", "course", "workshop", "audience",
    "sales", "marketing", "pricing", "validate", "niche", "topic",
]

def pick_model(message: str) -> str:
    """Return the appropriate model based on question complexity."""
    ml = message.lower()
    word_count = len(ml.split())
    # Long messages are inherently complex
    if word_count >= 20:
        return MODEL_SMART
    # Short but substantive questions
    if any(sig in ml for sig in _COMPLEX_SIGNALS):
        return MODEL_SMART
    # Multi-sentence = complex
    if message.count("?") > 1 or message.count(".") > 1:
        return MODEL_SMART
    # Simple: short, single-clause, lookup-style
    return MODEL_FAST

def strip_thinking(text):
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

def generate(system_prompt, user_prompt, max_tokens=3000, json_mode=False, message_for_routing: str = None):
    model = pick_model(message_for_routing or user_prompt)
    kwargs = dict(
        model=model,
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

AMY_SYSTEM_PROMPT = """You are Amy — an AI assistant modeled on Amy Porterfield's teaching style and course methodology.

LANGUAGE RULE (HIGHEST PRIORITY):
- Always respond in English, no exceptions.
- If the user writes in Filipino/Tagalog, understand it but reply in English.
- Never start a response with Filipino words. Never mix languages.

YOUR PERSONALITY:
- Warm, direct, and deeply practical — like a brilliant mentor who's been there
- You cite specific frameworks and steps from the course material provided
- You give advice that is SPECIFIC to what the user asked — not generic advice
- You name the actual document or module the advice comes from when you reference it

YOUR RULES:
- Ground every answer in the COURSE CONTENT below — quote specific frameworks, steps, and strategies from it
- If the course material directly addresses the question, lead with that content
- Be specific: name exact steps, exact frameworks, exact tools mentioned in the material
- If the course content is thin for this topic, say which section would be most relevant and give the best advice you can from what's there
- Never give advice that sounds like it could come from any generic business coach — it must feel like it came from Amy Porterfield's specific methods

COURSE CONTENT:
{context}"""

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str

class ChatSummaryRequest(BaseModel):
    messages: list   # [{role: "user"|"amy", text: "..."}]

SUMMARY_SYSTEM = """You are a professional advisor who synthesizes advice from Amy Porterfield's course methodology into a clean, structured summary document.

Given a conversation between a user and Amy, extract and organize Amy's advice into a rich, actionable summary. Do NOT write a transcript — write a polished advisory document.

OUTPUT FORMAT (use exactly these section headers with emoji):

🎯 Topic
One sentence describing what this advice covers.

💡 Core Recommendations
Number each recommendation. Be specific and concrete. Reference any frameworks or course material Amy mentioned.

✅ Action Steps
A checklist of concrete things the user should do, in priority order. Start each with a verb.

📚 Frameworks & Resources Referenced
List any specific Amy Porterfield frameworks, course modules, documents, or tools mentioned.

⚡ Quick Win — Start Here
The single most important first step from all of Amy's advice.

RULES:
- Write in second person ("you should...", "your next step...")
- Be specific — use exact names, numbers, steps from the conversation
- This is a polished document someone would save and refer back to
- Do NOT include any meta-commentary about the conversation itself"""

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
        # Search both tables: course_chunks (original Amy course) + library_chunks (richer content)
        course_ctx = search_chunks(req.message)

        # Also pull from library — DCA content, SOPs, AI Prompts, etc.
        lib_ctx = search_library_chunks(req.message, top_n=6)

        # If library returns nothing for this specific query, try DCA category
        if not lib_ctx or len(lib_ctx) < 200:
            lib_ctx_dca = search_library_chunks(req.message, top_n=4, category="Digital Course Academy")
            if lib_ctx_dca and len(lib_ctx_dca) > len(lib_ctx or ""):
                lib_ctx = lib_ctx_dca

        parts = []
        if course_ctx and len(course_ctx) > 100:
            parts.append(f"[From Amy's Course Materials]\n{course_ctx}")
        if lib_ctx and len(lib_ctx) > 100:
            parts.append(f"[From the Knowledge Library]\n{lib_ctx}")
        if not parts:
            parts.append("(No specific course content matched — answer from Amy Porterfield's general methodology.)")

        context = "\n\n===\n\n".join(parts)
        system  = AMY_SYSTEM_PROMPT.format(context=context)
        resp    = generate(system, req.message, max_tokens=1000, message_for_routing=req.message)
        return ChatResponse(response=resp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat/summary")
async def chat_summary(req: ChatSummaryRequest):
    try:
        if not req.messages:
            raise HTTPException(status_code=400, detail="No messages provided")
        # Build conversation text — only include Amy's turns for summarization
        convo_parts = []
        for m in req.messages:
            role = m.get("role", "")
            text = m.get("text", "").strip()
            if not text:
                continue
            label = "User" if role == "user" else "Amy"
            convo_parts.append(f"{label}: {text}")
        convo_text = "\n\n".join(convo_parts)
        resp = generate(
            SUMMARY_SYSTEM,
            f"Here is the conversation to summarize:\n\n{convo_text}",
            max_tokens=1200,
            message_for_routing="help me write a full detailed strategy plan with action steps frameworks and recommendations",
        )
        return {"summary": resp}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok", "app": "Hi, Amy!", "models": [MODEL_SMART, MODEL_FAST]}

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

LIBRARIAN_SYSTEM_PROMPT = """You are the AI Librarian — a sharp, direct, genuinely helpful assistant for the OPSteam knowledge library.

LANGUAGE RULE (HIGHEST PRIORITY):
- Always respond in English. If the user writes in Filipino, understand it but reply in English.
- Never mix languages or start with Filipino words.

YOUR PERSONALITY:
- Confident and knowledgeable — you know this library inside out
- Warm but efficient — you get people what they need without unnecessary fluff
- You surface relevant content proactively and suggest related docs

YOUR KNOWLEDGE BASE:
The library contains these categories of content:
• AI Prompts — ready-to-use AI prompt templates for content creation, marketing, course building, and more
• AI Advantage — masterclass sessions and summit recordings (use these prompts to unlock AI for business)
• Digital Course Academy (DCA) — Amy Porterfield's full course-building program: Start From Scratch and Ready To Launch tracks
• Donald Miller — StoryBrand framework, Hero on a Mission, messaging guides
• Category of One — positioning and differentiation content
• SOPs & Processes — standard operating procedures and team workflows
• Sales & Marketing — sales frameworks, email sequences, webinar scripts, AI chatbot setups
• Resources — tools, templates, Google Workspace guides, bootcamp materials
• GHL Webinar — Go High Level webinar transcripts and resources

YOUR RULES:
- Use the retrieved LIBRARY CONTENT below as your primary source — quote specific document names when referencing them
- If the retrieved content is thin but you know the category has relevant material, say so and recommend browsing that category
- Never claim the library is empty or unavailable — it always has content; search results just vary in specificity
- Always give a genuinely useful answer — be specific, practical, and actionable
- When listing prompts or steps, format them clearly

LIBRARY CONTENT RETRIEVED FOR THIS QUERY:
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

# Maps query keywords → library category to try as a fallback
KEYWORD_CATEGORY_MAP = [
    (['prompt', 'prompts', 'ai prompt', 'template'], 'AI Prompts'),
    (['donald miller', 'storybrand', 'hero on a mission', 'messaging'], 'Donald Miller'),
    (['category of one', 'positioning', 'differentiation'], 'Category of One'),
    (['dca', 'digital course academy', 'start from scratch', 'ready to launch',
      'course building', 'online course', 'launch'], 'Digital Course Academy'),
    (['ai advantage', 'masterclass', 'summit', 'aia'], 'AI Advantage'),
    (['sop', 'process', 'workflow', 'procedure'], 'SOPs & Processes'),
    (['sales', 'marketing', 'email sequence', 'webinar', 'chatbot', 'funnel'], 'Sales & Marketing'),
    (['ghl', 'go high level', 'highlevel'], 'GHL Webinar'),
    (['resource', 'tool', 'template', 'workspace', 'google', 'bootcamp'], 'Resources'),
]

def infer_category_from_message(msg: str) -> str | None:
    """Return the most likely library category for this message, or None."""
    ml = msg.lower()
    for keywords, cat in KEYWORD_CATEGORY_MAP:
        if any(kw in ml for kw in keywords):
            return cat
    return None

@app.post("/librarian/chat")
async def librarian_chat(req: LibrarianChatRequest):
    try:
        msg_lower = req.message.lower()

        # 1a. Primary search — within requested category or all
        lib_context = search_library_chunks(req.message, top_n=8, category=req.category)
        lib_has_content = bool(lib_context and len(lib_context) > 200)

        # 1b. Fallback: if thin results, try keyword-inferred category
        if not lib_has_content and not req.category:
            inferred_cat = infer_category_from_message(req.message)
            if inferred_cat:
                lib_context = search_library_chunks(req.message, top_n=8, category=inferred_cat)
                lib_has_content = bool(lib_context and len(lib_context) > 200)
                # If FTS still fails, grab the most recent chunks from that category
                if not lib_has_content:
                    cat_docs = get_library_docs(category=inferred_cat)[:4]
                    fallback_parts = []
                    for doc in cat_docs:
                        snippet = get_document_content(doc['file_id'])[:1200]
                        if snippet:
                            fallback_parts.append(f"[{doc['file_name']}]\n{snippet}")
                    if fallback_parts:
                        lib_context = "\n\n---\n\n".join(fallback_parts)
                        lib_has_content = True

        # 2. Decide whether to search the web
        wants_web = req.search_web or any(t in msg_lower for t in WEB_SEARCH_TRIGGERS)
        web_context, sources = None, []
        if wants_web:
            web_context, sources = do_web_search(req.message)

        # 3. Build combined context
        parts = []
        if lib_has_content:
            parts.append(lib_context)
        if web_context:
            parts.append(f"WEB SEARCH RESULTS:\n{web_context}")
        if not parts:
            parts.append("(No specific documents retrieved — answer from your knowledge of the library categories listed in YOUR KNOWLEDGE BASE above.)")

        combined = "\n\n---\n\n".join(parts)

        # 4. Web note
        web_note = "\n\nWhen using web results, cite sources by name and URL." if sources else ""
        system = LIBRARIAN_SYSTEM_PROMPT.format(context=combined) + web_note

        resp = generate(system, req.message, max_tokens=900, message_for_routing=req.message)

        # 5. Parse offer flag (only when web wasn't searched and library was empty)
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