"""Netlify Function: /health"""
import json

def handler(event, context):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "ok", "app": "Hi, Amy!", "model": "qwen/qwen3.8-27b"}),
    }
