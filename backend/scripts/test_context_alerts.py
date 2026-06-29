from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import init_pool, close_pool, get_pool
from processing.llm_classifier import potentials_context_alert_match, verify_context_alert_match


async def run_test():
    print("--- Context Alerts Diagnostic Tool ---")
    await init_pool()

    pool = get_pool()
    async with pool.acquire() as conn:
        active_alerts = await conn.fetch(
            "SELECT id, context_description FROM context_alerts WHERE active = true"
        )
        active_alerts = [dict(row) for row in active_alerts]

    print(f"Loaded {len(active_alerts)} active context alerts from DB:")
    for alert in active_alerts:
        print(f"  [{alert['id']}] {alert['context_description'][:100]}...")

    # Mock Federal Reserve News
    news_text = (
        "BREAKING: The Federal Reserve announced a reduction in the benchmark interest rate by 50 basis points. "
        "The decision was confirmed at the conclusion of the FOMC meeting today, in line with expectations."
    )
    print(f"\nIncoming Mock News:\n\"{news_text}\"")

    print("\n[1/2] Running potentials_context_alert_match (Candidate filtering)...")
    try:
        candidate_ids = await potentials_context_alert_match(news_text, active_alerts)
        print(f"Result candidate IDs: {candidate_ids}")
    except Exception as e:
        print(f"Candidate filtering failed: {e}")
        candidate_ids = []

    if candidate_ids:
        print("\n[2/2] Running verify_context_alert_match (Strict verification) for matched candidates...")
        alert_map = {alert["id"]: alert for alert in active_alerts}
        for alert_id in candidate_ids:
            alert = alert_map.get(alert_id)
            if alert:
                desc = alert["context_description"]
                print(f"Verifying against Alert [{alert_id}]: \"{desc[:100]}...\"")
                try:
                    is_match = await verify_context_alert_match(news_text, desc)
                    print(f"  Match verification result: {is_match}")
                except Exception as e:
                    print(f"  Verification failed: {e}")
    else:
        print("\nNo candidates matched. Skipping verification.")

    await close_pool()

if __name__ == "__main__":
    asyncio.run(run_test())
