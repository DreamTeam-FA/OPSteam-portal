"""
Netlify Function: /content-week/scrape
Scrapes a URL server-side and returns page text + brand colors.
"""

import json
import os
import re
import requests
from bs4 import BeautifulSoup
from collections import Counter


def extract_brand_colors(raw_html):
    colors = []

    # meta theme-color
    m = re.search(
        r'<meta[^>]+name=["\']theme-color["\'][^>]+content=["\']([^"\']+)["\']',
        raw_html, re.IGNORECASE
    )
    if m:
        c = m.group(1).strip()
        if re.match(r'^#[0-9a-fA-F]{3,8}$', c):
            colors.append(c.upper())

    # CSS custom properties that look like brand/primary colors
    css_vars = re.findall(
        r'--(?:primary|brand|accent|main|highlight|key|color-primary|theme)[^:]*:\s*(#[0-9a-fA-F]{6})',
        raw_html, re.IGNORECASE
    )
    colors.extend(c.upper() for c in css_vars[:5])

    # Most-used hex colors in <style> blocks
    style_blocks = re.findall(r'<style[^>]*>(.*?)</style>', raw_html, re.DOTALL | re.IGNORECASE)
    hex_all = []
    for block in style_blocks:
        hex_all.extend('#' + h.upper() for h in re.findall(r'#([0-9a-fA-F]{6})\b', block))

    # Skip near-white / near-black / common grays
    skip = {
        '#FFFFFF','#000000','#EEEEEE','#F5F5F5','#FAFAFA','#F0F0F0',
        '#111111','#222222','#333333','#444444','#555555','#666666',
        '#777777','#888888','#999999','#AAAAAA','#BBBBBB','#CCCCCC',
        '#DDDDDD','#E0E0E0','#E5E5E5','#F2F2F2',
    }
    for color, _ in Counter(hex_all).most_common(30):
        if color not in skip:
            colors.append(color)

    # Deduplicate, keep first 4
    seen, unique = set(), []
    for c in colors:
        if c not in seen:
            seen.add(c)
            unique.append(c)
        if len(unique) >= 4:
            break

    return unique


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

        # Extract brand colors BEFORE stripping style tags
        colors = extract_brand_colors(r.text)

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
            "body": json.dumps({"text": text[:8000], "colors": colors}),
        }

    except requests.HTTPError as e:
        return {"statusCode": 422, "headers": headers,
                "body": json.dumps({"detail": f"Could not read that URL: {e}"})}
    except Exception as e:
        return {"statusCode": 422, "headers": headers,
                "body": json.dumps({"detail": f"Could not read that URL: {e}"})}
