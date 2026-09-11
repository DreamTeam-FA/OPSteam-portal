"""
Hi, Amy! — Library Ingest Script
Processes the Google Drive Library folder into library_chunks and library_videos tables.
Skips files already in the database (incremental sync).

Usage:
    python ingest_library.py          # Dry run — shows what would be ingested
    python ingest_library.py --run    # Actually ingest
"""

import os, sys, io, time, argparse
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import pdfplumber

load_dotenv()

SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "hi-amy-service-account.json")
LIBRARY_FOLDER_ID    = "1hJI4zz7u3rh8kxKwvC3p8-Rl4vOs5iE3"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

creds     = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_svc = build("drive", "v3", credentials=creds)

# ── Skip logic ─────────────────────────────────────────────────────────────────
# These folder IDs are already ingested as Amy's course content — skip entirely
DCA_FOLDER_IDS = set()  # populated at runtime via detect_dca_folders()

SKIP_MIMES = {
    "application/x-zip",
    "application/x-zip-compressed",
    "application/zip",
    "application/octet-stream",
    "text/markdown",       # skill markdown files
    "text/html",           # html files
    "image/jpeg",
    "image/png",
    "image/gif",
    "application/json",
    "text/vtt",            # subtitle files — prefer .txt transcripts
    "application/x-zip",
}

SKIP_NAME_PATTERNS = [
    "~$",                  # Word temp/lock files
    ".skill",              # Claude skill bundles
    "don-miller-skill",
    "mark-timm-content-system",
    "category-of-one.md",
    "your-content-week.zip",
    "DCA Course.zip",
    "hi-amy-assistant",
    "hi-amy-service-account",
    "hi amy project",
    "workspace-studio-guide.html",
    "bookkeeping-landing-page.html",
    "claude-design-deck-autoplay.html",
    "RESULT_Founding Members",
]

# ── Category mapping ───────────────────────────────────────────────────────────
# Maps (folder_id_or_name_hint) → (category, subcategory)
# We build this dynamically as we walk the tree

def categorize(file_name: str, folder_path: list) -> tuple:
    """Return (category, subcategory) based on folder path and filename."""
    path_str = " / ".join(folder_path).lower()

    if "donald miller" in path_str:
        return ("Donald Miller", None)

    if "category of one" in path_str:
        return ("Category of One", None)

    if "ai advantage dump" in path_str:
        return ("AI Advantage", None)

    if "ai prompts" in path_str:
        return ("AI Prompts", None)

    if "ghl webinar" in path_str:
        return ("GHL Webinar", None)

    if "your content week" in path_str:
        # Only non-tool files
        return ("Content Week", None)

    # AI Corner subfolders
    if "ai corner" in path_str:
        if "digital course academy" in path_str:
            # Determine DCA subcategory
            if "ready to launch" in path_str:
                return ("Digital Course Academy", "Ready to Launch")
            if "start from scratch" in path_str:
                return ("Digital Course Academy", "Start from Scratch")
            if "bonus" in path_str:
                return ("Digital Course Academy", "Bonus")
            if "q&a vault" in path_str or "q&a and event" in path_str:
                return ("Digital Course Academy", "Q&A & Replays")
            if "resources & tech" in path_str or "resources" in path_str:
                # Sub-sub-categories
                if "getting started" in path_str:
                    return ("Digital Course Academy", "Getting Started")
                if "grow your audience" in path_str:
                    return ("Digital Course Academy", "Grow Your Audience")
                if "build your course" in path_str:
                    return ("Digital Course Academy", "Build Your Course")
                if "go live" in path_str or "webinar" in path_str:
                    return ("Digital Course Academy", "Webinars & Live")
                return ("Digital Course Academy", "Resources")
            return ("Digital Course Academy", "General")

        if "sales framework" in path_str:
            return ("Sales & Marketing", "Sales Framework")
        if "ai chatbot" in path_str:
            return ("Sales & Marketing", "AI Chatbot")
        if "write the follow" in path_str or "re-engage" in path_str:
            return ("Sales & Marketing", "Re-Engage Leads")
        if "create website" in path_str or "build" in path_str.split("/")[-1]:
            return ("Sales & Marketing", "Build Website")
        if "claude design" in path_str:
            return ("Sales & Marketing", "Claude Design")
        if "tabme" in path_str or "5-minute" in path_str:
            return ("Resources", "Tools & Templates")
        if "google workspace" in path_str:
            return ("Resources", "Google Workspace")
        # SOP documents at AI Corner root
        return ("SOPs & Processes", None)

    # Top-level documents
    name_lower = file_name.lower()
    if "ai advantage" in name_lower:
        return ("AI Advantage", None)
    if "re-engage" in name_lower or "follow-up" in name_lower:
        return ("Sales & Marketing", "Re-Engage Leads")
    if "build" in name_lower and "website" in name_lower:
        return ("Sales & Marketing", "Build Website")
    if "resource library" in name_lower or "hacks" in name_lower:
        return ("Resources", "Resource Library")
    if "bootcamp" in name_lower or "masterclass" in name_lower:
        return ("Resources", "Bootcamp & Training")
    if "invoice" in name_lower:
        return ("Resources", "Finance")
    if "voice sprint" in name_lower:
        return ("Resources", "Content Creation")

    return ("General", None)


