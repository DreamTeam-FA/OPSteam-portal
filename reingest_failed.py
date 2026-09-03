"""
Hi, Amy! — Re-ingest Failed Files
Retries any files that were skipped or failed during the original ingestion run.
Checks the database for files NOT yet stored, then re-runs them through ingest.

Usage:
    python reingest_failed.py               # Dry run — lists what would be re-processed
    python reingest_failed.py --run         # Actually re-ingest the failed files
    python reingest_failed.py --run --file "filename.pdf"  # Re-ingest one specific file
"""

import os
import sys
import time
import argparse
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from database import init_db, already_processed, SessionLocal
from sqlalchemy import text

load_dotenv()

SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
DRIVE_FOLDER_ID      = os.getenv("DRIVE_FOLDER_ID")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

creds     = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_svc = build("drive", "v3", credentials=creds)


def list_drive_files(folder_id):
    """Recursively list all files in the Drive folder."""
    files, page_token = [], None
    while True:
        resp = drive_svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, size, webViewLink)",
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


def get_processed_file_ids():
    """Return a set of Drive file IDs that are already in the database."""
    with SessionLocal() as db:
        rows = db.execute(text("SELECT DISTINCT file_id FROM course_chunks")).fetchall()
    return {row[0] for row in rows}


def main():
    parser = argparse.ArgumentParser(description="Re-ingest failed Drive files")
    parser.add_argument("--run",  action="store_true", help="Actually re-ingest (default is dry run)")
    parser.add_argument("--file", type=str, default=None, help="Re-ingest only this specific filename")
    args = parser.parse_args()

    init_db()

    print("\n🔍 Scanning Drive folder...")
    all_files = list_drive_files(DRIVE_FOLDER_ID)
    print(f"   Found {len(all_files)} file(s) in Drive\n")

    processed_ids = get_processed_file_ids()
    print(f"✅ Already in database: {len(processed_ids)} file(s)")

    # Find files NOT in the database
    failed = [f for f in all_files if f["id"] not in processed_ids]

    # If targeting a specific file, filter further
    if args.file:
        failed = [f for f in failed if args.file.lower() in f["name"].lower()]

    if not failed:
        print("\n🎉 Nothing to re-ingest — all files are already processed!")
        return

    print(f"\n⚠️  Files NOT yet in database ({len(failed)}):")
    for i, f in enumerate(failed, 1):
        size_mb = int(f.get("size", 0)) / (1024 * 1024) if f.get("size") else 0
        print(f"   {i:3}. {f['name']} ({f['mimeType']}) {size_mb:.1f} MB")

    if not args.run:
        print("\n💡 Dry run complete. Run with --run to actually re-ingest these files.")
        return

    # Actually re-ingest by importing and calling ingest logic
    print("\n🚀 Starting re-ingestion...")

    # Import ingest functions (must be in same directory)
    from ingest import (
        download_file, extract_text_pdf, extract_text_docx,
        gemini_summarize, gemini_describe_image, gemini_transcribe_video,
        ACTIVE_MODEL
    )
    from database import store_chunks

    success, skipped, errors = 0, 0, []

    for f in failed:
        name = f["name"]
        fid  = f["id"]
        mime = f["mimeType"]

        print(f"\n📄 {name}")

        # Double-check it's not processed now (in case another process ran)
        if already_processed(fid):
            print("   ⏭  Already processed now, skipping")
            skipped += 1
            continue

        try:
            if mime == "application/pdf":
                from io import BytesIO
                buf  = download_file(fid, mime)
                text = extract_text_pdf(buf)
                processed = gemini_summarize(text, name)
                n = store_chunks(name, fid, processed, "pdf")

            elif mime in (
                "application/vnd.google-apps.document",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ):
                buf  = download_file(fid, mime)
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
                buf       = download_file(fid, mime)
                img_bytes = buf.read()
                processed = gemini_describe_image(img_bytes, name)
                n = store_chunks(name, fid, processed, "image")

            elif mime.startswith("video/"):
                import tempfile
                meta    = drive_svc.files().get(fileId=fid, fields="size,webViewLink").execute()
                size_mb = int(meta.get("size", 0)) / (1024 * 1024)
                link    = meta.get("webViewLink", "")
                print(f"   📹 {size_mb:.0f} MB — transcribing...")
                tmp_path = None
                try:
                    buf = download_file(fid, mime)
                    ext = name.split(".")[-1] if "." in name else "mp4"
                    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                        tmp.write(buf.read())
                        tmp_path = tmp.name
                    processed = gemini_transcribe_video(tmp_path, name)
                    n = store_chunks(name, fid, processed, "video")
                except Exception as vid_err:
                    print(f"   ⚠️  Transcription failed ({vid_err}) — storing as reference")
                    summary = (
                        f"Video lesson: {name}\nSize: {size_mb:.0f} MB\nLink: {link}\n"
                        f"Topic (from filename): {name.replace('_', ' ').replace('.mp4', '')}\n"
                        f"This video could not be auto-transcribed. Watch directly in Google Drive."
                    )
                    n = store_chunks(name, fid, summary, "video_reference")
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.unlink(tmp_path)

            elif mime == "text/html":
                from bs4 import BeautifulSoup
                buf  = download_file(fid, mime)
                html = buf.read().decode("utf-8", errors="ignore")
                text = BeautifulSoup(html, "html.parser").get_text(separator="\n")
                processed = gemini_summarize(text, name)
                n = store_chunks(name, fid, processed, "html")

            else:
                print(f"   ⚠️  Unsupported MIME type: {mime} — skipping")
                skipped += 1
                continue

            print(f"   ✅ Stored {n} chunk(s)")
            success += 1

        except Exception as e:
            print(f"   ❌ Error: {e}")
            errors.append({"file": name, "error": str(e)})

        time.sleep(60)  # 60s between files -- lets rate-limit window reset

    # Summary
    print(f"\n{'='*50}")
    print(f"🏁 Re-ingestion complete!")
    print(f"   ✅ Success:  {success}")
    print(f"   ⏭  Skipped: {skipped}")
    print(f"   ❌ Errors:  {len(errors)}")
    if errors:
        print("\nFailed files:")
        for e in errors:
            print(f"   • {e['file']}: {e['error']}")


if __name__ == "__main__":
    main()
