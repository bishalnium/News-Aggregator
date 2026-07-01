import asyncio
import os
import sys

sys.path.append("/app")

import database
from database import get_pool

async def main():
    await database.init_pool()
    pool = get_pool()
    
    async with pool.acquire() as conn:
        print("Checking news inserted today by backfill (fetched_at >= '2026-06-29 05:00:00'):")
        rows = await conn.fetch(
            "SELECT DATE(published_at) as pub_date, COUNT(*) as cnt FROM news WHERE fetched_at >= '2026-06-29 05:00:00' GROUP BY DATE(published_at) ORDER BY pub_date DESC"
        )
        for row in rows:
            print(f"  Published Date: {row['pub_date']} | Count: {row['cnt']}")

        print("\nChecking news matching June 25, 26, 27:")
        for date_str in ['2026-06-25', '2026-06-26', '2026-06-27']:
            r = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM news WHERE published_at LIKE $1",
                f"{date_str}%"
            )
            print(f"  Date: {date_str} | Count: {r['cnt'] if r else 0}")

if __name__ == "__main__":
    asyncio.run(main())
