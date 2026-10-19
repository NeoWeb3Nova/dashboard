"""
Official Market Links & Token Research Utility
==============================================
Generates accurate, official exchange trading URLs and token background / website links
for Hummingbot positions, scanners, and trade records.
"""
from typing import Dict


def get_official_market_url(connector_name: str, token: str) -> str:
    """
    Generate the official web trading terminal URL for a given exchange connector and token.
    Guaranteed official URLs:
    - Binance Futures: https://www.binance.com/zh-CN/futures/{TOKEN}USDT
    - Hyperliquid: https://app.hyperliquid.xyz/trade/{TOKEN}
    - Bybit: https://www.bybit.com/trade/usdt/{TOKEN}USDT
    - OKX: https://www.okx.com/zh-hans/trade-swap/{token}-usdt-swap
    - Gate.io: https://www.gate.io/zh/futures/USDT/{TOKEN}_USDT
    - KuCoin: https://www.kucoin.com/zh-hant/futures/trade/{TOKEN}USDTM
    """
    if not token:
        return "https://www.tradingview.com"
        
    c_lower = (connector_name or "").lower()
    token_clean = str(token).strip().upper()

    # 1. Hyperliquid DEX Perpetual
    if "hyperliquid" in c_lower:
        return f"https://app.hyperliquid.xyz/trade/{token_clean}"

    # 2. Binance USDⓈ-M Futures
    elif "binance" in c_lower:
        return f"https://www.binance.com/zh-CN/futures/{token_clean}USDT"

    # 3. Bybit Perpetual
    elif "bybit" in c_lower:
        return f"https://www.bybit.com/trade/usdt/{token_clean}USDT"

    # 4. OKX Perpetual Swap
    elif "okx" in c_lower:
        return f"https://www.okx.com/zh-hans/trade-swap/{token_clean.lower()}-usdt-swap"

    # 5. Gate.io Futures
    elif "gate" in c_lower:
        return f"https://www.gate.io/zh/futures/USDT/{token_clean}_USDT"

    # 6. KuCoin Futures
    elif "kucoin" in c_lower:
        return f"https://www.kucoin.com/zh-hant/futures/trade/{token_clean}USDTM"

    # Fallback to TradingView
    return f"https://www.tradingview.com/chart/?symbol={token_clean}USDT"


def get_token_research_urls(token: str) -> Dict[str, str]:
    """
    Generate authoritative research links for a token:
    - TradingView Live K-Line Chart
    - CoinGlass Derivatives & Funding History
    - CoinGecko Project Website, Whitepaper & Info
    - CoinMarketCap Overview
    """
    token_clean = str(token).strip().upper()
    return {
        "tradingview": f"https://www.tradingview.com/chart/?symbol=BINANCE:{token_clean}USDT.P",
        "coinglass": f"https://www.coinglass.com/tv/Binance_{token_clean}USDT",
        "coingecko": f"https://www.coingecko.com/zh/search_queries?query={token_clean}",
        "coinmarketcap": f"https://coinmarketcap.com/search/?q={token_clean}",
    }
