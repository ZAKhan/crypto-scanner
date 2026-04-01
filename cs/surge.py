import statistics
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from cs.api import fetch_all_tickers, fetch_klines
from cs.config import CFG
from cs.indicators import calc_rsi


SURGE_CFG = {
    "enabled":            True,
    "interval_sec":       30,        # how often to check (seconds)
    # ── Primary trigger: 5m candle volume vs average ──────────
    "vol_5m_mult":        3.0,       # last 5m candle must be Nx avg 5m vol
    # ── Price gates ───────────────────────────────────────────
    "max_price_pct":      30.0,      # skip coins already up more than this %
    "min_price_pct":      0.5,       # skip if price barely moved
    # ── Volume floor ──────────────────────────────────────────
    "min_vol_usdt":       500_000,   # coin must have at least this 24h volume
    "candle_limit":       20,        # candles to fetch for confirmation
    "max_candidates":     10,        # max coins to confirm per tick
    "cooldown_mins":      60,        # per-coin cooldown between surge alerts
}

# Per-coin memory: symbol -> last seen 24h quoteVolume
_surge_vol_memory = {}
_surge_last_alert = {}   # symbol -> datetime of last surge alert


class VolumeSurgeDetector(QObject):
    """
    Background thread that detects volume surges on ANY coin under $1.
    Primary trigger: last 5m candle volume > N× average 5m volume.
    Secondary gate: 24h volume grew vs last check (confirms real activity).
    Emits surge_alert(dict) when a genuine surge is found.
    """
    surge_alert = pyqtSignal(dict)

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
            if SURGE_CFG["enabled"]:
                try:
                    self._check()
                except Exception:
                    pass
            for _ in range(SURGE_CFG["interval_sec"] * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _check(self):
        try:
            tickers = fetch_all_tickers()
        except Exception:
            return

        blocklist = CFG.get("symbol_blocklist", set())

        # Build candidate list from tickers — loose gates only
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
            if vol_24h < SURGE_CFG["min_vol_usdt"]:
                continue
            if chg_pct > SURGE_CFG["max_price_pct"]:
                continue

            # Update 24h vol memory (keep for reference, no longer a gate)
            _surge_vol_memory[sym] = vol_24h

            candidates.append({
                "symbol":  sym,
                "price":   price,
                "chg_pct": chg_pct,
                "vol_24h": vol_24h,
            })

        if not candidates:
            return

        # For each candidate, fetch 5m candles and use vol_ratio as primary trigger
        fired = 0
        for c in candidates:
            if fired >= SURGE_CFG.get("max_candidates", 10):
                break
            try:
                sym = c["symbol"]
                raw = fetch_klines(sym, "5m", SURGE_CFG["candle_limit"])
                if not raw or len(raw) < 3:
                    continue

                candles = [{"open": float(k[1]), "high": float(k[2]),
                            "low":  float(k[3]), "close": float(k[4]),
                            "vol":  float(k[5])} for k in raw]
                vols     = [cv["vol"] for cv in candles]
                # Use all candles except last as baseline average
                avg_vol  = statistics.mean(vols[:-1]) if len(vols) > 1 else vols[0]
                last_vol = vols[-1]

                if avg_vol <= 0:
                    continue

                vol_ratio_5m = round(last_vol / avg_vol, 1)

                # PRIMARY TRIGGER: last 5m candle volume vs average
                if vol_ratio_5m < SURGE_CFG["vol_5m_mult"]:
                    continue   # not a surge on 5m timeframe

                # Per-coin cooldown — skip if alerted recently
                _cooldown_mins = SURGE_CFG.get("cooldown_mins", 60)
                _last_alert = _surge_last_alert.get(sym)
                if _last_alert is not None:
                    _elapsed = (datetime.now() - _last_alert).total_seconds() / 60
                    if _elapsed < _cooldown_mins:
                        continue

                rsi = calc_rsi([cv["close"] for cv in candles])

                alert = {
                    "time":      datetime.now().strftime("%H:%M:%S"),
                    "symbol":    sym.replace("USDT", ""),
                    "signal":    "VOLUME SURGE",
                    "price":     c["price"],
                    "chg_pct":   round(c["chg_pct"], 2),
                    "vol_24h_x": round(c["vol_24h"] / max(_surge_vol_memory.get(sym, c["vol_24h"]), 1), 1),
                    "vol_5m_x":  vol_ratio_5m,
                    "rsi":       rsi,
                    # Fields expected by alert log / notification systems
                    "exp":       c["chg_pct"],
                    "pot":       min(int(vol_ratio_5m * 10), 100),
                    "vol":       vol_ratio_5m,
                    "pattern":   f"5m vol {vol_ratio_5m}x  |  price +{c['chg_pct']:.1f}%",
                    "surge":     True,
                }
                self.surge_alert.emit(alert)
                _surge_last_alert[sym] = datetime.now()
                fired += 1
                time.sleep(0.15)
            except Exception:
                continue
