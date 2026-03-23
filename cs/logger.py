import os

from cs.config import APP_LOGS_DIR
from cs.safety import check_trade_safety, safety_mark_symbol_blocked


ALERT_CFG = {
    "enabled":          True,
    "interval_sec":     60,         # auto-scan interval
    "min_signal":       "BUY",      # minimum: BUY | STRONG BUY
    "sound":            True,
    "desktop":          True,
    "telegram":         False,
    "tg_token":         "",
    "tg_chat_id":       "",
    "whatsapp":         False,      # PicoClaw WhatsApp channel
    "wa_number":        "",         # recipient: country code + number, e.g. 923001234567
    "picoclaw_queue":   os.path.expanduser("~/.picoclaw/workspace/crypto_alerts.json"),
    "min_potential":    40,         # only alert if Pot% >= this
    "min_exp_move":     3.0,        # only alert if Exp% >= this
    "max_rsi":          70,         # only alert if RSI <= this
    "max_bb_pct":       80,         # only alert if BB% <= this (0=oversold, 100=overbought)
    "require_vol_spike": False,     # only alert if volume spike detected
    "min_adr_pct":      0.5,        # minimum avg candle range % — skip flat coins
    "block_downtrend":  True,       # Fix 1 — block alerts when pattern shows Downtrend
    "block_1h_downtrend": True,     # v2.4.5 — block BUY alerts when 1h trend is down
    "min_vol_ratio":    0.8,        # Fix 2 — minimum volume ratio vs average
    "spike_cooldown":   True,       # Fix 3 — skip coin if spiked >15% in last 3 hours
    "crash_cooldown":   True,       # v2.4.5 — skip BUY if single candle dropped >8% recently
    "crash_pct":        8.0,        # v2.4.5 — single candle drop % threshold
    "crash_cooldown_mins": 60,      # v2.4.5 — cooldown duration in minutes after crash candle
    "spike_pct":        15.0,       # Fix 3 — spike threshold %
    "require_macd_rising": False,   # Fix 4 — only alert if MACD is rising
    "coin_cooldown":       True,    # Fix 5 — per-coin alert cooldown
    "coin_cooldown_mins":  30,      # minutes before same coin can alert again
}


def _get_signal_log_path():
    """Returns today's signal log path: signal_log_YYYY-MM-DD.csv"""
    from datetime import datetime as _dt
    date_str = _dt.now().strftime("%Y-%m-%d")
    return os.path.join(APP_LOGS_DIR,
                        f"signal_log_{date_str}.csv")

def _cleanup_old_signal_logs(keep_days=7):
    """Delete signal log files older than keep_days."""
    import glob
    from datetime import datetime as _dt, timedelta as _td
    log_dir = APP_LOGS_DIR
    cutoff  = _dt.now() - _td(days=keep_days)
    for fpath in glob.glob(os.path.join(log_dir, "signal_log_*.csv")):
        fname = os.path.basename(fpath)
        try:
            date_str = fname.replace("signal_log_", "").replace(".csv", "")
            fdate = _dt.strptime(date_str, "%Y-%m-%d")
            if fdate < cutoff:
                os.remove(fpath)
        except Exception:
            pass

# For backward compat — points to today's file
SIGNAL_LOG_PATH = _get_signal_log_path()
_SIGNAL_LOG_HEADERS = [
    "timestamp", "symbol", "price", "change_24h",
    "signal", "confidence", "rsi", "stoch_rsi", "macd_hist",
    "bb_pct", "bb_width_pct", "vol_ratio", "vol_spike",
    "pattern", "long_score", "short_score",
    "potential", "exp_move", "trend_1h",
    "adr_pct", "alert_fired", "safety_blocked", "safety_reason",
    "price_30m", "pct_30m", "price_1h", "pct_1h",
    "price_4h", "pct_4h", "outcome"
]

