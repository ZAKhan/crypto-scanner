import time as _time
from datetime import datetime as _dt

import requests as _req

from cs.config import CFG


SAFETY_CFG = {
    "signal_persistence":       True,
    "btc_trend_check":          True,
    "btc_drop_pct":             2.0,
    "btc_drop_cooldown_mins":   60,
    "btc_recovery_pct":         1.5,
    "trend_1h_freshness":       True,
    "trend_1h_stale_pct":       1.5,
    "symbol_recovery_gate":     True,
    "symbol_recovery_pct":      1.0,
    "symbol_recovery_expiry_mins": 30,
    "max_open_trades":          True,
    "max_open_trades_count":    3,
    "daily_loss_limit":         True,
    "daily_loss_amount":        100.0,
    "coin_trend_check":         True,
    "coin_drop_pct":            30.0,  # block coins down more than this % in 24h
}

_daily_loss_tracker      = {"date": "", "loss": 0.0}
_spike_cooldown_tracker  = {}   # symbol -> datetime of spike detection
_crash_cooldown_tracker  = {}   # symbol -> datetime of crash detection

_btc_drop_state = {
    "active":       False,
    "trigger_time": 0.0,
    "drop_low":     float("inf"),
}
_symbol_block_state = {}
_coin_alert_tracker = {}   # symbol -> {"time": datetime, "price": float, "signal": str}


def _get_symbol_block(symbol: str) -> dict:
    if symbol not in _symbol_block_state:
        _symbol_block_state[symbol] = {"blocked": False, "block_time": 0.0, "block_price": 0.0}
    return _symbol_block_state[symbol]


