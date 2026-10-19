"""
Canonical Token Normalization & Cross-Exchange Multiplier Resolution Engine
=============================================================================
Solves cross-exchange token naming discrepancies, unit multipliers (1000x / 1M x meme coins),
and protects against homonym ticker collisions (同名不同币) and unannounced token splits.

Core capabilities:
1. Bidirectional Mapping: Canonical Asset (e.g. PEPE) <-> Exchange Symbol (Binance: 1000PEPE, Hyperliquid: kPEPE)
2. Multiplier Management: Tracks unit multipliers (e.g. 1000x for PEPE, 1000000x for MOG) for normalized pricing & sizing
3. Cross-Exchange Universe Matcher: Discovers high-liquidity meme coins missed by raw string intersections
4. Sanity Circuit Breaker: Automatically rejects pairs with >3% normalized price divergence (e.g. RNDR vs RENDER or kDOGS)
"""

from typing import Dict, List, Optional, Tuple
import re

# Comprehensive Institutional Canonical Asset Registry
CANONICAL_TOKEN_REGISTRY: Dict[str, Dict] = {
    "PEPE": {
        "name": "Pepe",
        "connectors": {
            "binance_perpetual": {"symbol": "1000PEPE", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kPEPE", "multiplier": 1000.0, "quote": "USD"},
            "bybit_perpetual": {"symbol": "1000PEPE", "multiplier": 1000.0, "quote": "USDT"},
            "okx_perpetual": {"symbol": "PEPE", "multiplier": 10000000.0, "quote": "USDT"},
        }
    },
    "BONK": {
        "name": "Bonk",
        "connectors": {
            "binance_perpetual": {"symbol": "1000BONK", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kBONK", "multiplier": 1000.0, "quote": "USD"},
            "bybit_perpetual": {"symbol": "1000BONK", "multiplier": 1000.0, "quote": "USDT"},
        }
    },
    "SHIB": {
        "name": "Shiba Inu",
        "connectors": {
            "binance_perpetual": {"symbol": "1000SHIB", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kSHIB", "multiplier": 1000.0, "quote": "USD"},
            "bybit_perpetual": {"symbol": "1000SHIB", "multiplier": 1000.0, "quote": "USDT"},
        }
    },
    "FLOKI": {
        "name": "Floki",
        "connectors": {
            "binance_perpetual": {"symbol": "1000FLOKI", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kFLOKI", "multiplier": 1000.0, "quote": "USD"},
            "bybit_perpetual": {"symbol": "1000FLOKI", "multiplier": 1000.0, "quote": "USDT"},
        }
    },
    "LUNC": {
        "name": "Terra Classic",
        "connectors": {
            "binance_perpetual": {"symbol": "1000LUNC", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kLUNC", "multiplier": 1000.0, "quote": "USD"},
            "bybit_perpetual": {"symbol": "1000LUNC", "multiplier": 1000.0, "quote": "USDT"},
        }
    },
    "NEIRO": {
        "name": "First Neiro on Ethereum",
        "connectors": {
            "binance_perpetual": {"symbol": "NEIRO", "multiplier": 1.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kNEIRO", "multiplier": 1000.0, "quote": "USD"},
            "bybit_perpetual": {"symbol": "1000NEIRO", "multiplier": 1000.0, "quote": "USDT"},
        }
    },
    "XEC": {
        "name": "eCash",
        "connectors": {
            "binance_perpetual": {"symbol": "1000XEC", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kXEC", "multiplier": 1000.0, "quote": "USD"},
        }
    },
    "RATS": {
        "name": "rats (Ordinals)",
        "connectors": {
            "binance_perpetual": {"symbol": "1000RATS", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kRATS", "multiplier": 1000.0, "quote": "USD"},
        }
    },
    "SATS": {
        "name": "SATS (Ordinals)",
        "connectors": {
            "binance_perpetual": {"symbol": "1000SATS", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kSATS", "multiplier": 1000.0, "quote": "USD"},
        }
    },
    "CAT": {
        "name": "Simon's Cat",
        "connectors": {
            "binance_perpetual": {"symbol": "1000CAT", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kCAT", "multiplier": 1000.0, "quote": "USD"},
        }
    },
    "CHEEMS": {
        "name": "Cheems",
        "connectors": {
            "binance_perpetual": {"symbol": "1000CHEEMS", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kCHEEMS", "multiplier": 1000.0, "quote": "USD"},
        }
    },
    "WHY": {
        "name": "WHY",
        "connectors": {
            "binance_perpetual": {"symbol": "1000WHY", "multiplier": 1000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "kWHY", "multiplier": 1000.0, "quote": "USD"},
        }
    },
    "MOG": {
        "name": "Mog Coin",
        "connectors": {
            "binance_perpetual": {"symbol": "1000000MOG", "multiplier": 1000000.0, "quote": "USDT"},
            "hyperliquid_perpetual": {"symbol": "MOG", "multiplier": 1.0, "quote": "USD"},
        }
    },
}

# Reverse index: (connector_prefix, exchange_symbol) -> (canonical_token, multiplier)
_REVERSE_REGISTRY: Dict[Tuple[str, str], Tuple[str, float]] = {}
for canonical, info in CANONICAL_TOKEN_REGISTRY.items():
    for conn, cdata in info.get("connectors", {}).items():
        _REVERSE_REGISTRY[(conn, cdata["symbol"])] = (canonical, cdata["multiplier"])


def to_canonical_token(symbol: str, connector_name: str = "") -> str:
    """
    Resolve any exchange-specific symbol (e.g. '1000PEPE', 'kPEPE', 'PEPE-USDT')
    to its canonical asset ticker (e.g. 'PEPE').
    Preserves case sensitivity for Hyperliquid lowercase 'k' prefix to prevent
    stripping genuine tokens like KAITO or KAS.
    """
    if not symbol:
        return ""
    
    raw_clean = symbol.strip().split("-")[0]
    clean_sym = raw_clean.upper()
    c_lower = (connector_name or "").lower()

    # 1. Direct registry lookup
    for (conn, ex_sym), (canon, _) in _REVERSE_REGISTRY.items():
        if raw_clean == ex_sym or clean_sym == ex_sym.upper():
            if not c_lower or conn.startswith(c_lower) or c_lower.startswith(conn.split("_")[0]):
                return canon

    # 2. If it's already a known canonical token
    if clean_sym in CANONICAL_TOKEN_REGISTRY:
        return clean_sym

    # 3. Algorithmic heuristic fallback across industry exchanges
    # Hyperliquid 'k' prefix is explicitly lowercase: kPEPE, kSHIB, kBONK, kFLOKI, kLUNC
    # Genuine tickers like KAITO, KAS, KAVA start with uppercase 'K'
    if raw_clean.startswith("k") and len(raw_clean) >= 4 and raw_clean[1:].isupper():
        return raw_clean[1:]
    # dYdX '1k' prefix (e.g. 1kPEPE)
    if clean_sym.startswith("1K") and len(clean_sym) > 3:
        return clean_sym[2:]
    # Binance / Bybit / Gate '1000000' prefix (e.g. 1000000MOG)
    if clean_sym.startswith("1000000") and len(clean_sym) > 7:
        return clean_sym[7:]
    # Bybit '10000' prefix (e.g. 10000LADYS)
    if clean_sym.startswith("10000") and len(clean_sym) > 5:
        return clean_sym[5:]
    # Binance / Bybit / Bitget / KuCoin '1000' prefix (e.g. 1000PEPE, 1000BONK)
    if clean_sym.startswith("1000") and len(clean_sym) > 4:
        return clean_sym[4:]

    return clean_sym


def to_exchange_symbol(canonical_token: str, connector_name: str) -> str:
    """
    Convert a canonical token (or arbitrary symbol) to the exchange-specific native ticker.
    Supports all major CEX and DEX derivative platforms:
    - Binance / Bybit / Bitget / KuCoin: 1000PEPE, 1000SHIB, 1000000MOG
    - Hyperliquid: kPEPE, kSHIB, kBONK
    - dYdX: 1kPEPE
    - OKX: PEPE (quotes per base coin or standard swap symbol)
    """
    canon = to_canonical_token(canonical_token, connector_name)
    c_lower = (connector_name or "").lower()

    if canon in CANONICAL_TOKEN_REGISTRY:
        connectors_data = CANONICAL_TOKEN_REGISTRY[canon].get("connectors", {})
        # Exact match connector key
        for conn_key, val in connectors_data.items():
            if conn_key in c_lower or c_lower.startswith(conn_key.split("_")[0]):
                return val["symbol"]

    # Algorithmic fallback
    if "hyperliquid" in c_lower:
        if canon in ["PEPE", "SHIB", "BONK", "FLOKI", "LUNC", "CAT", "CHEEMS"]:
            return f"k{canon}"
    elif "dydx" in c_lower:
        if canon in ["PEPE", "SHIB", "BONK"]:
            return f"1k{canon}"
    elif any(k in c_lower for k in ["binance", "bybit", "bitget", "kucoin"]):
        if canon in ["PEPE", "SHIB", "BONK", "FLOKI", "LUNC", "CAT", "CHEEMS", "WHY", "RATS", "SATS", "XEC"]:
            return f"1000{canon}"
        if canon in ["MOG", "BOB"]:
            return f"1000000{canon}"

    return canon


def get_token_multiplier(token: str, connector_name: str) -> float:
    """
    Get the contract unit multiplier for a token on a specific connector.
    Returns:
    - 1000000.0 for 1000000MOG
    - 10000.0 for 10000LADYS
    - 1000.0 for 1000PEPE, kPEPE, 1kPEPE
    - 1.0 for normal tokens (BTC, ETH, SOL, KAITO, KAS)
    """
    canon = to_canonical_token(token, connector_name)
    c_lower = (connector_name or "").lower()

    if canon in CANONICAL_TOKEN_REGISTRY:
        connectors_data = CANONICAL_TOKEN_REGISTRY[canon].get("connectors", {})
        for conn_key, val in connectors_data.items():
            if conn_key in c_lower or c_lower.startswith(conn_key.split("_")[0]):
                return float(val.get("multiplier", 1.0))

    # Algorithmic fallback
    raw_clean = token.strip().split("-")[0]
    clean_sym = raw_clean.upper()
    if clean_sym.startswith("1000000"):
        return 1000000.0
    if clean_sym.startswith("10000"):
        return 10000.0
    if clean_sym.startswith("1000") or clean_sym.startswith("1K"):
        return 1000.0
    if raw_clean.startswith("k") and len(raw_clean) >= 4 and raw_clean[1:].isupper():
        return 1000.0

    return 1.0


def to_exchange_trading_pair(canonical_token: str, connector_name: str) -> str:
    """
    Convert a canonical token to the complete Hummingbot trading pair string.
    E.g. ('PEPE', 'hyperliquid_perpetual') -> 'kPEPE-USD'
         ('PEPE', 'binance_perpetual') -> '1000PEPE-USDT'
    """
    symbol = to_exchange_symbol(canonical_token, connector_name)
    c_lower = (connector_name or "").lower()
    
    quote = "USD" if "hyperliquid" in c_lower else "USDT"
    return f"{symbol}-{quote}"


def match_canonical_cross_exchange_universes(
    hl_rates: Dict[str, Dict],
    bn_rates: Dict[str, Dict],
    max_price_divergence: float = 0.03
) -> List[Dict]:
    """
    Match Hyperliquid and Binance universes using Canonical Asset Resolution.
    Verifies that multiplier-normalized prices match within max_price_divergence (default 3%).
    Safely rejects homonyms and unannounced splits.
    """
    hl_canonical: Dict[str, Dict] = {}
    for raw_hl_sym, info in hl_rates.items():
        canon = to_canonical_token(raw_hl_sym, "hyperliquid_perpetual")
        mult = get_token_multiplier(raw_hl_sym, "hyperliquid_perpetual")
        hl_canonical[canon] = {
            "raw_symbol": raw_hl_sym,
            "multiplier": mult,
            "info": info,
            "normalized_mark_price": info.get("mark_price", 0.0) / mult if mult > 0 else 0.0,
        }

    bn_canonical: Dict[str, Dict] = {}
    for raw_bn_sym, info in bn_rates.items():
        canon = to_canonical_token(raw_bn_sym, "binance_perpetual")
        mult = get_token_multiplier(raw_bn_sym, "binance_perpetual")
        bn_canonical[canon] = {
            "raw_symbol": raw_bn_sym,
            "multiplier": mult,
            "info": info,
            "normalized_mark_price": info.get("mark_price", 0.0) / mult if mult > 0 else 0.0,
        }

    common_canonicals = set(hl_canonical.keys()) & set(bn_canonical.keys())
    matched_results = []

    for canon in common_canonicals:
        hl_data = hl_canonical[canon]
        bn_data = bn_canonical[canon]

        p1_norm = hl_data["normalized_mark_price"]
        p2_norm = bn_data["normalized_mark_price"]

        # Price Divergence Pre-Flight Check (Circuit Breaker)
        avg_norm_px = (p1_norm + p2_norm) / 2.0
        if avg_norm_px > 0:
            price_div = abs(p1_norm - p2_norm) / avg_norm_px
        else:
            price_div = 0.0

        # Reject impostors or unadjusted splits
        if price_div > max_price_divergence:
            continue

        matched_results.append({
            "canonical": canon,
            "hl_raw_symbol": hl_data["raw_symbol"],
            "bn_raw_symbol": bn_data["raw_symbol"],
            "hl_multiplier": hl_data["multiplier"],
            "bn_multiplier": bn_data["multiplier"],
            "hl_info": hl_data["info"],
            "bn_info": bn_data["info"],
            "price_divergence_pct": price_div * 100.0,
            "is_multiplier_asset": (hl_data["multiplier"] > 1.0 or bn_data["multiplier"] > 1.0),
        })

    return matched_results
