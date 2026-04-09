import json
import os
import statistics
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from cs.api import fetch_all_tickers, fetch_klines, fetch_trend_1h
from cs.config import CFG
from cs.indicators import calc_rsi, analyse, market_context


SURGE_CFG = {
    "enabled":            True,
    "interval_sec":       30,        # how often to check (seconds)
    # ── Primary trigger: 5m candle volume vs average ──────────
    "vol_5m_mult":        3.0,       # last 5m candle must be Nx avg 5m vol
    # ── Price gates ───────────────────────────────────────────
    "max_price_pct":      100.0,     # skip coins already up more than this % on 24h
    "min_price_pct":      0.5,       # skip if price barely moved
    # ── Volume floor ──────────────────────────────────────────
    "min_vol_usdt":       500_000,   # coin must have at least this 24h volume
    "candle_limit":       20,        # candles to fetch for confirmation
    "max_candidates":     100,       # top candidates to evaluate per cycle (5× main scanner pool)
    "cooldown_mins":      30,        # per-coin cooldown between surge alerts
    "min_chg_pct":       -20.0,      # pre-filter: skip coins crashed > 20% on 24h
    "candle_chg_min":     1.0,       # 5m candle must be moving UP >= 1% to trigger
    "min_rsi":            25,        # skip deeply oversold (likely dumping)
    "max_rsi":            75,        # skip overbought — avoids blown-out tops
}

# Per-coin memory: symbol -> last seen 24h quoteVolume
_surge_vol_memory = {}
_surge_last_alert = {}   # symbol -> datetime of last surge alert

def _load_surge_cooldowns():
    """Load persisted cooldown timestamps from disk on startup."""
    try:
        from cs.config import APP_DATA_DIR
        path = os.path.join(APP_DATA_DIR, "surge_cooldowns.json")
        if not os.path.exists(path):
            return
        with open(path) as f:
            data = json.load(f)
        for sym, ts in data.items():
            _surge_last_alert[sym] = datetime.fromisoformat(ts)
    except Exception:
        pass

def _save_surge_cooldowns():
    """Persist cooldown timestamps to disk."""
    try:
        from cs.config import APP_DATA_DIR
        path = os.path.join(APP_DATA_DIR, "surge_cooldowns.json")
        data = {sym: dt.isoformat() for sym, dt in _surge_last_alert.items()}
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

_load_surge_cooldowns()


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
            if chg_pct < SURGE_CFG["min_chg_pct"]:  # pre-filter: skip crashed coins
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

        # Sort by 24h volume descending — highest liquidity candidates first
        candidates.sort(key=lambda x: x["vol_24h"], reverse=True)

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

                # Candle change: current 5m candle must be moving UP >= 1%
                last_c     = candles[-1]
                candle_chg = ((last_c["close"] - last_c["open"]) / last_c["open"] * 100
                              if last_c["open"] > 0 else 0.0)
                if candle_chg < SURGE_CFG["candle_chg_min"]:
                    continue   # candle not moving up

                # RSI gate — skip if oversold or overbought
                rsi = calc_rsi([cv["close"] for cv in candles])
                if rsi < SURGE_CFG["min_rsi"] or rsi > SURGE_CFG["max_rsi"]:
                    continue

                # Per-coin cooldown — skip if alerted recently
                _cooldown_mins = SURGE_CFG["cooldown_mins"]
                _last_alert = _surge_last_alert.get(sym)
                if _last_alert is not None:
                    _elapsed = (datetime.now() - _last_alert).total_seconds() / 60
                    if _elapsed < _cooldown_mins:
                        continue

                # ── SURGE PROMOTION ──────────────────────────────────────────
                # Run full analysis (70 candles) and check alert criteria.
                # If passes → STRONG BUY (surge_promoted=True) to Alerts tab.
                # If fails  → VOLUME SURGE (informational) to Surges tab.
                promoted   = False
                promo_data = None
                try:
                    raw70      = fetch_klines(sym, "5m", CFG["candle_limit"])
                    t1h        = fetch_trend_1h(sym)
                    promo_data = analyse(sym, raw70, c["chg_pct"], trend_1h=t1h)
                    promo_data["trend_1h"] = t1h

                    ctx = market_context(promo_data["candles"])
                    if ctx["structure_score"] > -1:
                        _pat = promo_data.get("pattern", "")
                        _pat_ok = (
                            "Doji"         not in _pat and
                            _pat           != "Neutral" and
                            not any(p in _pat for p in (
                                "Vol Spike ↓", "Shooting Star",
                                "Bearish Engulf", "Downtrend", "Rejection"
                            ))
                        )
                        # Crash check: no single candle in last 3 dropped >= 8% body
                        _candles70 = promo_data.get("candles", [])
                        _no_crash  = all(
                            (c_["open"] - c_["close"]) / c_["open"] * 100 < 8.0
                            for c_ in _candles70[-3:] if c_["open"] > 0
                        )
                        criteria_pass = (
                            promo_data.get("rsi",       50) <= 70   and
                            promo_data.get("bb_pos",    50) <= 70   and
                            promo_data.get("potential",  0) >= 50   and
                            promo_data.get("vol_ratio",  0) >= 1.0  and
                            promo_data.get("macd_rising", False)     and
                            promo_data.get("trend_1h", "flat") != "down" and
                            candle_chg >= SURGE_CFG["candle_chg_min"] and  # ADR substitute
                            _pat_ok and _no_crash
                        )
                        if criteria_pass:
                            promoted = True
                except Exception:
                    promoted = False

                if promoted and promo_data:
                    alert = {
                        "time":          datetime.now().strftime("%H:%M:%S"),
                        "symbol":        sym.replace("USDT", ""),
                        "signal":        "STRONG BUY",
                        "price":         c["price"],
                        "chg_pct":       round(c["chg_pct"], 2),
                        "vol_24h_x":     round(c["vol_24h"] / max(_surge_vol_memory.get(sym, c["vol_24h"]), 1), 1),
                        "vol_5m_x":      vol_ratio_5m,
                        "rsi":           promo_data.get("rsi", rsi),
                        "exp":           round(candle_chg, 2),
                        "pot":           promo_data.get("potential", 0),
                        "vol":           vol_ratio_5m,
                        "pattern":       promo_data.get("pattern", "—"),
                        "macd_rising":   promo_data.get("macd_rising", False),
                        "surge_promoted": True,
                        "surge":         True,
                    }
                else:
                    alert = {
                        "time":          datetime.now().strftime("%H:%M:%S"),
                        "symbol":        sym.replace("USDT", ""),
                        "signal":        "VOLUME SURGE",
                        "price":         c["price"],
                        "chg_pct":       round(c["chg_pct"], 2),
                        "vol_24h_x":     round(c["vol_24h"] / max(_surge_vol_memory.get(sym, c["vol_24h"]), 1), 1),
                        "vol_5m_x":      vol_ratio_5m,
                        "rsi":           rsi,
                        "exp":           round(candle_chg, 2),
                        "pot":           min(int(vol_ratio_5m * 10), 100),
                        "vol":           vol_ratio_5m,
                        "pattern":       f"5m vol {vol_ratio_5m}x  |  candle +{candle_chg:.1f}%",
                        "surge_promoted": False,
                        "surge":         True,
                    }

                self.surge_alert.emit(alert)
                _surge_last_alert[sym] = datetime.now()
                _save_surge_cooldowns()
                fired += 1
                time.sleep(0.15)
            except Exception:
                continue
