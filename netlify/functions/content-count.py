"""Netlify Function: /content-count"""
import json
import os
import urllib.parse
import pg8000.native

DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)


def handler(event, context):
    headers = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}
    try:
        p = urllib.parse.urlparse(DATABASE_URL)
        conn = pg8000.native.Connection(
            user=p.username, password=p.password,
            host=p.hostname, port=p.port or 5432,
            database=p.path.lstrip("/"), ssl_context=True,
            timeout=8,
        )
        try:
            rows = conn.run("SELECT COUNT(*) FROM course_chunks")
            count = rows[0][0] if rows else 0
        finally:
            conn.close()

        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"chunks": count, "ready": count > 0}),
        }
    except Exception as e:
        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"chunks": 0, "ready": False, "error": str(e)}),
        }
