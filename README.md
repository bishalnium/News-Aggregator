# News Codex Aggregator

A full-stack, low-latency market news aggregator built from scratch for your final plan:
- Telegram channel ingest (event-driven)
- Two Twitter/X account tracking
- Real-time frontend feed
- Rolling digest summaries (30s / 60s / 120s)
- Separate AI chat (database-backed)
- Separate NLP alerts to Telegram bot

## Architecture

### Backend
- FastAPI + aiomysql + MySQL
- Telethon listener for Telegram channels
- Twikit poller for Twitter/X handles
- AI classification and digest generation via Groq or Cerebras-compatible endpoint
- NLP keyword alert matching (regex word-boundary style)
- WebSocket push for real-time frontend updates

### Frontend
- React + Vite
- Pages:
  - Live Feed and Digest
  - Alert Setup
  - Alert History
  - AI Chat

## Folder Layout

- backend
  - main.py
  - config.py
  - database.py
  - models.py
  - ingestion/
  - processing/
  - api/
  - bot/
- frontend
  - src/

## Quick Start (Local)

## 1) Start MySQL

Option A: docker-compose

```bash
docker compose up -d
```

Option B: your own local/remote MySQL (including Oracle MySQL HeatWave) and set DATABASE_URL in backend/.env.

## 2) Backend Setup

```bash
cd backend
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
copy .env.example .env
```

Fill backend/.env with your real credentials.

Run backend:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## 3) Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

## Environment Notes

- Telegram ingest requires TELEGRAM_API_ID and TELEGRAM_API_HASH from my.telegram.org.
- Telethon uses your account session. First run may prompt OTP in terminal.
- Twitter poller uses Twikit cookie auth. First run creates cookie file.
- NLP/classification can use Groq by setting GROQ_API_KEYS as a comma-separated list.
  Keys are used sequentially, and the backend rotates to the next key only when
  the active key is invalid or quota-exhausted.
- Two bot channels supported:
  - ALERT_BOT_TOKEN + ALERT_CHAT_ID
  - SUMMARY_BOT_TOKEN + SUMMARY_CHAT_ID

## Deployment Notes

### Backend (Always-On)
Deploy backend on Oracle VM so ingest keeps running 24/7.

### Frontend
Deploy frontend on Vercel and set VITE_API_URL to backend public URL.

### Free Database Options
- Oracle MySQL HeatWave Always Free
- Self-hosted MySQL on Oracle VM
- Any MySQL-compatible managed service

Use one always-on database for historical Q&A continuity.

## Final Scope Mapping

- Prompt 1 foundation: ingestion + AI + alerts + dashboard.
- Prompt 2 change: removed DuckDuckGo and kept only Telegram + two X sources.
- Prompt 3 change: selectable digest window, tight FE-BE sync, separate AI chat and separate alerts workflow.

This codebase implements that final version.
