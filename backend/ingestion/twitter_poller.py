from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

import twikit

from config import settings


NewsHandler = Callable[[dict[str, Any]], Awaitable[None]]


class TwitterPoller:
    def __init__(self, on_news: NewsHandler) -> None:
        self._on_news = on_news
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=5000)
        self._apply_twikit_transaction_workaround()
        self._client = twikit.Client("en-US")

    @staticmethod
    def _apply_twikit_transaction_workaround() -> None:
        """Use a fallback when Twikit fails to extract KEY_BYTE indices.

        Twikit occasionally fails to parse dynamic JS used for transaction
        headers and raises "Couldn't get KEY_BYTE indices". This fallback keeps
        the login flow moving by using last-known indices.
        """

        try:
            from twikit.x_client_transaction.transaction import ClientTransaction
        except Exception:
            return

        if getattr(ClientTransaction, "_newscodex_indices_patch", False):
            return

        original_get_indices = ClientTransaction.get_indices

        async def patched_get_indices(transaction, home_page_response, session, headers):
            try:
                return await original_get_indices(
                    transaction, home_page_response, session, headers
                )
            except Exception as exc:
                if "KEY_BYTE indices" not in str(exc):
                    raise
                print(
                    "Twikit workaround active: using fallback transaction "
                    "indices because KEY_BYTE extraction failed"
                )
                return 2, [2, 42, 45]

        ClientTransaction.get_indices = patched_get_indices
        ClientTransaction._newscodex_indices_patch = True

    async def run(self) -> None:
        handles = [
            self._normalize_handle(handle) for handle in settings.twitter_handles
        ]
        handles = [handle for handle in handles if handle]

        if not handles:
            print("Twitter poller disabled: TWITTER_HANDLES is empty")
            return

        cookies_path = Path(settings.twitter_cookies_file)
        has_credentials = bool(settings.twitter_username and settings.twitter_password)
        has_cookies_file = cookies_path.exists()

        if not has_credentials and not has_cookies_file:
            print(
                "Twitter poller disabled: username/password missing and "
                f"cookies file not found at {cookies_path}"
            )
            return

        try:
            await self._ensure_auth()
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            if len(message) > 300:
                message = message[:297] + "..."
            print(f"Twitter poller authentication failed: {message}")
            print(
                "Twitter poller is inactive. Provide valid credentials or a "
                "working TWITTER_COOKIES_FILE."
            )
            return

        while True:
            try:
                for handle in handles:
                    await self._poll_handle(handle)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"Twitter poll loop error: {exc}")

            await asyncio.sleep(max(5, settings.twitter_poll_seconds))

    async def _ensure_auth(self) -> None:
        cookies_path = Path(settings.twitter_cookies_file)

        try:
            await self._client.load_cookies(str(cookies_path))
            print(f"Twitter cookies loaded from: {cookies_path}")
            return
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"Could not load twitter cookies, attempting fresh login: {exc}")

        if not settings.twitter_username or not settings.twitter_password:
            raise RuntimeError(
                "Twitter credentials are missing and no valid cookies file is available"
            )

        login_kwargs = {
            "auth_info_1": settings.twitter_username,
            "password": settings.twitter_password,
        }
        if settings.twitter_email:
            login_kwargs["auth_info_2"] = settings.twitter_email
        if settings.twitter_totp_secret:
            login_kwargs["totp_secret"] = settings.twitter_totp_secret

        try:
            await self._client.login(**login_kwargs)
        except Exception as exc:
            text = str(exc)
            if "Cloudflare" in text or "unable to access" in text.lower():
                raise RuntimeError(
                    "Blocked by Cloudflare while logging into x.com; "
                    "use browser-exported cookies with TWITTER_COOKIES_FILE"
                ) from exc
            raise
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        await self._client.save_cookies(str(cookies_path))
        print(f"Twitter cookies saved to: {cookies_path}")

    async def _poll_handle(self, handle: str) -> None:
        clean_handle = self._normalize_handle(handle)
        if not clean_handle:
            return

        try:
            user = await self._client.get_user_by_screen_name(clean_handle)
            tweets = await self._client.get_user_tweets(user.id, "Tweets", count=8)
        except Exception as exc:
            print(f"Twitter poll error for @{clean_handle}: {exc}")
            return

        for tweet in reversed(tweets):
            tweet_id = str(getattr(tweet, "id", ""))
            if not tweet_id:
                continue
            if tweet_id in self._seen_ids:
                continue

            self._remember_tweet(tweet_id)

            raw_text = (
                getattr(tweet, "full_text", None)
                or getattr(tweet, "text", None)
                or ""
            ).strip()
            if len(raw_text) < 3:
                continue

            await self._on_news(
                {
                    "raw_text": raw_text,
                    "source": "twitter",
                    "source_channel": clean_handle,
                    "url": f"https://x.com/{clean_handle}/status/{tweet_id}",
                    "published_at": getattr(tweet, "created_at", None),
                }
            )

    def _remember_tweet(self, tweet_id: str) -> None:
        self._seen_ids.add(tweet_id)
        self._seen_order.append(tweet_id)

        while len(self._seen_ids) > self._seen_order.maxlen:
            old_id = self._seen_order.popleft()
            self._seen_ids.discard(old_id)

    @staticmethod
    def _normalize_handle(value: str) -> str:
        raw = (value or "").strip().strip('"').strip("'")
        if not raw:
            return ""

        raw = raw.replace("\r", "").replace("\n", "")

        if "x.com/" in raw:
            raw = raw.split("x.com/", 1)[1]
        elif "twitter.com/" in raw:
            raw = raw.split("twitter.com/", 1)[1]

        raw = raw.split("?", 1)[0].split("#", 1)[0].strip("/")
        if "/" in raw:
            raw = raw.split("/", 1)[0]

        return raw.lstrip("@").strip()
