"""
Hi, Amy! - Course Content Ingestion Script
Run this once to process all files from Google Drive into PostgreSQL.
Run again anytime the course content is updated.

AI backend: Groq (llama-3.3-70b-versatile + whisper-large-v3)
NOTE: Video transcription requires FFmpeg installed on this machine.
  Windows: winget install ffmpeg
  Mac:     brew install ffmpeg
"""

import os
import io
import time
import base64
import tempfile
import subprocess
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from groq import Groq
import PyPDF2
from docx import Document
from bs4 import BeautifulSoup
from database import init_db, already_processed, store_chunks

load_dotenv()

# â”€â”€ Config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
DRIVE_FOLDER_ID      = os.getenv("DRIVE_FOLDER_ID")
GROQ_API_KEY         = os.getenv("GROQ_API_KEY")

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# â”€â”€ Init â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
creds       = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_svc   = build("drive", "v3", credentials=creds)
groq_client = Groq(api_key=GROQ_API_KEY)

ACTIVE_MODEL = "qwen/qwen3.8-27b"         # text generation
VISION_MODEL = "groq/compound"                              # image description (vision)
WHISPER_MODEL = "whisper-large-v3"          # audio/video transcription

GROQ_WHISPER_LIMIT = 24 * 1024 * 1024  # 24 MB (Groq's limit is 25 MB)

print(f"ðŸ¤– Using Groq models: {ACTIVE_MODEL} | {WHISPER_MODEL}")


# â”€â”€ Check FFmpeg â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _ffmpeg_available():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

FFMPEG_OK = _ffmpeg_available()
if not FFMPEG_OK:
    print("âš ï¸  FFmpeg not found â€” large video files will be skipped.")
    print("   Install: winget install ffmpeg  (then restart terminal)")


# â”€â”€ AI Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def generate(prompt, system=None):
    """Call Groq LLM with auto-retry on rate limits."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for attempt in range(5):
        try:
            resp = groq_client.chat.completions.create(
                model=ACTIVE_MODEL,
                messages=messages,
                max_tokens=4000,
                temperature=0.3,
            )
            return resp.choices[0].message.content
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower() or "rate limit" in err.lower():
                wait = 30 * (attempt + 1)
                print(f"   â³ Rate limit â€” waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Groq rate limit exceeded after 5 retries")


def ai_summarize(text, file_name):
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

Be thorough â€” this will be used to answer team questions about the course."""
    return generate(prompt)


def ai_describe_image(img_bytes, file_name):
    """Describe an image using Groq vision model."""
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    prompt = f"""This is a slide or image from Amy Porterfield's course: {file_name}

Describe in full detail:
1. All text visible in the image
2. Any diagrams, frameworks, or models shown
3. Key takeaways from this visual
4. Any action steps or strategies depicted"""

    for attempt in range(5):
        try:
            resp = groq_client.chat.completions.create(
                model=VISION_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"}
                        },
                        {"type": "text", "text": prompt}
                    ]
                }],
                max_tokens=2000,
            )
            return resp.choices[0].message.content
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                wait = 30 * (attempt + 1)
                print(f"   â³ Rate limit â€” waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Groq rate limit exceeded after 5 retries")


def _transcribe_audio_file(audio_path, label):
    """Send an audio file (â‰¤24MB) to Groq Whisper and return text."""
    with open(audio_path, "rb") as f:
        resp = groq_client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=(os.path.basename(audio_path), f),
            response_format="text",
        )
    return resp if isinstance(resp, str) else resp.text


def _extract_audio(video_path):
    """Extract audio from video to a temp MP3 file. Returns path."""
    audio_path = video_path + "_audio.mp3"
    subprocess.run(
        [
            "ffmpeg", "-i", video_path,
            "-vn",                  # no video
            "-acodec", "mp3",
            "-ab", "64k",           # 64kbps â€” good for speech, small file
            "-ac", "1",             # mono
            "-y",                   # overwrite if exists
            audio_path,
        ],
        capture_output=True,
        timeout=600,
        check=True,
    )
    return audio_path


def _transcribe_in_chunks(audio_path, file_name):
    """Split large audio into 10-min chunks and transcribe each."""
    chunk_duration = 600  # seconds
    transcripts = []
    offset = 0
    chunk_num = 0

    while True:
        chunk_path = audio_path + f".chunk{chunk_num}.mp3"
        result = subprocess.run(
            [
                "ffmpeg", "-i", audio_path,
                "-ss", str(offset), "-t", str(chunk_duration),
                "-acodec", "mp3", "-ab", "64k", "-ac", "1",
                "-y", chunk_path,
            ],
            capture_output=True,
            timeout=120,
        )
        # If output file is tiny or missing, we've hit the end
        if not os.path.exists(chunk_path) or os.path.getsize(chunk_path) < 2000:
            if os.path.exists(chunk_path):
                os.unlink(chunk_path)
            break

        try:
            print(f"      ðŸ“ Transcribing chunk {chunk_num + 1}...")
            t = _transcribe_audio_file(chunk_path, f"chunk_{chunk_num}.mp3")
            transcripts.append(t)
        finally:
            if os.path.exists(chunk_path):
                os.unlink(chunk_path)

        offset += chunk_duration
        chunk_num += 1
        time.sleep(1)  # be polite to the API

    return "\n\n".join(transcripts)


