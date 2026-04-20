from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _is_x_cookie(item: dict[str, Any]) -> bool:
    domain = str(item.get("domain") or "")
    if not domain:
        return True
    return "x.com" in domain or "twitter.com" in domain


def _extract_cookie_map(payload: Any) -> dict[str, str]:
    if isinstance(payload, dict):
        # Twikit format already uses a simple key/value cookie object.
        if all(isinstance(k, str) and isinstance(v, str) for k, v in payload.items()):
            return dict(payload)

        # Some browser exports wrap cookies in a top-level key.
        if "cookies" in payload and isinstance(payload["cookies"], list):
            payload = payload["cookies"]

    if isinstance(payload, list):
        cookie_map: dict[str, str] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if not name or value is None:
                continue
            if not _is_x_cookie(item):
                continue
            cookie_map[str(name)] = str(value)

        if cookie_map:
            return cookie_map

    raise ValueError(
        "Unsupported cookie format. Provide either a Twikit cookie dict or "
        "a browser export list containing name/value pairs."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert browser-exported X/Twitter cookies to Twikit JSON format."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to exported cookies JSON file.",
    )
    parser.add_argument(
        "--output",
        default="data/twitter_cookies.json",
        help="Output path for Twikit-compatible cookie JSON.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with input_path.open("r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    cookie_map = _extract_cookie_map(payload)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(cookie_map, f, indent=2, ensure_ascii=True)

    print(f"Converted {len(cookie_map)} cookies to: {output_path}")


if __name__ == "__main__":
    main()
