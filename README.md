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
- AI classification and digest generation via configurable Groq, Gemini, or Cerebras provider order
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
- LLM calls are provider-order driven. Set `LLM_PROVIDER_ORDER`, `SUMMARY_PROVIDER_ORDER`, and `CLASSIFICATION_PROVIDER_ORDER` to prioritize Groq/Gemini/Cerebras without code changes.
- Groq defaults to `openai/gpt-oss-120b`. Set `GROQ_API_KEYS` as a comma-separated list; keys are used sequentially and cooled down/rotated when invalid or quota-exhausted.
- `FAST_SUMMARY_MODE=false` enables model-generated summaries; leave it true only when you want cheap local fallback summaries.
- Near-duplicate Telegram/news repeats are filtered by `NEWS_DEDUPE_WINDOW_SECONDS` and `NEWS_DEDUPE_SIMILARITY`.
- AI chat supports selectable models through `CHAT_GROQ_MODEL` and `CEREBRAS_CHAT_MODEL`.
- Two bot channels supported:
  - ALERT_BOT_TOKEN + ALERT_CHAT_ID
  - SUMMARY_BOT_TOKEN + SUMMARY_CHAT_ID

## Oracle Cloud Deployment (OCI VM)

This repository now includes production deployment assets for Oracle Cloud VMs:
- `backend/Dockerfile`
- `docker-compose.oracle.yml`
- `.env.oracle.example`
- `deploy/oracle/setup-vm.sh`
- `deploy/oracle/deploy.sh`

### 1) Create Oracle VM

- Shape: Ampere A1 or E2 Micro (Always Free eligible)
- OS: Ubuntu 22.04/24.04
- Open ingress rules in OCI Security List or NSG:
  - TCP 22 (SSH)
  - TCP 8000 (backend API)
- Do not expose MySQL publicly; the Oracle compose file keeps MySQL on the internal Docker network.

### 2) Connect and clone

```bash
ssh -i <your_key>.pem ubuntu@<your_public_ip>
git clone https://github.com/GangserX/News-Aggregator.git
cd News-Aggregator
```

### 3) Prepare VM runtime

```bash
chmod +x deploy/oracle/setup-vm.sh deploy/oracle/deploy.sh
./deploy/oracle/setup-vm.sh
newgrp docker
```

### 4) Configure environment files

```bash
cp .env.oracle.example .env.oracle
cp backend/.env.example backend/.env
```

Edit `.env.oracle`:
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_ROOT_PASSWORD`
- `BACKEND_PORT`

Edit `backend/.env`:
- Telegram credentials and channels
- Twitter credentials/cookies
- LLM keys (Gemini/Groq/Cerebras)
- Keep `DATABASE_URL` as-is; in Oracle compose it is overridden to internal MySQL service DNS.

If any key contains `$`, escape it as `$$` in env files used by Docker Compose to avoid interpolation warnings.

### 5) Deploy services

```bash
./deploy/oracle/deploy.sh
```

### 6) Verify health

```bash
curl http://127.0.0.1:8000/health
curl http://<your_public_ip>:8000/health
```

### 7) Update frontend API URL

If frontend is on Vercel, set:
- `VITE_API_URL=http://<your_public_ip>:8000`

For production-grade HTTPS, place Nginx/Caddy in front of port 8000 and expose only 80/443.

## Final Scope Mapping

- Prompt 1 foundation: ingestion + AI + alerts + dashboard.
- Prompt 2 change: removed DuckDuckGo and kept only Telegram + two X sources.
- Prompt 3 change: selectable digest window, tight FE-BE sync, separate AI chat and separate alerts workflow.

This codebase implements that final version.
