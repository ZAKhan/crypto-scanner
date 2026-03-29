import os

APP_VERSION = "v3.0.0"

def _get_app_data_dir() -> str:
    import platform
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif system == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, "CryptoScalper")

APP_DATA_DIR = _get_app_data_dir()
APP_LOGS_DIR = os.path.join(APP_DATA_DIR, "logs")

CFG = {
    "max_price":       1.0,
    "min_volume_usdt": 3_000_000,   # raised 1M → 3M: filters thin/illiquid coins
    "interval":        "5m",
    "candle_limit":    50,
    "top_n":           30,
    "picks_n":         5,
    "rsi_period":      14,
    "base_url":        "https://api.binance.com",
    # ── Risk Management ──────────────────────────────
    "sl_pct":             3.0,
    "tp_pct":             5.0,
    "tp2_pct":           10.0,
    "min_expected_move":  2.5,      # raised 2.0 → 2.5
    # ── New Listing Filter ────────────────────────────────
    "new_listing_filter":   False,
    "new_listing_min_days": 2,
    "new_listing_max_days": 10,
    # ── Symbol blocklist ─────────────────────────────────
    # Stablecoins, pegged tokens, broken/illiquid tokens that generate
    # constant noise alerts with zero real move potential.
    "symbol_blocklist": {
        "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDPUSDT", "FRAXUSDT",
        "DAIUSDT",  "USDEUSDT", "FDUSDUSDT", "AEUSDUSDT", "SUSDUSDT",
        "MBLUSDT",  "A2ZUSDT",  "WLFIUSDT",
    },
}
