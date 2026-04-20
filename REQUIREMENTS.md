# REQUIREMENTS

## Functional
- Ingest Telegram messages instantly (event-driven) with Telethon user session.
- Poll Twitter handles at fixed interval (default 15 seconds).
- Deduplicate, classify, and store every incoming item in MySQL.
- Show live feed in frontend and push new items via WebSocket.
- Generate rolling summaries and store each summary batch.
- Allow summary interval change from UI (30s, 60s, 120s) with persistent backend setting.
- Provide AI Q&A over historical stored news.
- Provide alerts section with topic/keyword management.
- Trigger Telegram alerts when keyword matches and urgency threshold is met.

## Non-Functional
- Low latency ingest and push to UI.
- Stable asynchronous backend architecture.
- Deployable on always-on server (Oracle VM or similar).
- DB-backed memory for historical querying.
