"""
generate_ghl_docs.py
Reads GHL Webinar transcripts from DB, generates structured docs via Groq,
and stores them back as library_chunks under the GHL Webinar category.

Generates per day:
  1. Executive Summary
  2. Key Takeaways
  3. Action Plan / SOP

Usage:
    python generate_ghl_docs.py
"""

import os, time
from dotenv import load_dotenv
load_dotenv()

from groq import Groq
from database import init_db, SessionLocal, store_library_chunks, library_already_processed
from sqlalchemy import text

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL  = "qwen/qwen3-8b-27b" if False else "qwen/qwen3.8-27b"  # adjust if needed
CATEGORY = "GHL Webinar"

# ── Helpers ────────────────────────────────────────────────────────────────────

def groq_generate(system_prompt, user_prompt, max_tokens=800):
    import re
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.4,
            )
            content = resp.choices[0].message.content or ""
            # Strip <think> tags
            content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
            return content
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                wait = 35 * (attempt + 1)
                print(f"   ⏳ Rate limit — waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Max retries exceeded")


def get_transcript(day_id: str) -> str:
    """Fetch all chunks for a GHL day and join them."""
    with SessionLocal() as db:
        rows = db.execute(
            text("""
                SELECT content FROM library_chunks
                WHERE file_id = :fid
                ORDER BY chunk_index
            """),
            {"fid": day_id}
        ).fetchall()
    return "\n\n".join(r.content for r in rows)


def chunk_text(text, size=12000, overlap=500):
    """Split text into overlapping chunks for multi-pass summarization."""
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + size])
        start += size - overlap
    return chunks


# ── Step 1: Summarize transcript in sections ───────────────────────────────────

def summarize_sections(transcript: str, day_label: str) -> str:
    """Summarize the transcript in 12k-char chunks, then combine."""
    sections = chunk_text(transcript, size=12000)
    section_summaries = []

    print(f"   📝 Summarizing {len(sections)} section(s)...")
    for i, section in enumerate(sections):
        print(f"      Section {i+1}/{len(sections)}...", end="", flush=True)
        summary = groq_generate(
            system_prompt=(
                "You are summarizing a section of a webinar transcript. "
                "Extract the main topics discussed, key points made, and any action items or tools mentioned. "
                "Be concise but thorough. Use bullet points. Focus on substance, not filler."
            ),
            user_prompt=f"Summarize this section of the {day_label} webinar transcript:\n\n{section}",
            max_tokens=600,
        )
        section_summaries.append(summary)
        print(f" ✅")
        time.sleep(2)  # avoid rate limit

    combined = "\n\n---\n\n".join(section_summaries)
    return combined


# ── Step 2: Generate final documents ──────────────────────────────────────────

def generate_executive_summary(section_summaries: str, day_label: str) -> str:
    print(f"   📄 Generating Executive Summary...", end="", flush=True)
    result = groq_generate(
        system_prompt=(
            "You are a professional content strategist. Write a clear, well-structured executive summary "
            "of a webinar based on section summaries. Include: what the webinar was about, who presented, "
            "the main topics covered, and the overall value/goal. Keep it to 3-5 paragraphs. "
            "Use professional but approachable language. No bullet points — flowing prose only."
        ),
        user_prompt=(
            f"Write an executive summary for: {day_label}\n\n"
            f"Based on these section summaries:\n\n{section_summaries[:8000]}"
        ),
        max_tokens=700,
    )
    print(" ✅")
    return result


def generate_key_takeaways(section_summaries: str, day_label: str) -> str:
    print(f"   💡 Generating Key Takeaways...", end="", flush=True)
    result = groq_generate(
        system_prompt=(
            "You are a professional business analyst. Extract the most valuable insights and lessons "
            "from a webinar. Format as numbered key takeaways (10-15 items). "
            "Each takeaway should be: specific, actionable, and directly useful. "
            "No fluff. Start each with a strong verb or insight. "
            "Group them loosely by theme if it helps."
        ),
        user_prompt=(
            f"Extract 10-15 key takeaways from: {day_label}\n\n"
            f"Based on these summaries:\n\n{section_summaries[:8000]}"
        ),
        max_tokens=800,
    )
    print(" ✅")
    return result


