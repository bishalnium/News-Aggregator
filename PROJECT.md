# PROJECT

## Goal
Build a low-latency news aggregator engine first, then use it as the trusted input layer for downstream trading/automation agents.

## Final Scope (Prompt Evolution Applied)
- Source ingestion: 1 Telegram channel + 2 Twitter accounts.
- No DuckDuckGo fallback.
- Real-time news stream in frontend.
- Rolling digest summaries selectable as 30s, 60s, or 120s.
- Frontend controls must update backend behavior immediately.
- Separate AI chat interface for database-backed Q&A.
- Separate alerts system using keyword/NLP matching (non-LLM trigger path).
- Two Telegram outputs:
  - Summary bot output.
  - Alert bot output.

## Primary Outcome
A reliable and fast aggregation core that can be reused by other agents as a trusted market-news signal source.
