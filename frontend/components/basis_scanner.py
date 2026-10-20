import json
import urllib.request
from typing import Dict, List, Optional
import pandas as pd


def fetch_binance_perpetual_prices_and_rates(timeout: int = 5) -> Dict[str, Dict]:
    """Fetch mark prices, 24h volumes, and funding rates from Binance public API."""
    data_map = {}
    # 1. Fetch 24h volume & price
    try:
        t_url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        t_req = urllib.request.Request(t_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(t_req, timeout=timeout) as resp:
            t_data = json.loads(resp.read().decode())
            for item in t_data:
                sym = item.get("symbol", "")
                if sym.endswith("USDT"):
                    c = sym[:-4]
                    data_map[c] = {
                        "price": float(item.get("lastPrice", 0.0)),
                        "vol_24h_usd": float(item.get("quoteVolume", 0.0)),
                        "rate_daily": 0.0,
                    }
    except Exception:
        pass

    # 2. Fetch funding rates
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            p_data = json.loads(resp.read().decode())
            for item in p_data:
                symbol = item.get("symbol", "")
                if symbol.endswith("USDT"):
                    coin = symbol[:-4]
                    if coin in data_map:
                        try:
                            rate_8h = float(item.get("lastFundingRate", 0.0))
                            data_map[coin]["rate_daily"] = rate_8h * 3.0
                            if data_map[coin]["price"] == 0.0:
                                data_map[coin]["price"] = float(item.get("markPrice", 0.0))
                        except Exception:
                            pass
    except Exception:
        pass
    return data_map


def fetch_hyperliquid_perpetual_prices_and_rates(timeout: int = 5) -> Dict[str, Dict]:
    """Fetch mark prices, 24h volumes, and funding rates from Hyperliquid public API."""
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
                    rate_1h = float(ctx.get("funding", 0.0))
                    mark_px = float(ctx.get("markPx", 0.0))
                    vol_24h_usd = float(ctx.get("dayNtlVlm", 0.0))
                    rates[coin] = {
                        "price": mark_px,
                        "rate_daily": rate_1h * 24.0,
                        "vol_24h_usd": vol_24h_usd,
                    }
                except Exception:
                    pass
    except Exception:
        pass
    return rates


def scan_cross_exchange_basis_opportunities(
    target_tokens: Optional[List[str]] = None,
    min_basis_pct: float = 0.0050,
) -> pd.DataFrame:
    """
    Scan real-time basis spreads across Hyperliquid and Binance for target tokens.
    Calculates basis divergence, recommended trade direction, and expected daily carry.
    """
    default_liquid_universe = [
        "BTC", "ETH", "SOL", "AVAX", "SUI", "DOGE", "NEAR", "APT", "ARB", "OP", "LINK", "BNB", "XRP"
    ]
    tokens_to_scan = target_tokens if target_tokens else default_liquid_universe

    binance_data = fetch_binance_perpetual_prices_and_rates()
    hl_data = fetch_hyperliquid_perpetual_prices_and_rates()

    results = []
    for token in tokens_to_scan:
        b_info = binance_data.get(token)
        hl_info = hl_data.get(token)
        if not b_info or not hl_info:
            continue

        p_binance = b_info["price"]
        p_hl = hl_info["price"]
        if p_binance <= 0 or p_hl <= 0:
            continue

        # Basis spread: (P_HL - P_Binance) / P_Binance
        basis_spread_pct = (p_hl - p_binance) / p_binance
        abs_basis_spread = abs(basis_spread_pct)

        rate_b = b_info["rate_daily"]
        rate_hl = hl_info["rate_daily"]

        if basis_spread_pct > 0:
            # HL overvalued: Short HL, Long Binance
            recommended_action = "🔴 空 Hyperliquid / 🟢 多 Binance"
            # Shorting HL earns +rate_hl if rate_hl > 0; longing Binance pays rate_b if rate_b > 0
            carry_daily_pct = rate_hl - rate_b
        else:
            # HL undervalued: Long HL, Short Binance
            recommended_action = "🟢 多 Hyperliquid / 🔴 空 Binance"
            carry_daily_pct = rate_b - rate_hl

        is_dislocated = abs_basis_spread >= min_basis_pct

        results.append({
            "token": token,
            "hl_price": p_hl,
            "binance_price": p_binance,
            "basis_spread_pct": basis_spread_pct,
            "abs_basis_pct": abs_basis_spread,
            "action": recommended_action,
            "hl_funding_daily": rate_hl * 100.0,
            "binance_funding_daily": rate_b * 100.0,
            "carry_daily_pct": carry_daily_pct * 100.0,
            "carry_apr": carry_daily_pct * 365.0 * 100.0,
            "status": "⚡ 脉冲展开 (可套利)" if is_dislocated else "⚖️ 正常窄幅",
            "vol_24h_usd": min(b_info["vol_24h_usd"], hl_info["vol_24h_usd"]),
        })

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values(by="abs_basis_pct", ascending=False).reset_index(drop=True)
    return df
