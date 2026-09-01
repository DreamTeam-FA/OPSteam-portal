"""List all video files in the Drive folder and export to CSV."""
import os, csv
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
DRIVE_FOLDER_ID      = os.getenv("DRIVE_FOLDER_ID")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

creds     = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_svc = build("drive", "v3", credentials=creds)

def list_drive_files(folder_id):
    files, page_token = [], None
    while True:
        resp = drive_svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, size, webViewLink, parents)",
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

print("🔍 Scanning Drive folder...")
all_files = list_drive_files(DRIVE_FOLDER_ID)
videos = [f for f in all_files if f.get("mimeType", "").startswith("video/")]

print(f"✅ Found {len(videos)} video(s)\n")

rows = []
for i, v in enumerate(videos, 1):
    size_mb = int(v.get("size", 0)) / (1024 * 1024)
    rows.append({
        "#": i,
        "Video Title": v["name"],
        "Size (MB)": f"{size_mb:.1f}",
        "Drive Link": v.get("webViewLink", ""),
        "Transcript Downloaded?": "",
        "Transcript Filename": "",
        "Notes": "",
    })
    print(f"{i:3}. {v['name']} ({size_mb:.0f} MB)")

# Write CSV
csv_path = "video_transcripts_checklist.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print(f"\n✅ Checklist saved to: {csv_path}")