def check_trade_safety(r, trades, balance_usdt=0.0):
    """
    Run all enabled safety checks before placing a trade.
    Returns (allowed: bool, reason: str)
    """
    sym     = r.get("symbol", "")
    signal  = r.get("signal", "")
    is_long = "BUY" in signal.upper()
    now_ts  = _time.time()

    # Layer 5 — Signal persistence
    if SAFETY_CFG["signal_persistence"]:
        conf = r.get("signal_conf", 1)
        if conf < 2:
            return False, f"Signal not confirmed yet ({conf}/2 scans)"

    # Fix 1 — BTC drop cooldown
    if SAFETY_CFG["btc_trend_check"] and sym != "BTCUSDT":
        try:
            r2        = _req.get(CFG["base_url"] + "/api/v3/ticker/24hr",
                                 params={"symbol": "BTCUSDT"}, timeout=5).json()
            btc_chg   = float(r2.get("priceChangePercent", 0))
            btc_price = float(r2.get("lastPrice", 0))
            cooldown_secs = SAFETY_CFG.get("btc_drop_cooldown_mins", 60) * 60
            recovery_pct  = SAFETY_CFG.get("btc_recovery_pct", 1.5)

            if _btc_drop_state["active"] and btc_price < _btc_drop_state["drop_low"]:
                _btc_drop_state["drop_low"] = btc_price
            if _btc_drop_state["active"]:
                elapsed   = now_ts - _btc_drop_state["trigger_time"]
                low       = _btc_drop_state["drop_low"]
                recovered = (btc_price - low) / low * 100 if low > 0 else 0
                if elapsed >= cooldown_secs or recovered >= recovery_pct:
                    _btc_drop_state["active"] = False
            if not _btc_drop_state["active"] and btc_chg < -SAFETY_CFG["btc_drop_pct"]:
                _btc_drop_state["active"]       = True
                _btc_drop_state["trigger_time"] = now_ts
                _btc_drop_state["drop_low"]     = btc_price
            if _btc_drop_state["active"] and is_long:
                elapsed_min = (now_ts - _btc_drop_state["trigger_time"]) / 60
                low         = _btc_drop_state["drop_low"]
                recovered   = (btc_price - low) / low * 100 if low > 0 else 0
                return False, (
                    f"BTC drop cooldown — {elapsed_min:.0f}/{SAFETY_CFG.get('btc_drop_cooldown_mins', 60)} min  "
                    f"| BTC recovery from low: {recovered:.1f}% (need {recovery_pct}%)"
                )
        except Exception:
            pass

    # Fix 2 — 1h trend freshness
    if is_long and SAFETY_CFG.get("trend_1h_freshness", True) and r.get("trend_1h") == "up":
        stale_threshold = SAFETY_CFG.get("trend_1h_stale_pct", 1.5)
        price = r.get("price", 0)
        try:
            kl = _req.get(CFG["base_url"] + "/api/v3/klines",
                          params={"symbol": sym, "interval": "1h", "limit": 2},
                          timeout=5).json()
            if kl and len(kl) >= 1:
                open_1h = float(kl[-1][1])
                if open_1h > 0:
                    pct_from_1h = (price - open_1h) / open_1h * 100
                    if pct_from_1h <= -stale_threshold:
                        return False, (
                            f"trend_1h='up' is stale — {sym} is {pct_from_1h:.1f}% below its 1h open "
                            f"(threshold: -{stale_threshold}%)"
                        )
        except Exception:
            pass

    # Fix 3 — Per-symbol recovery gate
    if is_long and SAFETY_CFG.get("symbol_recovery_gate", True):
        sb = _get_symbol_block(sym)
        if sb["blocked"]:
            expiry_secs   = SAFETY_CFG.get("symbol_recovery_expiry_mins", 30) * 60
            recovery_need = SAFETY_CFG.get("symbol_recovery_pct", 1.0)
            elapsed       = now_ts - sb["block_time"]
            cur_price     = r.get("price", 0)
            block_price   = sb["block_price"]
            recovered_pct = (cur_price - block_price) / block_price * 100 if block_price > 0 else 0
            if elapsed >= expiry_secs or recovered_pct >= recovery_need:
                sb["blocked"] = False
            else:
                return False, (
                    f"{sym} recovery gate active — need +{recovery_need}% from block price, "
                    f"currently {recovered_pct:+.2f}%  "
                    f"({elapsed/60:.0f}/{SAFETY_CFG.get('symbol_recovery_expiry_mins', 30)} min)"
                )

    # Layer 2 — Coin 24h trend
    if SAFETY_CFG["coin_trend_check"]:
        chg_24h = r.get("change", 0)
        # If Binance returns 0 but RSI is deeply oversold and coin is in freefall,
        # treat the 0 as unreliable — check kline-based trend instead
        if chg_24h == 0:
            try:
                candles = r.get("candles", [])
                if len(candles) >= 20:
                    chg_kline = (candles[-1]["close"] - candles[0]["close"]) / candles[0]["close"] * 100
                    if chg_kline < -SAFETY_CFG["coin_drop_pct"]:
                        return False, f"{sym} kline trend {chg_kline:.1f}% — freefall (24h=0 unreliable)"
            except Exception:
                pass
        if chg_24h < -SAFETY_CFG["coin_drop_pct"]:
            return False, f"{sym} down {chg_24h:.1f}% in 24h — downtrend"

    # Layer 3 — Max open trades
    if SAFETY_CFG["max_open_trades"]:
        open_count = sum(1 for t in trades if t.get("status") == "OPEN")
        if open_count >= SAFETY_CFG["max_open_trades_count"]:
            return False, f"Max open trades reached ({open_count}/{SAFETY_CFG['max_open_trades_count']})"

    # Layer 4 — Daily loss limit
    if SAFETY_CFG["daily_loss_limit"]:
        today = _dt.now().strftime("%Y-%m-%d")
        if _daily_loss_tracker["date"] != today:
            _daily_loss_tracker["date"] = today
            _daily_loss_tracker["loss"] = 0.0
        if _daily_loss_tracker["loss"] >= SAFETY_CFG["daily_loss_amount"]:
            return False, f"Daily loss limit reached (${_daily_loss_tracker['loss']:.2f})"

    return True, ""


def safety_mark_symbol_blocked(symbol: str, price: float):
    sb = _get_symbol_block(symbol)
    sb["blocked"]     = True
    sb["block_time"]  = _time.time()
    sb["block_price"] = price


def record_trade_loss(pnl: float):
    if pnl < 0:
        today = _dt.now().strftime("%Y-%m-%d")
        if _daily_loss_tracker["date"] != today:
            _daily_loss_tracker["date"] = today
            _daily_loss_tracker["loss"] = 0.0
        _daily_loss_tracker["loss"] += abs(pnl)
