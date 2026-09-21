"""
transcribe_library.py
Transcribes all videos in library_videos using Groq Whisper and stores
transcripts in library_chunks so Hi Amy can answer questions from them.

Usage:
    python transcribe_library.py --dry-run          # preview without transcribing
    python transcribe_library.py                    # transcribe everything not yet done
    python transcribe_library.py --category "AI Advantage"   # one category only
    python transcribe_library.py --file-id 1abc...  # one specific video

Requirements:
    ffmpeg installed (winget install ffmpeg)
    GROQ_API_KEY and GOOGLE_SERVICE_ACCOUNT_FILE set in .env
"""

import os
import sys
import io
import json
import subprocess
import tempfile
import argparse
import time

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

load_dotenv()

SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "hi-amy-service-account.json")
SCOPES               = ["https://www.googleapis.com/auth/drive.readonly"]
CHUNK_SECONDS        = 900    # 15-min audio chunks → ~7 MB each at 64 kbps
CHUNK_BITRATE        = "64k"  # mono speech quality is fine


# ── Drive client ──────────────────────────────────────────────────────────────

def get_drive():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


# ── ffmpeg helpers ────────────────────────────────────────────────────────────

def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_duration(filepath):
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    info = json.loads(result.stdout.decode("utf-8", errors="replace"))
    return float(info["format"]["duration"])


# ── Download from Drive ───────────────────────────────────────────────────────

def download_video(drive, file_id: str, dest_path: str):
    """Stream a Drive file to dest_path. Returns file size in MB."""
    request = drive.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=20 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            pct = int(status.progress() * 100)
            print(f"   ⬇  Downloading... {pct}%", end="\r", flush=True)
    size_mb = os.path.getsize(dest_path) / (1024 * 1024)
    print(f"   ⬇  Downloaded  {size_mb:.0f} MB          ")
    return size_mb


# ── Transcribe ────────────────────────────────────────────────────────────────

def transcribe_video(filepath: str, name: str) -> str:
    """Split into chunks, transcribe each with Groq Whisper, return full text."""
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    duration = get_duration(filepath)
    n_chunks = max(1, int(duration / CHUNK_SECONDS) + 1)
    print(f"   ⏱  {duration/60:.0f} min  →  {n_chunks} chunk(s)")

    transcripts = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(n_chunks):
            start = i * CHUNK_SECONDS
            if start >= duration:
                break

            chunk_path = os.path.join(tmpdir, f"chunk_{i:03d}.mp3")
            subprocess.run([
                "ffmpeg", "-y", "-i", filepath,
                "-ss", str(int(start)), "-t", str(CHUNK_SECONDS),
                "-vn", "-acodec", "mp3", "-ab", CHUNK_BITRATE, "-ac", "1",
                chunk_path
            ], capture_output=True)

            if not os.path.exists(chunk_path) or os.path.getsize(chunk_path) < 500:
                print(f"   ⚠  Chunk {i+1} empty — skipping")
                continue

            chunk_mb = os.path.getsize(chunk_path) / (1024 * 1024)
            print(f"   🎙  Chunk {i+1}/{n_chunks} ({chunk_mb:.1f} MB) → transcribing...", end="", flush=True)

            for attempt in range(4):
                try:
                    with open(chunk_path, "rb") as f:
                        result = client.audio.transcriptions.create(
                            file=(f"chunk_{i:03d}.mp3", f),
                            model="whisper-large-v3-turbo",
                            response_format="text",
                        )
                    transcripts.append(result.strip())
                    print(f" ✅  {len(result.split())} words")
                    break
                except Exception as e:
                    if "429" in str(e) or "rate" in str(e).lower():
                        wait = 30 * (attempt + 1)
                        print(f" ⏳  rate limit — waiting {wait}s...", end="", flush=True)
                        time.sleep(wait)
                    else:
                        print(f" ❌  {e}")
                        break

    return "\n\n".join(transcripts)


# ── Store in DB ───────────────────────────────────────────────────────────────

def store_transcript(file_id: str, file_name: str, category: str,
                     subcategory: str, transcript: str):
    from database import library_already_processed, store_library_chunks

    # Use a namespaced ID so it doesn't collide with the video row
    transcript_id = f"transcript_{file_id}"

    if library_already_processed(transcript_id):
        print(f"   ⏭  Already transcribed — skipping storage")
        return False

    header = f"Video Transcript: {file_name}\nCategory: {category}\n\n"
    n = store_library_chunks(
        file_name, transcript_id,
        header + transcript,
        "transcript", category, subcategory
    )
    print(f"   💾  Stored as {n} chunk(s) in library_chunks")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",    action="store_true", help="Preview without transcribing")
    parser.add_argument("--category",   default=None, help="Only process this category")
    parser.add_argument("--subcategory", default=None, help="Only process this subcategory (requires --category)")
    parser.add_argument("--file-id",    default=None, help="Only process this specific file_id")
    args = parser.parse_args()

    if not check_ffmpeg():
        print("❌  ffmpeg not found.\n   Install with:  winget install ffmpeg\n   Then reopen this terminal.")
        sys.exit(1)

    if not os.getenv("GROQ_API_KEY"):
        print("❌  GROQ_API_KEY not set in .env")
        sys.exit(1)

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"❌  Service account file not found: {SERVICE_ACCOUNT_FILE}")
        sys.exit(1)

    from database import init_db, get_library_videos, library_already_processed
    init_db()

    drive   = get_drive()
    videos  = get_library_videos(category=args.category)

    # Filter by subcategory if requested
    if args.subcategory:
        videos = [v for v in videos if (v.get("subcategory") or "").lower() == args.subcategory.lower()]

    # Filter to one file if requested
    if args.file_id:
        videos = [v for v in videos if v["file_id"] == args.file_id]

    # Skip already-transcribed
    pending = [v for v in videos if not library_already_processed(f"transcript_{v['file_id']}")]
    already = len(videos) - len(pending)

    print(f"\n🎬  Library Video Transcription {'(DRY RUN) ' if args.dry_run else ''}")
    print(f"   {len(videos)} total  |  {already} already done  |  {len(pending)} to process\n")

    if not pending:
        print("✅  Nothing left to transcribe.")
        return

    done = skipped = failed = 0

    for v in pending:
        name       = v["file_name"]
        file_id    = v["file_id"]
        category   = v["category"]
        subcategory = v.get("subcategory")
        size_mb    = v.get("size_mb", 0)

        print(f"\n{'='*60}")
        print(f"📼  {name}")
        print(f"    {category}{' / ' + subcategory if subcategory else ''}  |  {size_mb:.0f} MB")

        if args.dry_run:
            print(f"    [DRY RUN] Would download and transcribe")
            continue

        with tempfile.TemporaryDirectory() as tmpdir:
            ext       = os.path.splitext(name)[1] or ".mp4"
            local     = os.path.join(tmpdir, f"video{ext}")

            # Download
            try:
                download_video(drive, file_id, local)
            except Exception as e:
                print(f"   ❌  Download failed: {e}")
                failed += 1
                continue

            # Transcribe
            try:
                transcript = transcribe_video(local, name)
            except Exception as e:
                print(f"   ❌  Transcription failed: {e}")
                failed += 1
                continue

        if not transcript.strip():
            print(f"   ⚠  Empty transcript — skipping storage")
            skipped += 1
            continue

        # Store
        stored = store_transcript(file_id, name, category, subcategory, transcript)
        if stored:
            done += 1
        else:
            skipped += 1

    print(f"\n{'='*60}")
    print(f"✅  Done  |  {done} transcribed  |  {skipped} skipped  |  {failed} failed")


if __name__ == "__main__":
    main()
