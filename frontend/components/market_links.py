"""
Official Market Links & Token Research Utility
==============================================
Generates accurate, official exchange trading URLs and token background / website links
for Hummingbot positions, scanners, and trade records.
Utilizes Canonical Token Normalization to resolve multiplier-prefixed symbols
(e.g. PEPE -> 1000PEPE on Binance, kPEPE on Hyperliquid).
"""
from typing import Dict
from frontend.components.token_normalization import to_exchange_symbol, to_canonical_token


def get_official_market_url(connector_name: str, token: str) -> str:
    """
    Generate the official web trading terminal URL for a given exchange connector and token.
    Resolves canonical multipliers (e.g. PEPE -> 1000PEPE on Binance, kPEPE on Hyperliquid).
    Guaranteed official URLs:
    - Binance Futures: https://www.binance.com/zh-CN/futures/{EX_SYMBOL}USDT
    - Hyperliquid: https://app.hyperliquid.xyz/trade/{EX_SYMBOL}
    - Bybit: https://www.bybit.com/trade/usdt/{EX_SYMBOL}USDT
    - OKX: https://www.okx.com/zh-hans/trade-swap/{EX_SYMBOL}-usdt-swap
    - Gate.io: https://www.gate.io/zh/futures/USDT/{EX_SYMBOL}_USDT
    - KuCoin: https://www.kucoin.com/zh-hant/futures/trade/{EX_SYMBOL}USDTM
    """
    if not token:
        return "https://www.tradingview.com"
        
    c_lower = (connector_name or "").lower()
    ex_sym = to_exchange_symbol(token, connector_name)

    # 1. Hyperliquid DEX Perpetual
    if "hyperliquid" in c_lower:
        return f"https://app.hyperliquid.xyz/trade/{ex_sym}"

    # 2. Binance USDⓈ-M Futures
    elif "binance" in c_lower:
        return f"https://www.binance.com/zh-CN/futures/{ex_sym}USDT"

    # 3. Bybit Perpetual
    elif "bybit" in c_lower:
        return f"https://www.bybit.com/trade/usdt/{ex_sym}USDT"

    # 4. OKX Perpetual Swap
    elif "okx" in c_lower:
        return f"https://www.okx.com/zh-hans/trade-swap/{ex_sym.lower()}-usdt-swap"

    # 5. Gate.io Futures
    elif "gate" in c_lower:
        return f"https://www.gate.io/zh/futures/USDT/{ex_sym}_USDT"

    # 6. KuCoin Futures
    elif "kucoin" in c_lower:
        return f"https://www.kucoin.com/zh-hant/futures/trade/{ex_sym}USDTM"

    # Fallback to TradingView
    return f"https://www.tradingview.com/chart/?symbol={ex_sym}USDT"


def get_token_research_urls(token: str) -> Dict[str, str]:
    """
    Generate authoritative research links for a token:
    - TradingView Live K-Line Chart (Direct Binance Perpetual)
    - CoinGlass Derivatives & Funding History (Direct Binance Cleared Contract)
    - Binance Official Contract Spec & Project Verification (About Token Tab)
    - CoinGecko Project Website, Whitepaper & Info (Canonical Token Ticker)
    - CoinMarketCap Overview (Canonical Token Ticker)
    """
    bn_sym = to_exchange_symbol(token, "binance_perpetual")
    canon = to_canonical_token(token)
    return {
        "tradingview": f"https://www.tradingview.com/chart/?symbol=BINANCE:{bn_sym}USDT.P",
        "coinglass": f"https://www.coinglass.com/tv/Binance_{bn_sym}USDT",
        "binance_info": f"https://www.binance.com/zh-CN/futures/{bn_sym}USDT",
        "coingecko": f"https://www.coingecko.com/zh/search_queries?query={canon}",
        "coinmarketcap": f"https://coinmarketcap.com/search/?q={canon}",
    }
