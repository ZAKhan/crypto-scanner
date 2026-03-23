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
    "interval_sec":       60,        # how often to check (matches alert engine)
    "volume_mult":        3.0,       # vol must grow by this multiple in one tick
    "max_price_pct":      8.0,       # skip if price already up more than this %
    "min_price_pct":      1.0,       # skip if price barely moved (not a real surge)
    "min_vol_usdt":       200_000,   # coin must have at least this 24h volume now
    "candle_limit":       20,        # candles to fetch for surge candidates
}

# Per-coin volume memory: symbol -> last seen quoteVolume
_surge_vol_memory = {}   # symbol -> float

class VolumeSurgeDetector(QObject):
    """
    Background thread that detects volume surges on ANY coin under $1,
    not just the top-30 scan list. Emits surge_alert(dict) when a
    genuine surge is found.
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
        """Fetch all tickers, compare volumes, fire surges."""
        try:
            tickers = fetch_all_tickers()
        except Exception:
            return

        surges = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                price   = float(t["lastPrice"])
                vol_24h = float(t["quoteVolume"])
                chg_pct = float(t["priceChangePercent"])
            except Exception:
                continue

            # Basic gates
            if price <= 0 or price >= CFG["max_price"]:
                continue
            if vol_24h < SURGE_CFG["min_vol_usdt"]:
                continue

            prev_vol = _surge_vol_memory.get(sym)
            _surge_vol_memory[sym] = vol_24h   # always update memory

            if prev_vol is None or prev_vol <= 0:
                continue   # first time seeing this coin — no comparison yet

            vol_ratio = vol_24h / prev_vol

            # Volume must have grown significantly in this tick
            if vol_ratio < SURGE_CFG["volume_mult"]:
                continue

            # Price must have moved — but not too much already
            if chg_pct < SURGE_CFG["min_price_pct"]:
                continue
            if chg_pct > SURGE_CFG["max_price_pct"]:
                continue   # already moved too far — likely too late

            surges.append({
                "symbol":    sym,
                "price":     price,
                "chg_pct":   chg_pct,
                "vol_24h":   vol_24h,
                "vol_ratio": round(vol_ratio, 1),
            })

        if not surges:
            return

        # For each surge candidate, fetch candles to confirm
        for s in surges[:5]:   # cap at 5 per tick to avoid API hammering
            try:
                sym   = s["symbol"]
                raw   = fetch_klines(sym, "5m", SURGE_CFG["candle_limit"])
                if not raw or len(raw) < 3:
                    continue
                candles   = [{"open": float(k[1]), "high": float(k[2]),
                              "low":  float(k[3]), "close": float(k[4]),
                              "vol":  float(k[5])} for k in raw]
                vols      = [c["vol"] for c in candles]
                avg_vol   = statistics.mean(vols[:-1]) if len(vols) > 1 else vols[0]
                last_vol  = vols[-1]
                vol_ratio_5m = round(last_vol / avg_vol, 1) if avg_vol > 0 else 0

                rsi = calc_rsi([c["close"] for c in candles])

                alert = {
                    "time":       datetime.now().strftime("%H:%M:%S"),
                    "symbol":     sym.replace("USDT", ""),
                    "signal":     "VOLUME SURGE",
                    "price":      s["price"],
                    "chg_pct":    round(s["chg_pct"], 2),
                    "vol_24h_x":  s["vol_ratio"],    # how much 24h vol grew
                    "vol_5m_x":   vol_ratio_5m,      # last 5m candle vs avg
                    "rsi":        rsi,
                    # Fields expected by notification/log systems
                    "exp":        s["chg_pct"],
                    "pot":        min(int(s["vol_ratio"] * 10), 100),
                    "vol":        vol_ratio_5m,
                    "pattern":    f"24h vol {s['vol_ratio']}x  |  price +{s['chg_pct']:.1f}%",
                    "surge":      True,              # flag for UI to colour differently
                }
                self.surge_alert.emit(alert)
                time.sleep(0.15)   # small pause between kline fetches
            except Exception:
                continue
