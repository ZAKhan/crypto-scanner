import os

APP_VERSION = "v2.6.0"

# ─────────────────────────────────────────────────────────────────────────────
#  CROSS-PLATFORM DATA DIRECTORY
# ─────────────────────────────────────────────────────────────────────────────
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

APP_DATA_DIR  = _get_app_data_dir()
APP_LOGS_DIR  = os.path.join(APP_DATA_DIR, "logs")

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG  (edit these to change scan behaviour)
# ─────────────────────────────────────────────────────────────────────────────
CFG = {
    "max_price":       1.0,
    "min_volume_usdt": 1_000_000,
    "interval":        "5m",
    "candle_limit":    50,
    "top_n":           30,
    "picks_n":         5,
    "rsi_period":      14,
    "base_url":        "https://api.binance.com",
    # ── Risk Management ──────────────────────────────
    "sl_pct":             3.0,   # Stop Loss %
    "tp_pct":             5.0,   # Take Profit %
    "tp2_pct":           10.0,   # TP2 (extended target) %
    "min_expected_move":  2.0,   # Filter: only show coins expected to move >2%
}
