"""Netlify Function: /content-count"""
import json
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)


def handler(event, context):
    headers = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=8)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM course_chunks")
                count = cur.fetchone()[0]
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
