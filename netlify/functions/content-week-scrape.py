"""
Netlify Function: /content-week/scrape
Scrapes a URL server-side — avoids CORS issues in the browser.
"""

import json
import os
import requests
from bs4 import BeautifulSoup


def handler(event, context):
    headers = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    try:
        params = event.get("queryStringParameters") or {}
        url    = params.get("url", "").strip()

        if not url:
            return {"statusCode": 400, "headers": headers,
                    "body": json.dumps({"detail": "No URL provided"})}

        req_headers = {"User-Agent": "Mozilla/5.0 (compatible; OPSteam-bot/1.0)"}
        r = requests.get(url, headers=req_headers, timeout=10, allow_redirects=True)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = " ".join(soup.get_text(separator=" ").split())

        if len(text) < 80:
            return {"statusCode": 422, "headers": headers,
                    "body": json.dumps({"detail": "Page has too little readable text."})}

        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"text": text[:8000]}),
        }

    except requests.HTTPError as e:
        return {"statusCode": 422, "headers": headers,
                "body": json.dumps({"detail": f"Could not read that URL: {e}"})}
    except Exception as e:
        return {"statusCode": 422, "headers": headers,
                "body": json.dumps({"detail": f"Could not read that URL: {e}"})}
