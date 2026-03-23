import requests

from cs.config import CFG

def api_get(path, params=None, timeout=10):
    url = CFG["base_url"] + path
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def fetch_all_tickers():
    return api_get("/api/v3/ticker/24hr")

def fetch_klines(symbol, interval, limit):
    return api_get("/api/v3/klines",
                   {"symbol": symbol, "interval": interval, "limit": limit})

def fetch_trend_1h(symbol):
    """
    Fetch last 60 1h candles and return 1h trend: "up", "down", or "flat".
    Method: price vs EMA50 on 1h closes + EMA slope.
    """
    try:
        raw = api_get("/api/v3/klines",
                      {"symbol": symbol, "interval": "1h", "limit": 60})
        closes = [float(k[4]) for k in raw]
        if len(closes) < 52:
            return "flat"
        from cs.indicators import ema
        e50 = ema(closes, 50)
        if not e50:
            return "flat"
        price   = closes[-1]
        ema_now = e50[-1]
        slope   = (e50[-1] - e50[-5]) / price * 100 if len(e50) >= 5 else 0
        if price > ema_now * 1.005 and slope > 0.05:
            return "up"
        elif price < ema_now * 0.995 and slope < -0.05:
            return "down"
        else:
            return "flat"
    except Exception:
        return "flat"


# ─────────────────────────────────────────────────────────────────────────────
#  TRADING CONFIG
# ─────────────────────────────────────────────────────────────────────────────
TRADING_CFG = {
    "api_key":    "",
    "api_secret": "",
    "testnet":    True,   # always start on testnet
    "oco_enabled": True,  # place OCO stop-loss on Binance after buy
}

TESTNET_BASE = "https://testnet.binance.vision"
LIVE_BASE    = "https://api.binance.com"

def trading_base() -> str:
    return TESTNET_BASE if TRADING_CFG["testnet"] else LIVE_BASE
