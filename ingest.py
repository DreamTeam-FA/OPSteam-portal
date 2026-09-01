"""
Hi, Amy! - Course Content Ingestion Script
Run this once to process all files from Google Drive into PostgreSQL.
Run again anytime the course content is updated.
"""

import os
import io
import time
import tempfile
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import google.generativeai as genai
import PyPDF2
from docx import Document
from bs4 import BeautifulSoup
import PIL.Image
from database import init_db, already_processed, store_chunks

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
DRIVE_FOLDER_ID      = os.getenv("DRIVE_FOLDER_ID")
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY")

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# ── Init ──────────────────────────────────────────────────────────────────────
creds     = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_svc = build("drive", "v3", credentials=creds)
genai.configure(api_key=GEMINI_API_KEY)
flash     = genai.GenerativeModel("gemini-1.5-flash")

# ── Helpers ───────────────────────────────────────────────────────────────────

def list_drive_files(folder_id):
    files, page_token = [], None
    while True:
        resp = drive_svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, webViewLink)",
            pageSize=100,
            pageToken=page_token,
        ).execute()
        for item in resp.get("files", []):
            if item["mimeType"] == "application/vnd.google-apps.folder":
                files.extend(list_drive_files(item["id"]))
            else:
                files.append(item)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(file_id, mime_type):
    export_map = {
        "application/vnd.google-apps.document":     "text/plain",
        "application/vnd.google-apps.presentation": "text/plain",
        "application/vnd.google-apps.spreadsheet":  "text/csv",
    }
    if mime_type in export_map:
        req = drive_svc.files().export_media(fileId=file_id, mimeType=export_map[mime_type])
    else:
        req = drive_svc.files().get_media(fileId=file_id)

    buf = io.BytesIO()
    dl  = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)
    return buf


def extract_text_pdf(buf):
    reader = PyPDF2.PdfReader(buf)
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def extract_text_docx(buf):
    doc = Document(buf)
    return "\n".join(p.text for p in doc.paragraphs)


def gemini_summarize(text, file_name):
    prompt = f"""You are processing Amy Porterfield's training course material.

File: {file_name}

Content:
{text[:12000]}

Extract and clearly organize:
1. Key concepts and frameworks taught
2. Step-by-step action items
3. Important strategies and techniques
4. Specific instructions for tasks
5. Quotes or memorable insights

Be thorough — this will be used to answer team questions about the course."""
    return flash.generate_content(prompt).text


def gemini_describe_image(img, file_name):
    prompt = f"""This is a slide or image from Amy Porterfield's course: {file_name}

Describe in full detail:
1. All text visible in the image
2. Any diagrams, frameworks, or models shown
3. Key takeaways from this visual
4. Any action steps or strategies depicted"""
    return flash.generate_content([img, prompt]).text


def gemini_transcribe_video(video_path, file_name):
    print(f"    Uploading video to Gemini: {file_name}")
    video_file = genai.upload_file(video_path)
    while video_file.state.name == "PROCESSING":
        time.sleep(5)
        video_file = genai.get_file(video_file.name)
    if video_file.state.name == "FAILED":
        return f"[Video processing failed for {file_name}]"

    prompt = f"""This is a training video from Amy Porterfield's course: {file_name}

Provide a comprehensive summary including:
1. All instructions and how-to steps explained
2. Key concepts and frameworks discussed
3. Specific strategies and techniques taught
4. Action items the viewer should take
5. Any tools, platforms, or resources mentioned
6. Important quotes and insights"""
    return flash.generate_content([video_file, prompt]).text


# ── Main ──────────────────────────────────────────────────────────────────────

def ingest():
    print(f"\n🎓 Hi, Amy! — Course Ingestion")
    print(f"📂 Drive folder: {DRIVE_FOLDER_ID}\n")

    init_db()

    files = list_drive_files(DRIVE_FOLDER_ID)
    print(f"Found {len(files)} file(s)\n")

    for f in files:
        name = f["name"]
        fid  = f["id"]
        mime = f["mimeType"]

        print(f"📄 {name}")
        print(f"   Type: {mime}")

        if already_processed(fid):
            print("   ⏭  Already processed, skipping\n")
            continue

        try:
            if mime == "application/pdf":
                buf  = download_file(fid, mime)
                text = extract_text_pdf(buf)
                processed = gemini_summarize(text, name)
                n = store_chunks(name, fid, processed, "pdf")

            elif mime in (
                "application/vnd.google-apps.document",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ):
                buf = download_file(fid, mime)
                text = buf.read().decode("utf-8", errors="ignore") if mime == "application/vnd.google-apps.document" else extract_text_docx(buf)
                processed = gemini_summarize(text, name)
                n = store_chunks(name, fid, processed, "document")

            elif mime in (
                "application/vnd.google-apps.presentation",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ):
                buf  = download_file(fid, mime)
                text = buf.read().decode("utf-8", errors="ignore")
                processed = gemini_summarize(text, name)
                n = store_chunks(name, fid, processed, "slides")

            elif mime.startswith("image/"):
                buf = download_file(fid, mime)
                img = PIL.Image.open(buf)
                processed = gemini_describe_image(img, name)
                n = store_chunks(name, fid, processed, "image")

            elif mime.startswith("video/"):
                buf = download_file(fid, mime)
                ext = name.split(".")[-1] if "." in name else "mp4"
                with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                    tmp.write(buf.read())
                    tmp_path = tmp.name
                processed = gemini_transcribe_video(tmp_path, name)
                os.unlink(tmp_path)
                n = store_chunks(name, fid, processed, "video")

            elif mime == "text/html":
                buf  = download_file(fid, mime)
                html = buf.read().decode("utf-8", errors="ignore")
                text = BeautifulSoup(html, "html.parser").get_text(separator="\n")
                processed = gemini_summarize(text, name)
                n = store_chunks(name, fid, processed, "html")

            else:
                print("   ⚠️  Unsupported type, skipping\n")
                continue

            print(f"   ✅ Stored {n} chunk(s)\n")

        except Exception as e:
            print(f"   ❌ Error: {e}\n")

        time.sleep(1)

    print("🎉 Ingestion complete!")


if __name__ == "__main__":
    ingest()
