"""
Netlify Function: /content-week/generate
Proxies Groq calls for the Content Week tool — key stays server-side.
"""

import json
import os
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ACTIVE_MODEL = "qwen/qwen3.8-27b"


def handler(event, context):
    headers = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    try:
        body          = json.loads(event.get("body") or "{}")
        system_prompt = body.get("system_prompt", "")
        user_prompt   = body.get("user_prompt", "")
        max_tokens    = int(body.get("max_tokens", 3000))
        json_mode     = bool(body.get("json_mode", False))

        if not user_prompt:
            return {"statusCode": 400, "headers": headers,
                    "body": json.dumps({"detail": "No prompt provided"})}

        client = Groq(api_key=GROQ_API_KEY)

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

        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content

        if not text:
            return {"statusCode": 500, "headers": headers,
                    "body": json.dumps({"detail": "Model returned an empty response. Please try again."})}

        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"text": text}),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"detail": str(e)}),
        }