def should_skip_name(name: str) -> bool:
    for pat in SKIP_NAME_PATTERNS:
        if pat.lower() in name.lower():
            return True
    return False


def is_video(mime: str) -> bool:
    return mime.startswith("video/") or mime.startswith("audio/")


def is_document(mime: str) -> bool:
    return mime in (
        "application/vnd.google-apps.document",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.google-apps.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


# ── Drive helpers ──────────────────────────────────────────────────────────────

def list_folder(folder_id: str):
    files, page_token = [], None
    while True:
        resp = drive_svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, size, webViewLink)",
            pageSize=100, pageToken=page_token,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(file_id: str, mime: str) -> io.BytesIO:
    buf = io.BytesIO()
    if mime == "application/vnd.google-apps.document":
        req = drive_svc.files().export_media(fileId=file_id, mimeType="text/plain")
    elif mime == "application/vnd.google-apps.presentation":
        req = drive_svc.files().export_media(fileId=file_id, mimeType="text/plain")
    else:
        req = drive_svc.files().get_media(fileId=file_id)
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)
    return buf


def extract_text(buf: io.BytesIO, mime: str, name: str) -> str:
    if mime == "application/vnd.google-apps.document":
        return buf.read().decode("utf-8", errors="ignore")
    if mime == "application/vnd.google-apps.presentation":
        return buf.read().decode("utf-8", errors="ignore")
    if mime == "application/pdf":
        try:
            with pdfplumber.open(buf) as pdf:
                return "\n\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception as e:
            return f"[PDF extraction failed: {e}]"
    if mime in ("text/plain", "text/markdown"):
        return buf.read().decode("utf-8", errors="ignore")
    if "word" in mime or "document" in mime:
        try:
            import docx
            doc = docx.Document(buf)
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            return buf.read().decode("utf-8", errors="ignore")
    return buf.read().decode("utf-8", errors="ignore")


# ── Walk the folder tree ───────────────────────────────────────────────────────

def walk_folder(folder_id: str, folder_path: list, dry_run: bool,
                docs_done: list, vids_done: list, skipped: list):
    from database import (library_already_processed, library_video_exists,
                          store_library_chunks, store_library_video, init_db)

    items = list_folder(folder_id)

    for item in items:
        name  = item["name"]
        fid   = item["id"]
        mime  = item["mimeType"]
        size  = int(item.get("size", 0) or 0)
        link  = item.get("webViewLink", f"https://drive.google.com/file/d/{fid}/view")
        size_mb = round(size / (1024 * 1024), 1)

        # Recurse into subfolders
        if mime == "application/vnd.google-apps.folder":
            walk_folder(fid, folder_path + [name], dry_run,
                        docs_done, vids_done, skipped)
            continue

        if should_skip_name(name):
            skipped.append(f"[NAME_SKIP] {name}")
            continue

        if mime in SKIP_MIMES:
            skipped.append(f"[MIME_SKIP] {name} ({mime})")
            continue

        category, subcategory = categorize(name, folder_path)

        # ── Videos ──
        if is_video(mime):
            embed_url = f"https://drive.google.com/file/d/{fid}/preview"
            if library_video_exists(fid):
                skipped.append(f"[ALREADY] {name}")
                continue
            print(f"  🎬 [{category}] {name} ({size_mb} MB)")
            if not dry_run:
                store_library_video(name, fid, category, subcategory,
                                    embed_url, size_mb, mime)
            vids_done.append(name)
            continue

        # ── Documents ──
        if library_already_processed(fid):
            skipped.append(f"[ALREADY] {name}")
            continue

        # Only process supported text/doc/pdf types
        supported = (
            is_document(mime) or
            mime == "application/pdf" or
            mime == "text/plain"
        )
        if not supported:
            skipped.append(f"[UNSUPPORTED] {name} ({mime})")
            continue

        print(f"  📄 [{category}] {name} ({size_mb} MB)")
        if not dry_run:
            try:
                buf  = download_file(fid, mime)
                text = extract_text(buf, mime, name)
                if len(text.strip()) < 50:
                    skipped.append(f"[EMPTY] {name}")
                    continue
                header = f"Document: {name}\nCategory: {category}\n\n"
                n = store_library_chunks(name, fid, header + text[:30000],
                                         mime.split("/")[-1], category)
                docs_done.append(name)
                print(f"      ✅ {n} chunk(s)")
                time.sleep(0.5)
            except Exception as e:
                print(f"      ❌ Error: {e}")
        else:
            docs_done.append(name)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ingest Library Drive folder")
    parser.add_argument("--run", action="store_true", help="Actually ingest (default: dry run)")
    args = parser.parse_args()

    from database import init_db
    init_db()

    print("\n📚 Library Ingestion" + (" (DRY RUN)" if not args.run else "") + "\n")

    docs_done, vids_done, skipped = [], [], []
    walk_folder(LIBRARY_FOLDER_ID, [], not args.run, docs_done, vids_done, skipped)

    print(f"\n{'='*55}")
    print(f"📄 Documents: {len(docs_done)}")
    print(f"🎬 Videos:    {len(vids_done)}")
    print(f"⏭  Skipped:   {len(skipped)}")

    if not args.run:
        print("\n💡 Dry run complete. Run with --run to actually ingest.")


if __name__ == "__main__":
    main()
