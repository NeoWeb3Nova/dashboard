import json
import urllib.request
from typing import Dict, List, Optional
import pandas as pd


def fetch_binance_perpetual_funding_rates(timeout: int = 5) -> Dict[str, Dict]:
    """Fetch all perpetual funding rates and 24h volumes from Binance public API."""
    rates = {}
    # 1. Fetch 24h volume
    vol_map = {}
    try:
        t_url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        t_req = urllib.request.Request(t_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(t_req, timeout=timeout) as resp:
            t_data = json.loads(resp.read().decode())
            for item in t_data:
                sym = item.get("symbol", "")
                if sym.endswith("USDT"):
                    c = sym[:-4]
                    vol_map[c] = float(item.get("quoteVolume", 0.0))
    except Exception:
        pass

    # 2. Fetch funding rates
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            for item in data:
                symbol = item.get("symbol", "")
                if symbol.endswith("USDT"):
                    coin = symbol[:-4]
                    try:
                        rate_8h = float(item.get("lastFundingRate", 0.0))
                        mark_px = float(item.get("markPrice", 0.0))
                        rates[coin] = {
                            "rate_daily": rate_8h * 3.0,
                            "rate_8h": rate_8h,
                            "mark_price": mark_px,
                            "vol_24h_usd": vol_map.get(coin, 0.0),
                            "next_funding_time": int(item.get("nextFundingTime", 0)) // 1000,
                        }
                    except (ValueError, TypeError):
                        pass
    except Exception as e:
        print(f"Error fetching Binance funding rates: {e}")
    return rates


def fetch_hyperliquid_perpetual_funding_rates(timeout: int = 5) -> Dict[str, Dict]:
    """Fetch all perpetual funding rates, 24h volumes, and open interest from Hyperliquid public API."""
    url = "https://api.hyperliquid.xyz/info"
    payload = json.dumps({"type": "metaAndAssetCtxs"}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    rates = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            universe = data[0].get("universe", [])
            ctxs = data[1] if len(data) > 1 else []
            for meta, ctx in zip(universe, ctxs):
                coin = meta.get("name")
                if not coin:
                    continue
                try:
                    # Hyperliquid funding is 1h rate: rate_1h * 24 = daily rate
                    rate_1h = float(ctx.get("funding", 0.0))
                    mark_px = float(ctx.get("markPx", 0.0))
                    vol_24h_usd = float(ctx.get("dayNtlVlm", 0.0))
                    oi_tokens = float(ctx.get("openInterest", 0.0))
                    rates[coin] = {
                        "rate_daily": rate_1h * 24.0,
                        "rate_1h": rate_1h,
                        "mark_price": mark_px,
                        "vol_24h_usd": vol_24h_usd,
                        "open_interest_usd": oi_tokens * mark_px,
                    }
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        print(f"Error fetching Hyperliquid funding rates: {e}")
    return rates


def scan_cross_exchange_opportunities(
    connector_1: str = "hyperliquid_perpetual",
    connector_2: str = "binance_perpetual",
    min_apr: float = 0.0,
    timeout: int = 6
) -> List[Dict]:
    """Scan and compute real-time funding rate arbitrage spreads, orderbook liquidity tiers and safe caps."""
    hl_rates = fetch_hyperliquid_perpetual_funding_rates(timeout=timeout)
    bn_rates = fetch_binance_perpetual_funding_rates(timeout=timeout)

    common_coins = set(hl_rates.keys()) & set(bn_rates.keys())
    results = []

    for coin in common_coins:
        h_info = hl_rates[coin]
        b_info = bn_rates[coin]

        h_daily = h_info["rate_daily"]
        b_daily = b_info["rate_daily"]
        daily_diff = abs(h_daily - b_daily)
        apr = daily_diff * 365.0 * 100.0

        if apr < min_apr:
            continue

        # Recommended direction
        if h_daily < b_daily:
            best_path = "Hyperliquid(多) + Binance(空)"
            c1_side, c2_side = "BUY", "SELL"
        else:
            best_path = "Binance(多) + Hyperliquid(空)"
            c1_side, c2_side = "SELL", "BUY"

        hl_vol = h_info.get("vol_24h_usd", 0.0)
        bn_vol = b_info.get("vol_24h_usd", 0.0)
        hl_oi = h_info.get("open_interest_usd", 0.0)

        # Liquidity evaluation: Hyperliquid is usually the bottleneck for altcoins
        if hl_vol >= 5_000_000:
            liq_badge = "🟢 充沛"
            safe_cap = "$20,000+"
            liq_rank = 3
        elif hl_vol >= 500_000:
            liq_badge = "🟡 适中"
            safe_cap = "$2,000 ~ $5,000"
            liq_rank = 2
        elif hl_vol >= 100_000:
            liq_badge = "🟠 较薄"
            safe_cap = "$500 ~ $1,500"
            liq_rank = 1
        else:
            liq_badge = "🔴 极薄 (易滑点)"
            safe_cap = "≤ $500"
            liq_rank = 0

        results.append({
            "coin": coin,
            "daily_diff_pct": daily_diff * 100.0,
            "diff_daily_pct": daily_diff * 100.0,
            "apr": apr,
            "best_path": best_path,
            "connector_1_rate_daily": h_daily * 100.0,
            "connector_2_rate_daily": b_daily * 100.0,
            "price": h_info.get("mark_price", 0.0),
            "hl_vol_24h": hl_vol,
            "bn_vol_24h": bn_vol,
            "hl_oi": hl_oi,
            "liquidity_badge": liq_badge,
            "safe_cap": safe_cap,
            "liq_rank": liq_rank,
        })

    # Sort descending by daily spread / APR
    results.sort(key=lambda x: x["daily_diff_pct"], reverse=True)
    return results
