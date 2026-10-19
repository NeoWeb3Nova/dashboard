"""
Clock Synchronization Health Monitor for Hummingbot Dashboard
实时监测本地/VPS时钟与主流交易所（Binance）的偏差，防范 code: -1021 拒单与单腿裸奔。
"""

import time
import json
import urllib.request
import streamlit as st

@st.cache_data(ttl=60, show_spinner=False)
def get_exchange_clock_health():
    """
    轻量级探测本地与 Binance 合约服务器的时钟偏差（TTL 60s 缓存，不阻塞页面）
    """
    t0 = time.time()
    try:
        req = urllib.request.Request(
            "https://fapi.binance.com/fapi/v1/time",
            headers={"User-Agent": "HummingbotClockMonitor/1.0"}
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            t1 = time.time()
            data = json.loads(resp.read().decode())
            server_time = data["serverTime"]
            rtt = (t1 - t0) * 1000
            local_mid = (t0 + t1) / 2 * 1000
            offset = local_mid - server_time
            abs_offset = abs(offset)
            
            if abs_offset < 200:
                status = "excellent"
                badge = f"⏱️ 时钟同步优异 ({offset:+.0f}ms)"
                desc = "本地时钟与币安完全同步，套利风控处于最高安全等级。"
            elif abs_offset < 600:
                status = "warning"
                badge = f"🟡 时钟偏差注意 ({offset:+.0f}ms)"
                desc = "本地时钟偏差超过 200ms，如持续增大可能逼近交易所 1000ms 拒单红线。"
            else:
                status = "danger"
                badge = f"🔴 时钟严重滞后 ({offset:+.0f}ms)"
                desc = "⚠️ 严重风险：时钟偏差逼近 1000ms！极易触发 -1021 导致对冲单腿拒单变成裸奔，请立即校准！"
            
            return {
                "ok": True,
                "offset_ms": offset,
                "rtt_ms": rtt,
                "status": status,
                "badge": badge,
                "desc": desc,
            }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "status": "unknown",
            "badge": "⏱️ 时钟检测离线",
            "desc": f"无法连接币安服务器检测时钟: {e}",
        }