def generate_action_plan(section_summaries: str, day_label: str) -> str:
    print(f"   📋 Generating Action Plan / SOP...", end="", flush=True)
    result = groq_generate(
        system_prompt=(
            "You are a business operations expert. Based on a webinar, create a practical SOP "
            "(Standard Operating Procedure) and action plan that a team can actually follow. "
            "Format with clear sections:\n"
            "OVERVIEW — what this SOP covers and who it's for\n"
            "PREREQUISITES — what you need before starting\n"
            "STEP-BY-STEP PROCESS — numbered steps with sub-steps where needed\n"
            "TOOLS & RESOURCES — specific tools, platforms, or links mentioned\n"
            "SUCCESS METRICS — how to know if you've done it right\n"
            "Be specific and practical. Use the actual content from the webinar."
        ),
        user_prompt=(
            f"Create an SOP and action plan based on: {day_label}\n\n"
            f"Webinar content summaries:\n\n{section_summaries[:8000]}"
        ),
        max_tokens=800,
    )
    print(" ✅")
    return result


# ── Main ───────────────────────────────────────────────────────────────────────

DAYS = [
    {
        "id":    "ghl_ghl-day1",
        "label": "GHL Webinar Day 1 — Build Mini Web Apps for AI Client Activation",
        "short": "Day 1",
    },
    {
        "id":    "ghl_ghl-day2",
        "label": "GHL Webinar Day 2 — AI Client Engagement & Ongoing Strategy",
        "short": "Day 2",
    },
]

def main():
    init_db()

    for day in DAYS:
        print(f"\n{'='*60}")
        print(f"📅 Processing: {day['label']}")
        print(f"{'='*60}")

        # Check if transcript exists
        transcript = get_transcript(day["id"])
        if not transcript or len(transcript) < 500:
            print(f"   ⚠️  No transcript found for {day['short']} — skipping")
            continue

        print(f"   📜 Transcript: {len(transcript):,} chars / ~{len(transcript.split()):,} words")

        # Summarize in sections
        section_summaries = summarize_sections(transcript, day["label"])
        time.sleep(3)

        # Generate the 3 documents
        summary   = generate_executive_summary(section_summaries, day["label"])
        time.sleep(3)
        takeaways = generate_key_takeaways(section_summaries, day["label"])
        time.sleep(3)
        sop       = generate_action_plan(section_summaries, day["label"])
        time.sleep(2)

        # Store each as a library doc
        docs = [
            (f"GHL Webinar {day['short']} — Executive Summary",
             f"ghl_{day['short'].lower().replace(' ','')}_summary",
             f"Document: GHL Webinar {day['short']} — Executive Summary\nCategory: {CATEGORY}\n\n{summary}"),

            (f"GHL Webinar {day['short']} — Key Takeaways",
             f"ghl_{day['short'].lower().replace(' ','')}_takeaways",
             f"Document: GHL Webinar {day['short']} — Key Takeaways\nCategory: {CATEGORY}\n\n{takeaways}"),

            (f"GHL Webinar {day['short']} — Action Plan & SOP",
             f"ghl_{day['short'].lower().replace(' ','')}_sop",
             f"Document: GHL Webinar {day['short']} — Action Plan & SOP\nCategory: {CATEGORY}\n\n{sop}"),
        ]

        for doc_name, doc_id, content in docs:
            if library_already_processed(doc_id):
                print(f"   ⏭  Already exists: {doc_name}")
                continue
            n = store_library_chunks(doc_name, doc_id, content, "generated", CATEGORY)
            print(f"   💾 Stored: {doc_name} ({n} chunk(s))")

    print(f"\n{'='*60}")
    print("✅ All GHL docs generated and stored!")
    print("   Refresh the library to see them under GHL Webinar.")


if __name__ == "__main__":
    main()