def log_scan_results(results, alert_cfg=None, safety_cfg=None, trades=None):
    """
    Append all scan results to the signal log CSV.
    Called after every scan — builds a full audit trail.
    """
    import csv
    from datetime import datetime as _dt

    now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    trades = trades or []

    # Determine which signals would have fired an alert
    sig_order = {
        "PRE-BREAKOUT": 0, "STRONG BUY": 1, "STRONG SELL": 2,
        "BUY": 3, "SELL": 4, "NEUTRAL": 5
    }
    min_level = sig_order.get(
        ALERT_CFG.get("min_signal", "BUY"), 3
    )

    log_path   = _get_signal_log_path()
    file_exists = os.path.exists(log_path)
    # Clean up old logs (runs quickly, once per call)
    try:
        _cleanup_old_signal_logs(keep_days=7)
    except Exception:
        pass
    try:
        with open(log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_SIGNAL_LOG_HEADERS)
            if not file_exists:
                writer.writeheader()

            for r in results:
                sig   = r.get("signal", "NEUTRAL")
                pot   = r.get("potential", 0)
                exp   = r.get("expected_move", 0)
                rsi   = r.get("rsi", 0)
                bb_pct_raw = 0
                if r.get("bb_upper") and r.get("bb_lower") and r["bb_upper"] != r["bb_lower"]:
                    bb_pct_raw = (r["price"] - r["bb_lower"]) / (r["bb_upper"] - r["bb_lower"]) * 100

                # Would this have fired an alert?
                level = sig_order.get(sig, 5)
                alert_fired = (
                    level <= min_level and
                    pot  >= ALERT_CFG.get("min_potential", 0) and
                    exp  >= ALERT_CFG.get("min_exp_move", 0) and
                    rsi  <= ALERT_CFG.get("max_rsi", 100) and
                    bb_pct_raw <= ALERT_CFG.get("max_bb_pct", 200) and
                    r.get("vol_ratio", 0) >= ALERT_CFG.get("min_vol_ratio", 0) and
                    (not ALERT_CFG.get("block_downtrend") or not any(p in r.get("pattern", "") for p in ("Downtrend", "Rejection"))) and
                    (not ALERT_CFG.get("require_macd_rising") or r.get("macd_rising", False)) and
                    (not ALERT_CFG.get("require_vol_spike") or r.get("vol_spike", False)) and
                    (not ALERT_CFG.get("block_1h_downtrend", True) or
                     not ("BUY" in sig and r.get("trend_1h") == "down")) and
                    (not ALERT_CFG.get("crash_cooldown", True) or
                     not ("BUY" in sig and any(
                         (c["open"] - c["close"]) / c["open"] * 100 >= ALERT_CFG.get("crash_pct", 8.0)
                         for c in r.get("candles", [])[-3:] if c["open"] > 0)))
                )

                # Would safety have blocked it?
                safety_blocked = False
                safety_reason  = ""
                if alert_fired and "BUY" in sig:
                    ok, reason = check_trade_safety(r, trades)
                    if not ok:
                        safety_blocked = True
                        safety_reason  = reason
                        safety_mark_symbol_blocked(r.get("symbol", ""), r.get("price", 0))

                writer.writerow({
                    "timestamp":    now,
                    "symbol":       r.get("symbol", ""),
                    "price":        round(r.get("price", 0), 8),
                    "change_24h":   round(r.get("change", 0), 2),
                    "signal":       sig,
                    "confidence":   r.get("signal_conf", 1),
                    "rsi":          round(rsi, 1),
                    "stoch_rsi":    round(r.get("stoch_rsi", 0), 1),
                    "macd_hist":    round(r.get("macd_hist", 0), 6),
                    "bb_pct":       round(bb_pct_raw, 1),
                    "bb_width_pct": round(r.get("bb_width_pct", 0), 2),
                    "vol_ratio":    round(r.get("vol_ratio", 0), 2),
                    "vol_spike":    r.get("vol_spike", False),
                    "pattern":      r.get("pattern", ""),
                    "long_score":   r.get("long_score", 0),
                    "short_score":  r.get("short_score", 0),
                    "potential":    round(pot, 1),
                    "exp_move":     round(exp, 2),
                    "trend_1h":     r.get("trend_1h", ""),
                    "adr_pct":      round(r.get("adr_pct", 0), 2),
                    "alert_fired":  alert_fired,
                    "safety_blocked": safety_blocked,
                    "safety_reason":  safety_reason,
                    "price_30m": "", "pct_30m": "",
                    "price_1h":  "", "pct_1h":  "",
                    "price_4h":  "", "pct_4h":  "",
                    "outcome":   "",
                })
    except Exception as e:
        pass  # never crash the app due to logging
