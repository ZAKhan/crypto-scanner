"""
cs/momentum.py — Momentum (Upward Surge) Detector
===================================================
Completely standalone background thread.
Does NOT share state with surge.py or alerts.py.
Only fires when:
  - 5m candle volume >= vol_5m_mult × average
  - 24h price change >= min_chg_pct  (coin moving UP)
  - RSI < max_rsi                    (not completely blown out)
  - Price < max_price ($1)
  - 24h volume >= min_vol_usdt

Emits momentum_alert(dict) signal consumed by the Momentum tab.
"""

import statistics
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from cs.api import fetch_all_tickers, fetch_klines
from cs.config import CFG
from cs.indicators import calc_rsi


MOMENTUM_CFG = {
    "enabled":        True,
    "interval_sec":   30,        # scan interval
    "vol_5m_mult":    3.0,       # 5m candle must be Nx average
    "min_chg_pct":    2.0,       # coin must be up at least this % on 24h
    "max_rsi":        85.0,      # skip if RSI already blown out
    "min_vol_usdt":   500_000,   # minimum 24h volume
    "max_price_pct":  100.0,     # skip coins up more than this % on 24h
    "candle_limit":   20,
    "max_candidates": 5,         # max kline fetches per tick
    "cooldown_mins":  20,        # per-coin cooldown
    "sound":          True,      # play sound on alert
    "desktop":        True,      # desktop notification on alert
}

_momentum_last_alert = {}   # symbol -> datetime of last momentum alert


class MomentumDetector(QObject):
    """
    Background thread detecting upward momentum surges.
    Completely independent from VolumeSurgeDetector.
    """
    momentum_alert = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self._thread  = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            if MOMENTUM_CFG["enabled"]:
                try:
                    self._check()
                except Exception:
                    pass
            for _ in range(MOMENTUM_CFG["interval_sec"] * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _check(self):
        try:
            tickers = fetch_all_tickers()
        except Exception:
            return

        blocklist = CFG.get("symbol_blocklist", set())

        candidates = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if sym in blocklist:
                continue
            try:
                price   = float(t["lastPrice"])
                vol_24h = float(t["quoteVolume"])
                chg_pct = float(t["priceChangePercent"])
            except Exception:
                continue

            if price <= 0 or price >= CFG["max_price"]:
                continue
            if vol_24h < MOMENTUM_CFG["min_vol_usdt"]:
                continue

            # Directional filter — must be moving UP
            if chg_pct < MOMENTUM_CFG["min_chg_pct"]:
                continue
            if chg_pct > MOMENTUM_CFG["max_price_pct"]:
                continue

            candidates.append({
                "symbol":  sym,
                "price":   price,
                "chg_pct": chg_pct,
                "vol_24h": vol_24h,
            })

        if not candidates:
            return

        # Sort by 24h price change descending — strongest movers first
        candidates.sort(key=lambda x: x["chg_pct"], reverse=True)

        fired = 0
        for c in candidates:
            if fired >= MOMENTUM_CFG.get("max_candidates", 5):
                break
            try:
                sym = c["symbol"]

                # Per-coin cooldown
                _cooldown = MOMENTUM_CFG.get("cooldown_mins", 20)
                _last = _momentum_last_alert.get(sym)
                if _last is not None:
                    _elapsed = (datetime.now() - _last).total_seconds() / 60
                    if _elapsed < _cooldown:
                        continue

                raw = fetch_klines(sym, "5m", MOMENTUM_CFG["candle_limit"])
                if not raw or len(raw) < 3:
                    continue

                candles = [{
                    "close": float(k[4]),
                    "vol":   float(k[5]),
                } for k in raw]

                vols     = [cv["vol"] for cv in candles]
                avg_vol  = statistics.mean(vols[:-1]) if len(vols) > 1 else vols[0]
                last_vol = vols[-1]

                if avg_vol <= 0:
                    continue

                vol_ratio = round(last_vol / avg_vol, 1)

                # Primary trigger: volume spike
                if vol_ratio < MOMENTUM_CFG["vol_5m_mult"]:
                    continue

                rsi = calc_rsi([cv["close"] for cv in candles])

                # RSI gate — skip completely blown out coins
                if rsi > MOMENTUM_CFG["max_rsi"]:
                    continue

                alert = {
                    "time":      datetime.now().strftime("%H:%M:%S"),
                    "symbol":    sym.replace("USDT", ""),
                    "signal":    "MOMENTUM",
                    "price":     c["price"],
                    "chg_pct":   round(c["chg_pct"], 2),
                    "vol_5m_x":  vol_ratio,
                    "vol_24h":   c["vol_24h"],
                    "rsi":       round(rsi, 1),
                    "momentum":  True,
                }
                self.momentum_alert.emit(alert)
                _momentum_last_alert[sym] = datetime.now()
                fired += 1
                time.sleep(0.15)

            except Exception:
                continue
