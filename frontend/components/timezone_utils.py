"""
Timezone Management & Formatting Utility
========================================
Provides unified timezone selection, state persistence, and localized datetime
formatting across Hummingbot Dashboard and Arbitrage components.
"""
from datetime import datetime, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

TIMEZONE_CHOICES = {
    "Asia/Shanghai": "🇨🇳 北京时间 / 上海时间 (UTC+8)",
    "UTC": "🌐 UTC 世界协调时 (UTC+0)",
    "Asia/Tokyo": "🇯🇵 东京时间 (UTC+9)",
    "Asia/Singapore": "🇸🇬 新加坡时间 (UTC+8)",
    "America/New_York": "🇺🇸 纽约时间 (UTC-5/UTC-4)",
    "Europe/London": "🇬🇧 伦敦时间 (UTC+0/UTC+1)",
}

DEFAULT_TIMEZONE = "Asia/Shanghai"


def get_current_timezone(bot_conf: Optional[dict] = None) -> str:
    """
    Retrieve active timezone based on:
    1. User's manual selection in Streamlit session state
    2. Bot configuration parameter (if available)
    3. Default: Asia/Shanghai (Beijing/Shanghai time)
    """
    if "selected_timezone" in st.session_state and st.session_state["selected_timezone"] in TIMEZONE_CHOICES:
        return st.session_state["selected_timezone"]
    
    if bot_conf and isinstance(bot_conf, dict):
        tz = bot_conf.get("timezone")
        if tz in TIMEZONE_CHOICES:
            st.session_state["selected_timezone"] = tz
            return tz
            
    return DEFAULT_TIMEZONE


def get_timezone_label(tz_name: Optional[str] = None) -> str:
    """Return concise human-readable timezone badge."""
    tz = tz_name or get_current_timezone()
    if tz == "Asia/Shanghai":
        return "北京时间"
    elif tz == "UTC":
        return "UTC"
    elif tz == "Asia/Tokyo":
        return "东京时间"
    elif tz == "Asia/Singapore":
        return "新加坡时间"
    elif tz == "America/New_York":
        return "美东时间"
    elif tz == "Europe/London":
        return "伦敦时间"
    return tz


def render_timezone_selector(key: str = "global_tz_selector", label: str = "🌐 显示时区") -> Tuple[str, str]:
    """
    Render an interactive dropdown to switch display timezone.
    Returns: (tz_iana_string, tz_friendly_label)
    """
    curr_tz = get_current_timezone()
    tz_keys = list(TIMEZONE_CHOICES.keys())
    default_index = tz_keys.index(curr_tz) if curr_tz in tz_keys else 0

    selected_tz = st.selectbox(
        label,
        options=tz_keys,
        index=default_index,
        format_func=lambda k: TIMEZONE_CHOICES.get(k, k),
        key=key,
        help="选择系统中持仓时间、平仓记录、资金费结算点和系统日志的显示时区"
    )
    st.session_state["selected_timezone"] = selected_tz
    return selected_tz, get_timezone_label(selected_tz)


def format_timestamp(
    ts: Optional[object],
    tz_name: Optional[str] = None,
    fmt: str = "%Y-%m-%d %H:%M:%S"
) -> str:
    """
    Convert unix timestamp (seconds or milliseconds) or datetime string to localized string.
    """
    if ts is None or ts == "" or ts == 0:
        return "N/A"
    
    target_tz_str = tz_name or get_current_timezone()
    try:
        target_tz = ZoneInfo(target_tz_str)
    except Exception:
        target_tz = ZoneInfo(DEFAULT_TIMEZONE)

    try:
        # If already a number
        if isinstance(ts, (int, float)):
            ts_float = float(ts)
            if ts_float > 1e11:  # Milliseconds timestamp
                ts_float /= 1000.0
            utc_dt = datetime.fromtimestamp(ts_float, tz=timezone.utc)
            return utc_dt.astimezone(target_tz).strftime(fmt)
            
        # If numeric string
        str_val = str(ts).strip()
        try:
            ts_float = float(str_val)
            if ts_float > 1e11:
                ts_float /= 1000.0
            utc_dt = datetime.fromtimestamp(ts_float, tz=timezone.utc)
            return utc_dt.astimezone(target_tz).strftime(fmt)
        except ValueError:
            pass

        # If date string
        dt = pd.to_datetime(str_val)
        if dt is pd.NaT:
            return str_val
        if dt.tz is None:
            # Assume UTC if naive
            dt = dt.tz_localize("UTC")
        return dt.tz_convert(target_tz_str).strftime(fmt)
    except Exception:
        return str(ts)
