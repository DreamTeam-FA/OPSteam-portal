"""
Netlify Function: /watermark/rewrite
Rewrites text via Groq to defeat word-choice AI watermarks (e.g. SynthID).
"""

import json
import os
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ACTIVE_MODEL = "qwen/qwen3.8-27b"

SYSTEM_PROMPT = (
    "You are a neutral rewriting assistant. Your only job is to rewrite the provided text "
    "in natural, human-sounding language. Preserve the full meaning and all facts exactly. "
    "Do NOT add commentary, headers, or explanations — output only the rewritten text. "
    "Vary sentence structure, word choices, and phrasing so the result reads as naturally "
    "human-written. Do not start your reply with phrases like 'Here is' or 'Sure'."
)


def handler(event, context):
    headers = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    try:
        body = json.loads(event.get("body") or "{}")
        text = body.get("text", "").strip()

        if not text:
            return {"statusCode": 400, "headers": headers,
                    "body": json.dumps({"detail": "No text provided"})}

        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=ACTIVE_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": f"Rewrite this text:\n\n{text}"},
            ],
            max_tokens=4096,
            temperature=0.85,
        )

        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"text": resp.choices[0].message.content}),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"detail": str(e)}),
        }
