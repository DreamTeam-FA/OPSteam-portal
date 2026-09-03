"""
Netlify Function: /chat
Amy Porterfield course assistant — searches DB, generates response via Groq.
"""

import json
import os
import psycopg2
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
DATABASE_URL  = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)
ACTIVE_MODEL  = "qwen/qwen3.8-27b"

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


def search_chunks(query, top_n=6):
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=8)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT file_name, source_type, content,
                       ts_rank(to_tsvector('english', content),
                               plainto_tsquery('english', %s)) AS rank
                FROM course_chunks
                WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s
            """, (query, query, top_n))
            rows = cur.fetchall()

            if not rows:
                cur.execute(
                    "SELECT file_name, source_type, content FROM course_chunks ORDER BY id DESC LIMIT %s",
                    (top_n,)
                )
                rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return "No specific course content matched. Answer based on general Amy Porterfield principles."

    parts = [f"[Source: {r[0]} ({r[1]})]\n{r[2]}" for r in rows]
    return "\n\n---\n\n".join(parts)


def handler(event, context):
    headers = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    try:
        body    = json.loads(event.get("body") or "{}")
        message = body.get("message", "").strip()

        if not message:
            return {"statusCode": 400, "headers": headers,
                    "body": json.dumps({"detail": "No message provided"})}

        context_text = search_chunks(message)
        system = AMY_SYSTEM_PROMPT.format(context=context_text)

        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=ACTIVE_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": message},
            ],
            max_tokens=2000,
            temperature=0.85,
        )

        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"response": resp.choices[0].message.content}),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"detail": str(e)}),
        }