def ai_transcribe_video(video_path, file_name):
    """Transcribe a video using Groq Whisper. Extracts audio first."""
    if not FFMPEG_OK:
        raise RuntimeError(
            "FFmpeg is required for video transcription. "
            "Install with: winget install ffmpeg"
        )

    print(f"    ðŸŽµ Extracting audio from video...")
    audio_path = None
    try:
        audio_path = _extract_audio(video_path)
        audio_size = os.path.getsize(audio_path)
        print(f"    ðŸŽµ Audio extracted: {audio_size / (1024*1024):.1f} MB")

        if audio_size <= GROQ_WHISPER_LIMIT:
            # Small enough â€” send directly
            raw_transcript = _transcribe_audio_file(audio_path, file_name + ".mp3")
        else:
            # Too large â€” split into chunks
            print(f"    âœ‚ï¸  Audio too large for single request â€” splitting into chunks...")
            raw_transcript = _transcribe_in_chunks(audio_path, file_name)

    finally:
        if audio_path and os.path.exists(audio_path):
            os.unlink(audio_path)

    # Summarize the raw transcript into structured course notes
    print(f"    ðŸ“‹ Summarizing transcript...")
    return generate(
        f"""This is a raw transcript from Amy Porterfield's training video: {file_name}

Transcript:
{raw_transcript[:15000]}

Provide a comprehensive summary including:
1. All instructions and how-to steps explained
2. Key concepts and frameworks discussed
3. Specific strategies and techniques taught
4. Action items the viewer should take
5. Any tools, platforms, or resources mentioned
6. Important quotes and insights"""
    )


# Legacy aliases so reingest_failed.py continues to work unchanged
gemini_summarize       = ai_summarize
gemini_describe_image  = ai_describe_image
gemini_transcribe_video = ai_transcribe_video


# â”€â”€ Drive Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    dl = MediaIoBaseDownload(buf, req)
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


# â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def ingest():
    print(f"\nðŸŽ“ Hi, Amy! â€” Course Ingestion")
    print(f"ðŸ“‚ Drive folder: {DRIVE_FOLDER_ID}\n")

    init_db()

    files = list_drive_files(DRIVE_FOLDER_ID)
    print(f"Found {len(files)} file(s)\n")

    for f in files:
        name = f["name"]
        fid  = f["id"]
        mime = f["mimeType"]

        print(f"ðŸ“„ {name}")
        print(f"   Type: {mime}")

        if already_processed(fid):
            print("   â­  Already processed, skipping\n")
            continue

        try:
            if mime == "application/pdf":
                buf  = download_file(fid, mime)
                text = extract_text_pdf(buf)
                processed = ai_summarize(text, name)
                n = store_chunks(name, fid, processed, "pdf")

            elif mime in (
                "application/vnd.google-apps.document",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ):
                buf = download_file(fid, mime)
                text = (
                    buf.read().decode("utf-8", errors="ignore")
                    if mime == "application/vnd.google-apps.document"
                    else extract_text_docx(buf)
                )
                processed = ai_summarize(text, name)
                n = store_chunks(name, fid, processed, "document")

            elif mime in (
                "application/vnd.google-apps.presentation",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ):
                buf  = download_file(fid, mime)
                text = buf.read().decode("utf-8", errors="ignore")
                processed = ai_summarize(text, name)
                n = store_chunks(name, fid, processed, "slides")

            elif mime.startswith("image/"):
                buf = download_file(fid, mime)
                img_bytes = buf.read()
                processed = ai_describe_image(img_bytes, name)
                n = store_chunks(name, fid, processed, "image")

            elif mime.startswith("video/"):
                meta = drive_svc.files().get(fileId=fid, fields="size,webViewLink").execute()
                size_mb = int(meta.get("size", 0)) / (1024 * 1024)
                link = meta.get("webViewLink", "")
                print(f"   ðŸ“¹ Size: {size_mb:.0f} MB â€” downloading & transcribing...")
                tmp_path = None
                try:
                    buf = download_file(fid, mime)
                    ext = name.split(".")[-1] if "." in name else "mp4"
                    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                        tmp.write(buf.read())
                        tmp_path = tmp.name
                    processed = ai_transcribe_video(tmp_path, name)
                    n = store_chunks(name, fid, processed, "video")
                except Exception as vid_err:
                    print(f"   âš ï¸  Transcription failed ({vid_err}) â€” storing as reference")
                    summary = (
                        f"Video lesson: {name}\n"
                        f"Size: {size_mb:.0f} MB\n"
                        f"Link: {link}\n"
                        f"Topic (from filename): {name.replace('_', ' ').replace('.mp4', '').replace(' _ Digital Course Academy', '')}\n"
                        f"This video could not be auto-transcribed. Team members can watch it directly in Google Drive."
                    )
                    n = store_chunks(name, fid, summary, "video_reference")
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.unlink(tmp_path)

            elif mime == "text/html":
                buf  = download_file(fid, mime)
                html = buf.read().decode("utf-8", errors="ignore")
                text = BeautifulSoup(html, "html.parser").get_text(separator="\n")
                processed = ai_summarize(text, name)
                n = store_chunks(name, fid, processed, "html")

            else:
                print("   âš ï¸  Unsupported type, skipping\n")
                continue

            print(f"   âœ… Stored {n} chunk(s)\n")

        except Exception as e:
            print(f"   âŒ Error: {e}\n")

        time.sleep(1)

    print("ðŸŽ‰ Ingestion complete!")


if __name__ == "__main__":
    ingest()
