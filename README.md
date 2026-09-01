# Hi, Amy! 🌟

> An internal course assistant built on Amy Porterfield's training materials.
> Team members chat with Amy to get answers, task instructions, and insights — all drawn from the course content.

---

## Setup

### 1. Clone & install
```bash
cd hi-amy
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 2. Add credentials
- Rename `.env.example` to `.env`
- Fill in your `GEMINI_API_KEY`
- Place your service account JSON file in this folder and name it `hi-amy-service-account.json`

### 3. Enable Cloud Firestore (one-time)
- Go to [console.cloud.google.com](https://console.cloud.google.com)
- Select project `hi-amy-assistant`
- **APIs & Services → Library → search "Cloud Firestore API" → Enable**
- Then: **Firestore → Create Database → Native mode → us-central1**

### 4. Ingest course content (one-time, or when course updates)
```bash
python ingest.py
```
This reads all files from your Google Drive folder and stores them in Firestore.
Videos and images are processed by Gemini to extract the content inside them.
⏱ Takes a few minutes depending on how many files you have.

### 5. Run locally
```bash
uvicorn main:app --reload
```
Open [http://localhost:8000](http://localhost:8000)

---

## Deploy to Render

1. Push this folder to a **GitHub repository** (private is fine)
   - ⚠️ Make sure `.gitignore` excludes your `.env` and `*.json` files
2. Go to [render.com](https://render.com) → New → Web Service → connect your repo
3. Add environment variables in Render dashboard:
   - `GEMINI_API_KEY` → your key
   - `GOOGLE_CLOUD_PROJECT` → `hi-amy-assistant-507305`
   - `DRIVE_FOLDER_ID` → `1uRY6rOo2_M9YKxRuIVLMCZ0gt12_fPJn`
4. Upload your `hi-amy-service-account.json` as a Secret File in Render
   - Render Dashboard → your service → Environment → Secret Files
   - Filename: `hi-amy-service-account.json`
5. Deploy!

---

## How it works

1. **Ingest**: `ingest.py` reads PDFs, Word docs, slides, images, and videos from Google Drive.
   Gemini processes every file — including extracting content from inside videos and images.
   Everything is stored as text chunks in Firestore.

2. **Chat**: When a team member asks a question, the app searches Firestore for relevant course chunks,
   feeds them to Gemini along with Amy's persona, and returns a warm, practical answer.

3. **Activation**: Team members must type **"Hi, Amy!"** to start the conversation.

---

## Updating course content

If new files are added to the Drive folder, just re-run:
```bash
python ingest.py
```
Already-processed files are skipped automatically.
