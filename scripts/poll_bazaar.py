"""
Runs once per GitHub Actions invocation: fetches the Hypixel Bazaar API,
filters for high-liquidity / wide-spread items, flags likely price
manipulation, and writes the result to docs/data.json, which the static
site (served via GitHub Pages) reads.

See the main README for the full explanation of the filter logic and the
manipulation-detection formula.
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests

BAZAAR_URL = "https://api.hypixel.net/v2/skyblock/bazaar"

VOLUME_THRESHOLD = float(os.getenv("VOLUME_THRESHOLD", "60000"))
SPREAD_THRESHOLD_PCT = float(os.getenv("SPREAD_THRESHOLD_PCT", "20"))
WINDOW_DAYS = float(os.getenv("WINDOW_DAYS", "5"))
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))
OUTPUT_PATH = os.getenv("OUTPUT_PATH", "docs/data.json")

# --- Manipulation detection settings ---
PRICE_HISTORY_PATH = os.getenv("PRICE_HISTORY_PATH", "price_history.json")
MANIPULATION_WINDOW_MINUTES = float(os.getenv("MANIPULATION_WINDOW_MINUTES", "10"))
MANIPULATION_PCT_THRESHOLD = float(os.getenv("MANIPULATION_PCT_THRESHOLD", "100"))
MANIPULATION_MIN_SELL_PRICE = float(os.getenv("MANIPULATION_MIN_SELL_PRICE", "1000"))
# Keep a bit more history than the lookback window needs, so a delayed/missed
# run doesn't leave us without a usable reference sample.
HISTORY_RETENTION_MINUTES = MANIPULATION_WINDOW_MINUTES + 15


def pct_swing(a: float, b: float):
    """Symmetric percent swing between two prices: doubling OR halving both
    read as a 100% swing, rather than only catching increases."""
    if a <= 0 or b <= 0:
        return None
    lo, hi = (a, b) if a < b else (b, a)
    return (hi / lo - 1) * 100


def load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def prune_history(history: dict, now_ts: float):
    cutoff = now_ts - HISTORY_RETENTION_MINUTES * 60
    for pid in list(history.keys()):
        history[pid] = [s for s in history[pid] if s["ts"] >= cutoff]
        if not history[pid]:
            del history[pid]


def find_reference_sample(samples: list, now_ts: float, window_seconds: float):
    """Most recent sample that is at least `window_seconds` old - i.e. the
    closest available match to "the price N minutes ago"."""
    candidates = [s for s in samples if now_ts - s["ts"] >= window_seconds]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s["ts"])


def check_manipulation(product_id: str, buy_price: float, sell_price: float,
                        history: dict, now_ts: float):
    window_seconds = MANIPULATION_WINDOW_MINUTES * 60
    ref = find_reference_sample(history.get(product_id, []), now_ts, window_seconds)
    if ref is None:
        return None

    buy_swing = pct_swing(buy_price, ref["buy_price"])
    sell_swing = pct_swing(sell_price, ref["sell_price"])

    triggered = (
        (buy_swing is not None and buy_swing > MANIPULATION_PCT_THRESHOLD)
        or (sell_swing is not None and sell_swing > MANIPULATION_PCT_THRESHOLD)
    )
    if not triggered or sell_price <= MANIPULATION_MIN_SELL_PRICE:
        return None

    return {
        "flagged": True,
        "prior_buy_price": round(ref["buy_price"], 2),
        "prior_sell_price": round(ref["sell_price"], 2),
        "buy_change_pct": round(buy_swing, 1) if buy_swing is not None else None,
        "sell_change_pct": round(sell_swing, 1) if sell_swing is not None else None,
        "reference_minutes_ago": round((now_ts - ref["ts"]) / 60, 1),
    }


def compute_filtered(products: dict, history: dict, now_ts: float) -> list[dict]:
    results = []
    for product_id, data in products.items():
        qs = data.get("quick_status", {})

        buy_price = qs.get("buyPrice", 0) or 0
        sell_price = qs.get("sellPrice", 0) or 0
        buy_moving_week = qs.get("buyMovingWeek", 0) or 0
        sell_moving_week = qs.get("sellMovingWeek", 0) or 0

        if buy_price <= 0 or sell_price <= 0:
            continue

        est_buy_volume = buy_moving_week * (WINDOW_DAYS / 7)
        est_sell_volume = sell_moving_week * (WINDOW_DAYS / 7)

        if est_buy_volume < VOLUME_THRESHOLD or est_sell_volume < VOLUME_THRESHOLD:
            continue

        spread_pct = (buy_price - sell_price) / sell_price * 100
        if spread_pct < SPREAD_THRESHOLD_PCT:
            continue

        avg_price = (buy_price + sell_price) / 2

        results.append(
            {
                "id": product_id,
                "name": product_id.replace("_", " ").title(),
                "buy_price": round(buy_price, 2),
                "sell_price": round(sell_price, 2),
                "avg_price": round(avg_price, 2),
                "spread_pct": round(spread_pct, 2),
                "est_buy_volume": round(est_buy_volume),
                "est_sell_volume": round(est_sell_volume),
                "buy_moving_week": round(buy_moving_week),
                "sell_moving_week": round(sell_moving_week),
                "manipulation": check_manipulation(product_id, buy_price, sell_price, history, now_ts),
            }
        )

    # Highest average of instant-buy/instant-sell price first.
    results.sort(key=lambda x: x["avg_price"], reverse=True)
    return results


def load_existing(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {"items": [], "products_scanned": 0, "last_updated": None}


def main():
    now_ts = datetime.now(timezone.utc).timestamp()
    payload = load_existing(OUTPUT_PATH)
    history = load_json(PRICE_HISTORY_PATH, {})
    error = None

    try:
        resp = requests.get(BAZAAR_URL, timeout=20)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success", False):
            raise RuntimeError(f"Hypixel API returned success=false: {body}")

        products = body.get("products", {})

        # Filter + flag using history as it stood BEFORE this run's prices are added.
        payload["items"] = compute_filtered(products, history, now_ts)
        payload["products_scanned"] = len(products)
        payload["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Record this run's prices for every product (not just filtered ones),
        # so items that start qualifying later still have price history behind them.
        for product_id, data in products.items():
            qs = data.get("quick_status", {})
            buy_price = qs.get("buyPrice", 0) or 0
            sell_price = qs.get("sellPrice", 0) or 0
            if buy_price <= 0 or sell_price <= 0:
                continue
            history.setdefault(product_id, []).append(
                {"ts": now_ts, "buy_price": buy_price, "sell_price": sell_price}
            )

        prune_history(history, now_ts)
        with open(PRICE_HISTORY_PATH, "w") as f:
            json.dump(history, f)

    except Exception as exc:
        # Keep whatever the last good data was; just surface the error.
        error = str(exc)

    payload["error"] = error
    payload["last_checked"] = datetime.now(timezone.utc).isoformat()
    payload["config"] = {
        "volume_threshold": VOLUME_THRESHOLD,
        "spread_threshold_pct": SPREAD_THRESHOLD_PCT,
        "window_days": WINDOW_DAYS,
        "poll_interval_minutes": POLL_INTERVAL_MINUTES,
        "manipulation_window_minutes": MANIPULATION_WINDOW_MINUTES,
        "manipulation_pct_threshold": MANIPULATION_PCT_THRESHOLD,
        "manipulation_min_sell_price": MANIPULATION_MIN_SELL_PRICE,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    if error:
        print(f"Completed with error (kept previous data): {error}", file=sys.stderr)
    else:
        flagged = sum(1 for i in payload["items"] if i.get("manipulation"))
        print(f"Wrote {len(payload['items'])} matching items to {OUTPUT_PATH} ({flagged} flagged as likely manipulation)")


if __name__ == "__main__":
    main()
