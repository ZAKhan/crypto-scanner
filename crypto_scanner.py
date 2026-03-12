#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║        CRYPTO SCALPER SCANNER  v9  — Binance            ║
║   Price < $1  |  Vol > 1M  |  5m Analysis  |  Qt GUI   ║
╠══════════════════════════════════════════════════════════╣
║  Standalone — no external .py files needed              ║
║  Requires : pip install PyQt6 requests                  ║
║  Run      : python3 crypto_scanner_v9.py                ║
╚══════════════════════════════════════════════════════════╝
"""

import sys
import os
import struct
import math
import tempfile
import json
import time
import subprocess
import statistics
import threading
import requests
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QLabel, QPushButton,
    QFrame, QHeaderView, QAbstractItemView, QProgressBar, QTabWidget,
    QScrollArea, QGridLayout, QSizePolicy, QSpacerItem, QGroupBox,
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox,
    QStatusBar, QToolBar, QMessageBox, QDialog, QMenu
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize, QPropertyAnimation,
    QEasingCurve, pyqtProperty, QObject, QSettings, QByteArray, QUrl
)
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QBrush, QLinearGradient,
    QPainter, QPen, QIcon, QAction, QFontDatabase,
    QShortcut, QKeySequence, QDesktopServices
)
try:
    from PyQt6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
    HAS_CHARTS = True
except ImportError:
    HAS_CHARTS = False

# ─────────────────────────────────────────────────────────
#  CONFIG  (edit these to change scan behaviour)
# ─────────────────────────────────────────────────────────
CFG = {
    "max_price":       1.0,
    "min_volume_usdt": 1_000_000,
    "interval":        "5m",
    "candle_limit":    50,
    "top_n":           30,
    "rsi_period":      14,
    "base_url":        "https://api.binance.com",
    # ── Risk Management ──────────────────────────────
    "sl_pct":             3.0,   # Stop Loss %
    "tp_pct":             5.0,   # Take Profit %
    "tp2_pct":           10.0,   # TP2 (extended target) %
    "min_expected_move":  2.0,   # Filter: only show coins expected to move >2% (lower = more results)
}

# ─────────────────────────────────────────────────────────
#  COLOUR PAIRS
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
#  BINANCE API
# ─────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────
#  TECHNICAL ANALYSIS
# ─────────────────────────────────────────────────────────
def ema(values, period):
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def calc_rsi(closes, period=14):
    if len(closes) < period + 2:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

def calc_macd(closes, fast=12, slow=26, sig=9):
    if len(closes) < slow + sig:
        return 0.0, 0.0, 0.0, []
    ef = ema(closes, fast)
    es = ema(closes, slow)
    n  = min(len(ef), len(es))
    ml = [ef[i] - es[i] for i in range(n)]
    sl = ema(ml, sig)
    if not sl:
        return ml[-1], 0.0, ml[-1], [ml[-1]]
    # Build last 3 histogram values to detect rising/falling momentum
    offset = len(ml) - len(sl)
    hist_series = [round(ml[i] - sl[i - offset], 8)
                   for i in range(max(offset, len(ml) - 3), len(ml))]
    hist = ml[-1] - sl[-1]
    return round(ml[-1], 8), round(sl[-1], 8), round(hist, 8), hist_series

def calc_bollinger(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None, None, None
    win = closes[-period:]
    mid = sum(win) / period
    std = statistics.stdev(win)
    return round(mid + mult * std, 6), round(mid, 6), round(mid - mult * std, 6)

def calc_stoch_rsi(closes, period=14):
    if len(closes) < period * 2:
        return 50.0
    rsi_vals = [calc_rsi(closes[:i+1], period) for i in range(period, len(closes))]
    if len(rsi_vals) < period:
        return 50.0
    win = rsi_vals[-period:]
    lo, hi = min(win), max(win)
    if hi == lo:
        return 50.0
    return round((rsi_vals[-1] - lo) / (hi - lo) * 100, 2)

def detect_pattern(candles):
    if len(candles) < 5:
        return "—"
    closes = [c["close"] for c in candles]
    vols   = [c["vol"]   for c in candles]
    avg_v  = statistics.mean(vols[:-3]) if len(vols) > 3 else vols[0]
    last   = candles[-1]
    prev   = candles[-2]
    body   = abs(last["close"] - last["open"])
    rng    = last["high"] - last["low"]
    ratio  = body / rng if rng > 0 else 0
    green  = last["close"] > last["open"]
    red    = not green
    vspike = last["vol"] > avg_v * 2
    lw = min(last["open"], last["close"]) - last["low"]
    uw = last["high"] - max(last["open"], last["close"])
    if ratio < 0.12:
        return "Doji"
    if lw > body * 2 and uw < body and green:
        return "Hammer ↑"
    if uw > body * 2 and lw < body and red:
        return "Shooting Star ↓"
    if (green and prev["close"] < prev["open"]
            and last["close"] > prev["open"]
            and last["open"] < prev["close"]):
        return "Bullish Engulf ↑"
    if (red and prev["close"] > prev["open"]
            and last["close"] < prev["open"]
            and last["open"] > prev["close"]):
        return "Bearish Engulf ↓"
    last5 = max(closes[-5:]) - min(closes[-5:])
    avg_r = statistics.mean([c["high"] - c["low"] for c in candles[-20:]])
    if last5 < avg_r * 0.4:
        return "Squeeze →"
    if vspike and green:
        return "Vol Spike ↑"
    if vspike and red:
        return "Vol Spike ↓"
    if all(closes[i] >= closes[i-1] for i in range(-4, 0)):
        return "Uptrend ↑"
    if all(closes[i] <= closes[i-1] for i in range(-4, 0)):
        return "Downtrend ↓"
    return "Neutral"

def score_signal(rsi, macd_h, price, bb_upper, bb_lower, bb_mid, pattern,
                 change_24h=0.0, stoch_rsi=50.0,
                 vol_ratio=1.0, macd_rising=False, bb_width_pct=0.0):
    """
    Confluence scoring — quality over quantity.
    Key improvements over v2:
    - MACD freshness: rising histogram = stronger signal than stale positive
    - BB width penalty: very wide bands mean position is less meaningful
    - Contra-pattern penalty: bearish pattern on a long signal reduces score
    - Score margin: signal must clearly win, not just edge out by 1
    - RSI: still contributes from 45 down, but deeper = more points
    Targets 5-12 actionable signals per 30-coin scan.
    """
    long_score  = 0
    short_score = 0

    # ── RSI ────────────────────────────────────────────
    # Full range contributes, but deeper extremes score much higher
    if rsi < 25:    long_score += 5
    elif rsi < 30:  long_score += 4
    elif rsi < 35:  long_score += 3
    elif rsi < 40:  long_score += 2
    elif rsi < 45:  long_score += 1   # mild oversold — still counts, just weakly
    # 45-55: neutral zone — no score either direction
    if rsi > 75:    short_score += 5
    elif rsi > 70:  short_score += 4
    elif rsi > 65:  short_score += 3
    elif rsi > 60:  short_score += 2
    elif rsi > 55:  short_score += 1  # mild overbought

    # ── Stochastic RSI ──────────────────────────────────
    if stoch_rsi < 20:  long_score  += 2
    elif stoch_rsi < 40: long_score += 1
    if stoch_rsi > 80:  short_score += 2
    elif stoch_rsi > 60: short_score+= 1

    # ── MACD — weight by freshness (key v3 improvement) ─
    # Fresh/rising momentum is much more reliable than stale positive
    if macd_h > 0:
        long_score  += 3 if macd_rising else 1   # rising=strong, stale=weak
    elif macd_h < 0:
        short_score += 3 if not macd_rising else 1

    # ── Bollinger Band position ─────────────────────────
    if bb_lower and bb_upper and bb_upper > bb_lower:
        pos = (price - bb_lower) / (bb_upper - bb_lower)
        # Reduce BB contribution when bands are very wide (> 12% of price)
        # Wide bands = lower band is too far from any real support level
        bb_mult = 0.5 if bb_width_pct > 12 else 1.0

        if pos < 0.10:    long_score  += int(3 * bb_mult)
        elif pos < 0.25:  long_score  += int(2 * bb_mult)
        elif pos < 0.40:  long_score  += int(1 * bb_mult)

        if pos > 0.90:    short_score += int(3 * bb_mult)
        elif pos > 0.75:  short_score += int(2 * bb_mult)
        elif pos > 0.60:  short_score += int(1 * bb_mult)

    # ── Candlestick pattern ─────────────────────────────
    # Confirming pattern = +2, contra-direction pattern = -1 penalty
    BULLISH = ["Hammer", "Bullish Engulf", "Vol Spike ↑", "Uptrend"]
    BEARISH  = ["Shooting Star", "Bearish Engulf", "Vol Spike ↓", "Downtrend"]
    for p in BULLISH:
        if p in pattern:
            long_score  += 2
            short_score -= 1  # pattern contradicts short signal
            break
    for p in BEARISH:
        if p in pattern:
            short_score += 2
            long_score  -= 1
            break
    if "Squeeze" in pattern:
        if rsi < 45: long_score  += 1   # tight range + oversold = coiled spring
        if rsi > 55: short_score += 1

    # ── 24h momentum ───────────────────────────────────
    # Strong up move = short candidate; moderate dip = mild long bonus
    if change_24h > 15:    short_score += 2
    elif change_24h > 8:   short_score += 1
    elif change_24h < -10: long_score  += 1   # oversold on day = bounce candidate
    # Note: heavy bleeding (-20%+) does NOT get extra bonus — may be in freefall

    # ── Clip negatives ──────────────────────────────────
    long_score  = max(0, long_score)
    short_score = max(0, short_score)

    # ── Determine signal with margin check ──────────────
    # Margin requirement: winning side must beat loser by >= 2 for BUY,
    # >= 3 for STRONG. Prevents marginal ambiguous signals.
    margin = abs(long_score - short_score)

    if long_score > short_score:
        if   long_score >= 6 and margin >= 3: return "STRONG BUY",  "green",  long_score, short_score
        elif long_score >= 3 and margin >= 2: return "BUY",          "green",  long_score, short_score
        else:                                  return "NEUTRAL",      "yellow", long_score, short_score
    elif short_score > long_score:
        if   short_score >= 6 and margin >= 3: return "STRONG SELL", "red",    long_score, short_score
        elif short_score >= 3 and margin >= 2: return "SELL",         "red",    long_score, short_score
        else:                                   return "NEUTRAL",     "yellow", long_score, short_score
    else:
        return "NEUTRAL", "yellow", long_score, short_score


def profit_potential(r):
    """
    Score 0-100 indicating how much immediate profit potential this coin has.
    Combines signal strength, volume momentum, BB squeeze, trend alignment,
    and 5m price momentum. Higher = more likely to move soon and fast.
    """
    score = 0
    sig = r["signal"]

    # Base from signal strength
    if "STRONG" in sig:  score += 30
    elif "BUY" in sig or "SELL" in sig: score += 15

    # Volume ratio — recent candle vs average (momentum)
    vr = r.get("vol_ratio", 1)
    if vr > 3:    score += 25
    elif vr > 2:  score += 18
    elif vr > 1.5:score += 12
    elif vr > 1:  score += 6

    # BB position — closer to band edge = more room to move
    bbu, bbl = r.get("bb_upper"), r.get("bb_lower")
    price = r["price"]
    if bbu and bbl and bbu != bbl:
        pos = (price - bbl) / (bbu - bbl)
        if "BUY" in sig:
            # for longs: lower = more potential (room to run up)
            score += int((1 - pos) * 20)
        else:
            # for shorts: higher = more potential (room to fall)
            score += int(pos * 20)

    # RSI extremity — more extreme = more elastic snap back potential
    rsi = r["rsi"]
    if "BUY" in sig:
        if rsi < 25:    score += 15
        elif rsi < 35:  score += 10
        elif rsi < 45:  score += 5
    else:
        if rsi > 75:    score += 15
        elif rsi > 65:  score += 10
        elif rsi > 55:  score += 5

    # MACD histogram — confirmed direction = higher potential
    mh = r.get("macd_hist", 0)
    if ("BUY" in sig and mh > 0) or ("SELL" in sig and mh < 0):
        score += 10

    # Stoch RSI extremity
    srsi = r.get("stoch_rsi", 50)
    if "BUY" in sig and srsi < 20:    score += 10
    elif "SELL" in sig and srsi > 80: score += 10

    return min(score, 100)


def calc_expected_move(candles, signal):
    """
    Estimate expected % move based on:
    - ATR (Average True Range) over last 14 candles — measures actual volatility
    - BB width — how wide the current range is
    - Recent momentum — how fast price has been moving
    Returns expected move as a percentage of current price.
    """
    if len(candles) < 15:
        return 0.0

    closes = [c["close"] for c in candles]
    price  = closes[-1]

    # ATR
    trs = []
    for i in range(1, len(candles)):
        high  = candles[i]["high"]
        low   = candles[i]["low"]
        prev_c= candles[i-1]["close"]
        tr    = max(high - low, abs(high - prev_c), abs(low - prev_c))
        trs.append(tr)
    atr14 = statistics.mean(trs[-14:])
    atr_pct = (atr14 / price) * 100

    # BB width as % of price
    bb_width_pct = 0.0
    bbu = candles[-1].get("bb_upper")  # not stored on candle, calc inline
    # recalc BB from closes
    if len(closes) >= 20:
        win = closes[-20:]
        mid = statistics.mean(win)
        std = statistics.stdev(win)
        bb_width_pct = (std * 4 / price) * 100   # full band width / price

    # Momentum: avg candle body size over last 5 candles as % of price
    bodies = [abs(c["close"] - c["open"]) for c in candles[-5:]]
    momentum_pct = (statistics.mean(bodies) / price) * 100

    # Projected move = weighted combo
    # ATR is the most reliable measure, BB gives context, momentum shows current speed
    expected = (atr_pct * 0.5) + (bb_width_pct * 0.3) + (momentum_pct * 0.2)

    # Scale up slightly for strong signals — strong momentum = bigger move
    if "STRONG" in signal:
        expected *= 1.4
    elif "BUY" in signal or "SELL" in signal:
        expected *= 1.1

    return round(expected, 2)


def analyse(symbol, raw_klines, change_24h=0.0):
    candles = [{"open": float(k[1]), "high": float(k[2]),
                "low":  float(k[3]), "close": float(k[4]),
                "vol":  float(k[5])} for k in raw_klines]
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    vols   = [c["vol"]   for c in candles]
    rsi              = calc_rsi(closes, CFG["rsi_period"])
    stoch_rsi        = calc_stoch_rsi(closes, CFG["rsi_period"])
    macd, msig, mh, macd_hist_series = calc_macd(closes)
    bbu, bbm, bbl    = calc_bollinger(closes)
    pattern          = detect_pattern(candles)
    avg_vol          = statistics.mean(vols)
    vol_ratio        = vols[-1] / avg_vol if avg_vol else 0

    # MACD rising: histogram is growing in the last 2 candles
    macd_rising = False
    if len(macd_hist_series) >= 2:
        macd_rising = macd_hist_series[-1] > macd_hist_series[-2]

    # BB width as % of price — wide bands = less reliable BB position signal
    bb_width_pct = 0.0
    if bbu and bbl and closes[-1] > 0:
        bb_width_pct = (bbu - bbl) / closes[-1] * 100

    signal, sig_clr, long_sc, short_sc = score_signal(
        rsi, mh, closes[-1], bbu, bbl, bbm, pattern, change_24h, stoch_rsi,
        vol_ratio=vol_ratio, macd_rising=macd_rising, bb_width_pct=bb_width_pct)

    result = {
        "price":        closes[-1],
        "rsi":          rsi,
        "stoch_rsi":    stoch_rsi,
        "macd":         macd,
        "macd_sig":     msig,
        "macd_hist":    mh,
        "macd_rising":  macd_rising,
        "bb_upper":     bbu,
        "bb_mid":       bbm,
        "bb_lower":     bbl,
        "bb_width_pct": round(bb_width_pct, 2),
        "support":      round(min(lows[-15:]), 6),
        "resist":       round(max(highs[-15:]), 6),
        "avg_vol":      avg_vol,
        "last_vol":     vols[-1],
        "vol_ratio":    vol_ratio,
        "pattern":      pattern,
        "signal":       signal,
        "sig_clr":      sig_clr,
        "long_score":   long_sc,
        "short_score":  short_sc,
        "candles":      candles,
    }
    result["potential"]     = profit_potential(result)
    result["expected_move"] = calc_expected_move(candles, signal)
    return result

# ─────────────────────────────────────────────────────────
#  BACKGROUND SCANNER THREAD
# ─────────────────────────────────────────────────────────
class Scanner:
    def __init__(self):
        self.results   = []
        self.status    = "Ready — press S to scan"
        self.scanning  = False
        self.progress  = (0, 0)
        self.last_scan = None
        self._lock     = threading.Lock()

    def start_scan(self):
        if self.scanning:
            return
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        self.scanning = True
        self.results  = []
        try:
            self.status = "Fetching 24h ticker data from Binance..."
            tickers = fetch_all_tickers()
            filtered = []
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                try:
                    price = float(t["lastPrice"])
                    vol   = float(t["quoteVolume"])
                    chg   = float(t["priceChangePercent"])
                except Exception:
                    continue
                if 0 < price < CFG["max_price"] and vol > CFG["min_volume_usdt"]:
                    filtered.append({"symbol": sym, "price": price,
                                     "volume": vol, "change": chg})
            filtered.sort(key=lambda x: x["volume"], reverse=True)
            filtered = filtered[:CFG["top_n"]]
            results   = []
            errors    = []
            total     = len(filtered)
            for i, coin in enumerate(filtered):
                sym = coin["symbol"]
                self.status   = f"Analysing {sym} ({i+1}/{total})..."
                self.progress = (i + 1, total)
                try:
                    raw  = fetch_klines(sym, CFG["interval"], CFG["candle_limit"])
                    data = analyse(sym, raw, coin["change"])
                    data["symbol"]     = sym
                    data["trend_1h"]   = fetch_trend_1h(sym)
                    data["volume_24h"] = coin["volume"]
                    data["change_24h"] = coin["change"]
                    results.append(data)
                    time.sleep(0.07)
                except Exception as e:
                    errors.append(f"{sym}:{e}")

            if not results:
                self.status = f"No results. Errors: {errors[:3]}"
                return

            # Filter: remove NEUTRAL coins with very low potential — not actionable
            # Vol quality is reflected in Potential% score, not a hard gate here
            all_results  = results
            results      = [r for r in all_results
                            if r["signal"] != "NEUTRAL" or r.get("potential", 0) >= 25]
            filtered_out = len(all_results) - len(results)
            # Fallback: if everything filtered, show all
            if not results:
                results      = all_results
                filtered_out = 0

            # Sort: STRONG BUY → STRONG SELL → BUY → SELL → NEUTRAL
            # Within each tier: highest expected_move first, then potential
            def sort_key(r):
                sig = r["signal"]
                if   sig == "STRONG BUY":  tier = 0
                elif sig == "STRONG SELL": tier = 1
                elif sig == "BUY":         tier = 2
                elif sig == "SELL":        tier = 3
                else:                      tier = 4
                return (tier, -r.get("expected_move", 0), -r.get("potential", 0))

            results.sort(key=sort_key)
            with self._lock:
                self.results   = results
                self.last_scan = datetime.now().strftime("%H:%M:%S")
            self.status = f"Done — {len(results)} coins  (dropped {filtered_out} neutral/weak)  [{self.last_scan}]"
        except Exception as e:
            self.status = f"Error: {e}"
        finally:
            self.scanning = False

    def get_results(self):
        with self._lock:
            return list(self.results)


# ─────────────────────────────────────────────────────────────
#  ALERT ENGINE
#  Runs in background, detects new breakout signals, fires alerts
# ─────────────────────────────────────────────────────────────

# Global alert config — updated by GUI settings
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
}

# ─────────────────────────────────────────────────────────────
#  SOUND ENGINE — Pure Python WAV generation, zero dependencies
# ─────────────────────────────────────────────────────────────

def _make_wav(tone_sequence, sample_rate=44100, volume=0.85):
    """
    Generate WAV bytes from a list of (freq_hz, duration_sec).
    Use freq=0 for silence gaps between beeps.
    Pure stdlib — no external libraries needed.
    """
    samples = []
    for freq, dur in tone_sequence:
        n = int(sample_rate * dur)
        for i in range(n):
            if freq == 0:
                samples.append(0.0)
            else:
                t = i / sample_rate
                # Smooth envelope: 5ms attack, 10ms release — avoids clicks
                env = 1.0
                atk = int(0.005 * sample_rate)
                rel = int(0.010 * sample_rate)
                if i < atk:
                    env = i / atk
                elif i > n - rel:
                    env = (n - i) / rel
                samples.append(math.sin(2 * math.pi * freq * t) * env * volume)

    pcm = struct.pack(
        f"<{len(samples)}h",
        *[max(-32768, min(32767, int(s * 32767))) for s in samples]
    )
    data_size = len(pcm)
    byte_rate  = sample_rate * 2        # mono 16-bit
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate,
        byte_rate, 2, 16,
        b"data", data_size
    )
    return header + pcm


def _write_temp_wav(wav_bytes):
    """Write WAV bytes to a named temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="scanner_alert_")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(wav_bytes)
    return path


def _play_wav(path):
    """
    Play a WAV file using the best available player.
    Tries: ffplay → aplay → paplay → pw-play.
    Non-blocking — fires and forgets.
    """
    players = [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
        ["aplay",  "-q", path],
        ["paplay", path],
        ["pw-play", path],
    ]
    for cmd in players:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return  # First one that launches wins
        except FileNotFoundError:
            continue  # Try next player


# Pre-bake all 4 alert WAVs at import time — stored in memory as temp files.
# Generated once, reused for every alert. Deleted on process exit.
_SOUNDS = {}

def _init_sounds():
    """Generate and cache all alert WAVs. Called once at startup."""
    global _SOUNDS
    definitions = {
        # STRONG BUY: 3-beep aggressive ascending burst
        "STRONG BUY": [
            (880, 0.14), (0, 0.04),
            (1100, 0.14), (0, 0.04),
            (1320, 0.24),
        ],
        # STRONG SELL: 3-beep aggressive descending burst
        "STRONG SELL": [
            (1100, 0.14), (0, 0.04),
            (880,  0.14), (0, 0.04),
            (660,  0.24),
        ],
        # BUY: clean double ascending
        "BUY": [
            (700, 0.16), (0, 0.05),
            (950, 0.22),
        ],
        # SELL: clean double descending
        "SELL": [
            (950, 0.16), (0, 0.05),
            (700, 0.22),
        ],
    }
    for name, seq in definitions.items():
        try:
            wav   = _make_wav(seq)
            path  = _write_temp_wav(wav)
            _SOUNDS[name] = path
        except Exception as e:
            _SOUNDS[name] = None   # Graceful fallback — never crash at startup

_init_sounds()   # Run once when module loads


class AlertEngine(QObject):
    """
    Background alert engine.
    - Auto-scans on a timer
    - Compares new results against last scan
    - Fires desktop / sound / telegram for NEW signals only
    """
    new_alert   = pyqtSignal(dict)   # emits alert dict to GUI log
    scan_done   = pyqtSignal(list)   # emits results to update table

    def __init__(self):
        super().__init__()
        self._scanner       = Scanner()
        self._last_signals  = {}    # symbol -> signal string from last scan
        self._signal_age    = {}    # symbol -> datetime when current signal first appeared
        self._signal_conf   = {}    # symbol -> consecutive scan count holding same signal
        self._thread        = None
        self._running       = False
        self._timer         = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def trigger_now(self):
        """Force an immediate scan (called when user presses Scan button)."""
        threading.Thread(target=self._run_scan, daemon=True).start()

    def _loop(self):
        while self._running:
            self._run_scan()
            # Sleep in small increments so stop() is responsive
            for _ in range(ALERT_CFG["interval_sec"] * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _run_scan(self):
        if self._scanner.scanning:
            return
        self._scanner.start_scan()
        # Wait for scan to finish
        while self._scanner.scanning:
            time.sleep(0.2)
        results = self._scanner.get_results()
        if results:
            self._check_alerts(results)   # inject age/conf BEFORE emitting to table
            self.scan_done.emit(results)

    def _check_alerts(self, results):
        now = datetime.now()

        # ── Update age / confirmation tracking for every result ──────────
        for r in results:
            sym  = r["symbol"]
            sig  = r["signal"]
            prev = self._last_signals.get(sym, "NEUTRAL")

            if sig == "NEUTRAL":
                # Signal gone — reset age & conf
                self._signal_age.pop(sym, None)
                self._signal_conf[sym] = 0
            elif sig != prev:
                # New or changed signal — reset age & conf
                self._signal_age[sym]  = now
                self._signal_conf[sym] = 1
            else:
                # Same signal as last scan — increment confirmation
                if sym not in self._signal_age:
                    self._signal_age[sym] = now
                self._signal_conf[sym] = self._signal_conf.get(sym, 0) + 1

            # Inject into result dict so table can display them
            r["signal_conf"] = self._signal_conf.get(sym, 1)
            r["signal_age"]  = self._signal_age.get(sym, now)

        if not ALERT_CFG["enabled"]:
            for r in results:
                self._last_signals[r["symbol"]] = r["signal"]
            return

        sig_order = {"STRONG BUY": 0, "STRONG SELL": 1, "BUY": 2, "SELL": 3, "NEUTRAL": 4}
        min_level = sig_order.get(ALERT_CFG["min_signal"], 2)

        for r in results:
            sym    = r["symbol"]
            sig    = r["signal"]
            prev   = self._last_signals.get(sym, "NEUTRAL")
            level  = sig_order.get(sig, 4)
            pot    = r.get("potential", 0)
            exp    = r.get("expected_move", 0)

            # Only alert on NEW signals (wasn't a signal before, or upgraded)
            is_new = (sig != "NEUTRAL" and
                      (prev == "NEUTRAL" or sig_order.get(prev, 4) > level))
            passes = (level <= min_level and
                      pot >= ALERT_CFG["min_potential"] and
                      exp >= ALERT_CFG["min_exp_move"])

            if is_new and passes:
                alert = {
                    "time":    now.strftime("%H:%M:%S"),
                    "symbol":  sym.replace("USDT",""),
                    "signal":  sig,
                    "price":   r["price"],
                    "rsi":     r["rsi"],
                    "exp":     exp,
                    "pot":     pot,
                    "pattern": r.get("pattern","—"),
                    "vol":     r.get("vol_ratio", 0),
                    "macd_rising": r.get("macd_rising", False),
                }
                self.new_alert.emit(alert)
                self._fire(alert)

        # Update last known signals
        for r in results:
            self._last_signals[r["symbol"]] = r["signal"]

    def _fire(self, a):
        """Fire all enabled notification channels."""
        sym  = a["symbol"]
        sig  = a["signal"]
        msg  = (f"{sig}: {sym}  ${a['price']:.5f}\n"
                f"RSI {a['rsi']:.0f}  Exp {a['exp']:.1f}%  Pot {a['pot']}%  "
                f"Vol {a['vol']:.1f}x  {a['pattern']}")

        if ALERT_CFG["sound"]:
            self._play_sound(sig)

        if ALERT_CFG["desktop"]:
            self._desktop_notify(sig, sym, msg)

        if ALERT_CFG["telegram"] and ALERT_CFG["tg_token"] and ALERT_CFG["tg_chat_id"]:
            self._telegram(sig, sym, a)

        if ALERT_CFG["whatsapp"] and ALERT_CFG["wa_number"]:
            self._whatsapp_via_picoclaw(sig, sym, a)

    def _play_sound(self, signal):
        """Play the pre-generated WAV for this signal type."""
        # Match signal string to one of our 4 sound keys
        if "STRONG" in signal and "BUY" in signal:
            key = "STRONG BUY"
        elif "STRONG" in signal:
            key = "STRONG SELL"
        elif "BUY" in signal:
            key = "BUY"
        else:
            key = "SELL"

        path = _SOUNDS.get(key)
        if path and os.path.exists(path):
            _play_wav(path)

    def _desktop_notify(self, signal, symbol, body):
        """Send desktop notification via notify-send — urgency=critical stays until dismissed."""
        try:
            is_buy    = "BUY" in signal
            is_strong = "STRONG" in signal
            icon      = "dialog-information" if is_buy else "dialog-warning"
            urgency   = "critical" if is_strong else "normal"
            timeout   = "0" if is_strong else "12000"   # 0 = stays until clicked
            title     = f"🚀 {signal} — {symbol}" if is_buy else f"🔴 {signal} — {symbol}"
            subprocess.Popen(
                ["notify-send",
                 "-i", icon,
                 "-u", urgency,   # critical = red border, stays on screen
                 "-t", timeout,
                 title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            pass  # notify-send not installed — skip silently

    def _telegram(self, signal, symbol, a):
        """Send Telegram message via Bot API."""
        try:
            emoji = "🟢" if "BUY" in signal else "🔴"
            macd_txt = "Fresh ✅" if a["macd_rising"] == ("BUY" in signal) else "Stale ⚠️"
            text = (
                f"{emoji} *{signal}* — `{a['symbol']}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 Price:    `${a['price']:.5f}`\n"
                f"📊 RSI:      `{a['rsi']:.1f}`\n"
                f"📈 Exp Move: `{a['exp']:.1f}%`\n"
                f"⭐ Potential: `{a['pot']}%`\n"
                f"📦 Volume:   `{a['vol']:.1f}x avg`\n"
                f"🕯 Pattern:  `{a['pattern']}`\n"
                f"⚡ MACD:    `{macd_txt}`\n"
                f"🕐 Time:    `{a['time']}`"
            )
            requests.post(
                f"https://api.telegram.org/bot{ALERT_CFG['tg_token']}/sendMessage",
                json={"chat_id": ALERT_CFG["tg_chat_id"], "text": text,
                      "parse_mode": "Markdown"},
                timeout=5
            )
        except Exception:
            pass

    def _whatsapp_via_picoclaw(self, signal, symbol, a):
        """
        Send WhatsApp alert via PicoClaw.

        Strategy: write a pending alert entry into a JSON queue file that
        PicoClaw's HEARTBEAT task reads every minute and delivers via WhatsApp.

        Queue file: ~/.picoclaw/workspace/crypto_alerts.json
        Format:     list of { "to": "...", "text": "...", "sent": false }

        PicoClaw HEARTBEAT.md reads this file and sends pending entries using
        the picoclaw `message` tool, then marks them sent.
        """
        try:
            emoji  = "🟢" if "BUY" in signal else "🔴"
            strong = "STRONG" in signal
            macd_ok = (a.get("macd_rising", False) and "BUY" in signal) or \
                      (not a.get("macd_rising", True) and "SELL" in signal)
            macd_txt = "Fresh ✅" if macd_ok else "Stale ⚠️"

            lines = [
                f"{emoji} *{'🚨 ' if strong else ''}{signal}* — {symbol}",
                f"━━━━━━━━━━━━━━━",
                f"💰 Price:    ${a['price']:.6f}",
                f"📊 RSI:      {a['rsi']:.1f}",
                f"📈 Exp Move: {a['exp']:.1f}%",
                f"⭐ Potential: {a['pot']}%",
                f"📦 Volume:   {a['vol']:.1f}x avg",
                f"🕯 Pattern:  {a['pattern']}",
                f"⚡ MACD:    {macd_txt}",
                f"🕐 {a['time']}  |  Binance Spot",
            ]
            text = "\n".join(lines)

            queue_path = ALERT_CFG["picoclaw_queue"]
            os.makedirs(os.path.dirname(queue_path), exist_ok=True)

            # Load existing queue safely
            queue = []
            if os.path.exists(queue_path):
                try:
                    with open(queue_path, "r") as f:
                        queue = json.load(f)
                    if not isinstance(queue, list):
                        queue = []
                except Exception:
                    queue = []

            # Append new pending alert
            queue.append({
                "to":   ALERT_CFG["wa_number"],
                "text": text,
                "sent": False,
                "ts":   datetime.now().isoformat(),
            })

            # Keep only last 50 entries (sent + unsent) to avoid unbounded growth
            queue = queue[-50:]

            with open(queue_path, "w") as f:
                json.dump(queue, f, indent=2)

        except Exception:
            pass  # Never crash on notification failure



# ─────────────────────────────────────────────────────────────
DARK  = "#0a0e1a"
DARK2 = "#0f1525"
PANEL = "#131929"
CARD  = "#1a2235"
BORDER= "#1e2d47"
ACCENT= "#00d4ff"
GREEN = "#00ff88"
RED   = "#ff3366"
YELLOW= "#ffcc00"
WHITE = "#e8f0fe"
DIM   = "#4a5568"
STRONG_BUY_BG  = "#002a1a"
STRONG_SELL_BG = "#2a0010"
BUY_BG         = "#001a10"
SELL_BG        = "#1a000a"

# ── Cross-platform monospace font stack ──────────────────────────────────────
# Arch Linux built-in:  DejaVu Sans Mono, Liberation Mono
# Windows built-in:     Cascadia Code (Win11), Consolas (Vista+), Courier New
# macOS built-in:       SF Mono (10.12+), Menlo (10.6+), Monaco
# Common dev install:   JetBrains Mono, Fira Code, Hack, Source Code Pro
# Final fallback:       "monospace" Qt/CSS generic hint (always resolves)
MONO_FAMILIES = [
    # Preferred — popular dev fonts, available on all platforms if installed
    "JetBrains Mono", "Fira Code", "Fira Mono", "Cascadia Code", "Cascadia Mono",
    "Source Code Pro", "Hack", "Iosevka", "Inconsolata",
    # Linux guaranteed
    "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", "Noto Mono",
    # macOS guaranteed
    "SF Mono", "Menlo", "Monaco", "Andale Mono",
    # Windows guaranteed
    "Consolas", "Lucida Console", "Courier New",
    # Generic Qt hint — must be last, always resolves to something monospaced
    "monospace",
]

# For Qt stylesheets (font-family CSS property)
MONO_CSS = (
    "'JetBrains Mono','Fira Code','Fira Mono','Cascadia Code','Cascadia Mono',"
    "'Source Code Pro','Hack','Ubuntu Mono','DejaVu Sans Mono','Liberation Mono',"
    "'Noto Mono','SF Mono','Menlo','Monaco','Consolas','Lucida Console',"
    "'Courier New',monospace"
)

# Single name used where Qt API needs one string (setFamilies handles the rest)
MONO = "JetBrains Mono"

def mono_font(size=10, bold=False):
    """
    Return a QFont using the full cross-platform monospace fallback chain.
    Qt walks MONO_FAMILIES in order and uses the first one found on the system.
    StyleHint.Monospace + setFixedPitch(True) ensure a fixed-width font is
    always selected even if none of the named families are installed.
    """
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setPointSize(size)
    f.setBold(bold)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFixedPitch(True)
    return f
SANS  = "Inter,Segoe UI,SF Pro Display,sans-serif"

FONT_SIZE = 13   # default — user can change in Config tab

def make_stylesheet(fs=13):
    """Generate the full app stylesheet at a given base font size."""
    fs0  = fs        # base
    fs_s = fs - 1    # small (labels, headers)
    fs_x = fs - 2    # extra small (hints, status)
    fs_l = fs + 1    # large (buttons, titles)
    fs_h = fs + 5    # heading (symbol name in detail)
    return f"""
QMainWindow, QWidget {{
    background-color: {DARK};
    color: {WHITE};
    font-family: {SANS};
    font-size: {fs0}px;
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {DARK2};
    border-radius: 6px;
}}
QTabBar::tab {{
    background: {PANEL};
    color: {DIM};
    padding: 8px 20px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    font-weight: 600;
    font-size: {fs_s}px;
    letter-spacing: 0.5px;
}}
QTabBar::tab:selected {{
    background: {CARD};
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover:!selected {{
    color: {WHITE};
    background: {CARD};
}}

QTableWidget {{
    background-color: {DARK2};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: {MONO_CSS};
    font-size: {fs_s}px;
    selection-background-color: #1a3a5c;
    selection-color: {WHITE};
    outline: none;
}}
QTableWidget::item {{
    padding: 6px 10px;
    border-bottom: 1px solid {BORDER};
}}
QTableWidget::item:hover {{
    background-color: #162030;
}}
QHeaderView::section {{
    background-color: {PANEL};
    color: {ACCENT};
    padding: 8px 10px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 2px solid {ACCENT};
    font-family: {SANS};
    font-size: {fs_x}px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QHeaderView::section:hover {{
    background-color: {CARD};
    color: {WHITE};
}}

QPushButton {{
    background-color: {CARD};
    color: {WHITE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: {fs0}px;
    letter-spacing: 0.3px;
}}
QPushButton:hover {{
    background-color: {BORDER};
    border-color: {ACCENT};
    color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: {DARK};
}}
QPushButton#scanBtn {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0066aa, stop:1 #00aacc);
    color: white;
    border: none;
    padding: 10px 30px;
    font-size: {fs_l}px;
    font-weight: 700;
    letter-spacing: 1px;
    border-radius: 6px;
    min-width: 120px;
}}
QPushButton#scanBtn:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0088cc, stop:1 #00ccee);
}}
QPushButton#scanBtn:disabled {{
    background: {DIM};
    color: #888;
}}

QProgressBar {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT}, stop:1 #0066ff);
    border-radius: 3px;
}}

QLabel#statusLabel {{
    color: {DIM};
    font-size: {fs_x}px;
    padding: 2px 8px;
}}
QLabel#titleLabel {{
    color: {ACCENT};
    font-size: {fs_h}px;
    font-weight: 800;
    letter-spacing: 2px;
    font-family: {MONO_CSS};
}}
QLabel#subtitleLabel {{
    color: {DIM};
    font-size: {fs_x}px;
    letter-spacing: 1px;
}}

QFrame#cardFrame {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 12px;
}}
QFrame#accentCard {{
    background: {CARD};
    border: 1px solid {ACCENT};
    border-left: 3px solid {ACCENT};
    border-radius: 8px;
    padding: 12px;
}}

QGroupBox {{
    color: {ACCENT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 16px;
    font-weight: 700;
    font-size: {fs_x}px;
    letter-spacing: 1px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 10px;
}}

QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit {{
    background: {PANEL};
    color: {WHITE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 10px;
    font-family: {MONO_CSS};
    font-size: {fs_s}px;
    min-width: 80px;
}}
QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QLineEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    color: {WHITE};
    border: 1px solid {BORDER};
    selection-background-color: {CARD};
}}

QScrollBar:vertical {{
    background: {DARK2};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QStatusBar {{
    background: {PANEL};
    color: {DIM};
    border-top: 1px solid {BORDER};
    font-size: {fs_x}px;
}}
"""

STYLESHEET = make_stylesheet(FONT_SIZE)


# ─────────────────────────────────────────────────────────────
#  WORKER THREAD
# ─────────────────────────────────────────────────────────────
class ScanWorker(QThread):
    progress  = pyqtSignal(int, int, str)   # done, total, status
    finished  = pyqtSignal(list)            # results list
    error     = pyqtSignal(str)

    def __init__(self, scanner):
        super().__init__()
        self._scanner = scanner

    def run(self):
        self._scanner.start_scan()
        while self._scanner.scanning:
            done, total = self._scanner.progress
            self.progress.emit(done, total, self._scanner.status)
            time.sleep(0.2)
        results = self._scanner.get_results()
        if results:
            self.finished.emit(results)
        else:
            self.error.emit(self._scanner.status)

# ─────────────────────────────────────────────────────────────
#  SIGNAL BADGE
# ─────────────────────────────────────────────────────────────
class SignalBadge(QLabel):
    COLORS = {
        "STRONG BUY":  (GREEN,  STRONG_BUY_BG,  "#00ff88"),
        "BUY":         (GREEN,  BUY_BG,          "#00cc66"),
        "STRONG SELL": (RED,    STRONG_SELL_BG,  "#ff3366"),
        "SELL":        (RED,    SELL_BG,          "#cc2244"),
        "NEUTRAL":     (DIM,    CARD,             DIM),
    }

    def __init__(self, signal_text):
        super().__init__(signal_text)
        fg, bg, border = self.COLORS.get(signal_text, (WHITE, CARD, BORDER))
        bold = "800" if "STRONG" in signal_text else "600"
        self.setStyleSheet(f"""
            QLabel {{
                color: {fg};
                background: {bg};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 3px 8px;
                font-weight: {bold};
                font-size: 11px;
                font-family: {MONO_CSS};
                letter-spacing: 0.5px;
            }}
        """)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

# ─────────────────────────────────────────────────────────────
#  SPARKLINE WIDGET
# ─────────────────────────────────────────────────────────────
class Sparkline(QWidget):
    def __init__(self, values, color=GREEN, parent=None):
        super().__init__(parent)
        self.values = values
        self.color  = QColor(color)
        self.setFixedSize(80, 28)

    def paintEvent(self, event):
        if not self.values or len(self.values) < 2:
            return
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        lo   = min(self.values)
        hi   = max(self.values)
        rng  = hi - lo or 1e-9
        pts  = [(i / (len(self.values) - 1) * w,
                 h - (v - lo) / rng * (h - 4) - 2)
                for i, v in enumerate(self.values)]
        pen  = QPen(self.color, 1.5)
        p.setPen(pen)
        for i in range(len(pts) - 1):
            p.drawLine(int(pts[i][0]), int(pts[i][1]),
                       int(pts[i+1][0]), int(pts[i+1][1]))
        # dot at end
        p.setBrush(QBrush(self.color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(pts[-1][0]) - 3, int(pts[-1][1]) - 3, 6, 6)

# ─────────────────────────────────────────────────────────────
#  MINI BAR (RSI / StochRSI)
# ─────────────────────────────────────────────────────────────
class MiniBar(QWidget):
    def __init__(self, value, lo_good=True, parent=None):
        super().__init__(parent)
        self.value   = max(0, min(100, value))
        self.lo_good = lo_good
        self.setFixedSize(70, 14)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # track
        p.setBrush(QBrush(QColor(BORDER)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 3, w, h - 6, 3, 3)
        # fill
        v = self.value / 100
        if self.lo_good:
            c = QColor(GREEN) if self.value < 40 else QColor(RED) if self.value > 60 else QColor(YELLOW)
        else:
            c = QColor(RED) if self.value < 40 else QColor(GREEN) if self.value > 60 else QColor(YELLOW)
        p.setBrush(QBrush(c))
        p.drawRoundedRect(0, 3, int(w * v), h - 6, 3, 3)

# ─────────────────────────────────────────────────────────────
#  STAT CARD
# ─────────────────────────────────────────────────────────────
class StatCard(QFrame):
    def __init__(self, label, value, color=WHITE, parent=None):
        super().__init__(parent)
        self.setObjectName("cardFrame")
        lay = QVBoxLayout(self)
        lay.setSpacing(4)
        lay.setContentsMargins(14, 10, 14, 10)

        lbl = QLabel(label.upper())
        lbl.setStyleSheet(f"color:{DIM}; font-size:10px; font-weight:700; letter-spacing:1px;")

        self.val_lbl = QLabel(value)
        self.val_lbl.setStyleSheet(f"color:{color}; font-size:18px; font-weight:800; font-family:{MONO_CSS};")

        lay.addWidget(lbl)
        lay.addWidget(self.val_lbl)

    def set_value(self, value, color=None):
        self.val_lbl.setText(value)
        if color:
            self.val_lbl.setStyleSheet(
                f"color:{color}; font-size:18px; font-weight:800; font-family:{MONO_CSS};")

# ─────────────────────────────────────────────────────────────
#  PRICE CHART — pure QPainter candlestick chart, no QtCharts
# ─────────────────────────────────────────────────────────────
class PriceChart(QWidget):
    def __init__(self, candles, parent=None):
        super().__init__(parent)
        self.candles = candles
        self.setFixedHeight(180)
        self.setStyleSheet(f"background:{DARK2}; border-radius:6px;")
        self.setMinimumWidth(300)

    def paintEvent(self, event):
        if not self.candles:
            return
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h  = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 55, 12, 12, 24

        # background
        p.fillRect(0, 0, w, h, QColor(DARK2))

        candles = self.candles
        hi  = max(c["high"]  for c in candles)
        lo  = min(c["low"]   for c in candles)
        rng = hi - lo or 1e-9

        def y(price):
            return pad_t + (hi - price) / rng * (h - pad_t - pad_b)

        n    = len(candles)
        cw   = max(2, (w - pad_l - pad_r) / n - 1)
        gap  = (w - pad_l - pad_r) / n

        # Grid lines + Y labels
        p.setPen(QPen(QColor(BORDER), 1, Qt.PenStyle.DotLine))
        p.setFont(mono_font(8))
        p.setPen(QColor(DIM))
        for frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            price = hi - frac * rng
            yy    = int(y(price))
            p.setPen(QPen(QColor(BORDER), 1, Qt.PenStyle.DotLine))
            p.drawLine(pad_l, yy, w - pad_r, yy)
            p.setPen(QColor(DIM))
            p.drawText(2, yy + 4, f"{price:.5f}")

        # Candles
        for i, c in enumerate(candles):
            x_center = pad_l + i * gap + gap / 2
            x_left   = int(x_center - cw / 2)
            is_green = c["close"] >= c["open"]
            col      = QColor(GREEN) if is_green else QColor(RED)

            # Wick
            p.setPen(QPen(col, 1))
            p.drawLine(int(x_center), int(y(c["high"])),
                       int(x_center), int(y(c["low"])))

            # Body
            body_top = int(y(max(c["open"], c["close"])))
            body_bot = int(y(min(c["open"], c["close"])))
            body_h   = max(1, body_bot - body_top)
            p.fillRect(x_left, body_top, max(1, int(cw)), body_h, col)

        # Current price line
        last_price = candles[-1]["close"]
        yy = int(y(last_price))
        p.setPen(QPen(QColor(ACCENT), 1, Qt.PenStyle.DashLine))
        p.drawLine(pad_l, yy, w - pad_r, yy)
        p.setPen(QColor(ACCENT))
        p.drawText(w - pad_r - 2, yy - 3, f"${last_price:.5f}")

        p.end()


# ─────────────────────────────────────────────────────────────
#  DETAIL PANEL
# ─────────────────────────────────────────────────────────────
class DetailPanel(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet(f"background:{DARK2}; border:none;")
        self._build_ui()

    def _build_ui(self):
        w = QWidget()
        self.setWidget(w)
        self.lay = QVBoxLayout(w)
        self.lay.setSpacing(10)
        self.lay.setContentsMargins(14, 14, 14, 14)
        self.lay.addWidget(QLabel("← Select a coin from the scanner"))
        self.lay.addStretch()

    def load(self, r):
        # Clear
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not r:
            self.lay.addWidget(QLabel("No data"))
            return

        sym    = r["symbol"].replace("USDT", "/USDT")
        price  = r["price"]
        sig    = r["signal"]
        chg    = r["change_24h"]
        rsi    = r["rsi"]
        srsi   = r["stoch_rsi"]
        mh     = r["macd_hist"]
        pot    = r.get("potential", 0)
        exp    = r.get("expected_move", 0)
        chg_c  = GREEN if chg >= 0 else RED
        sig_c  = GREEN if "BUY" in sig else RED if "SELL" in sig else DIM

        # ── Header ──────────────────────────────────────
        hdr = QFrame(); hdr.setObjectName("accentCard")
        hlay = QHBoxLayout(hdr)

        sym_lbl = QLabel(sym)
        sym_lbl.setStyleSheet(f"color:{ACCENT}; font-size:22px; font-weight:800; font-family:{MONO_CSS};")

        price_lbl = QLabel(f"${price:.6f}")
        price_lbl.setStyleSheet(f"color:{WHITE}; font-size:18px; font-weight:700; font-family:{MONO_CSS};")

        chg_lbl = QLabel(f"{chg:+.2f}%")
        chg_lbl.setStyleSheet(f"color:{chg_c}; font-size:16px; font-weight:700;")

        badge = SignalBadge(sig)

        hlay.addWidget(sym_lbl)
        hlay.addWidget(price_lbl)
        hlay.addWidget(chg_lbl)
        hlay.addStretch()
        hlay.addWidget(badge)
        self.lay.addWidget(hdr)

        # ── Stats row ───────────────────────────────────
        stats_w = QWidget()
        slay    = QHBoxLayout(stats_w)
        slay.setSpacing(8)
        slay.setContentsMargins(0, 0, 0, 0)
        pot_c  = GREEN if pot >= 70 else YELLOW if pot >= 40 else RED
        exp_c  = GREEN if exp >= 8  else GREEN  if exp >= 5  else YELLOW
        vr     = r.get("vol_ratio", 0)
        rising = r.get("macd_rising", False)
        # Volume gate thresholds matching scanner rules
        if "STRONG" in sig: vr_needed = 1.8
        elif sig in ("BUY","SELL"): vr_needed = 1.3
        else: vr_needed = 1.0
        vr_c   = GREEN if vr >= vr_needed * 1.3 else YELLOW if vr >= vr_needed else RED
        macd_c = GREEN if (mh > 0 and rising) or (mh < 0 and not rising) else YELLOW
        # 1H trend
        t1h = r.get("trend_1h", "flat")
        t1h_labels = {"up": "↑ Uptrend", "down": "↓ Downtrend", "flat": "→ Sideways"}
        t1h_str    = t1h_labels.get(t1h, "→ Sideways")
        if t1h == "up":
            t1h_col = GREEN if "BUY" in sig else (RED if "SELL" in sig else ACCENT)
        elif t1h == "down":
            t1h_col = RED if "BUY" in sig else (GREEN if "SELL" in sig else RED)
        else:
            t1h_col = DIM
        # Signal age
        age_dt  = r.get("signal_age")
        if age_dt and sig != "NEUTRAL":
            age_s   = int((datetime.now() - age_dt).total_seconds())
            age_str = f"{age_s // 60}m {age_s % 60}s" if age_s >= 60 else f"{age_s}s"
        else:
            age_str = "—"
        # Conf count
        sc      = r.get("signal_conf", 0) if sig != "NEUTRAL" else 0
        sc_col  = ACCENT if sc >= 5 else GREEN if sc >= 3 else YELLOW if sc >= 1 else DIM
        sc_str  = f"{sc} scan{'s' if sc != 1 else ''}"

        stats = [
            ("RSI",         f"{rsi:.1f}",          GREEN if rsi < 40 else RED if rsi > 60 else YELLOW),
            ("Stoch RSI",   f"{srsi:.1f}",          GREEN if srsi < 30 else RED if srsi > 70 else YELLOW),
            ("MACD",        f"{'Fresh ✓' if macd_c==GREEN else 'Stale ⚠'}  {mh:+.5f}", macd_c),
            ("Potential",   f"{pot}%",              pot_c),
            ("Exp Move",    f"{exp:.1f}%",          exp_c),
            ("Vol Ratio",   f"{vr:.2f}x  ({'✓' if vr >= vr_needed else '✗'})", vr_c),
            ("1H Trend",    t1h_str,                t1h_col),
            ("Sig Age",     age_str,                YELLOW if age_str != "—" else DIM),
            ("Confirmed",   sc_str,                 sc_col),
        ]
        for lbl, val, col in stats:
            slay.addWidget(StatCard(lbl, val, col))
        self.lay.addWidget(stats_w)

        # ── Indicators ──────────────────────────────────
        ind_grp = QGroupBox("INDICATORS")
        ind_lay = QGridLayout(ind_grp)
        ind_lay.setSpacing(6)

        def ind_row(row, label, widget, val_text="", col=WHITE):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
            val = QLabel(val_text)
            val.setStyleSheet(f"color:{col}; font-family:{MONO_CSS}; font-size:12px; font-weight:700;")
            ind_lay.addWidget(lbl, row, 0)
            ind_lay.addWidget(widget, row, 1)
            ind_lay.addWidget(val, row, 2)

        ind_row(0, "RSI (14)",    MiniBar(rsi),  f"{rsi:.1f}", GREEN if rsi < 40 else RED if rsi > 60 else YELLOW)
        ind_row(1, "Stoch RSI",   MiniBar(srsi), f"{srsi:.1f}", GREEN if srsi < 30 else RED if srsi > 70 else YELLOW)

        # BB position
        bb_pos_val = 50.0
        if r.get("bb_upper") and r.get("bb_lower") and r["bb_upper"] != r["bb_lower"]:
            bb_pos_val = (price - r["bb_lower"]) / (r["bb_upper"] - r["bb_lower"]) * 100
        ind_row(2, "BB Position", MiniBar(bb_pos_val, lo_good=False),
                f"{bb_pos_val:.0f}%", GREEN if bb_pos_val < 30 else RED if bb_pos_val > 70 else YELLOW)

        macd_lbl = QLabel(f"MACD")
        macd_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        rising   = r.get("macd_rising", False)
        macd_dir = ("▲ Rising" if rising else "▼ Fading") if mh > 0 else \
                   ("▼ Falling" if not rising else "▲ Recovering") if mh < 0 else "— Flat"
        conf_txt = "  ✓ Fresh" if (mh > 0 and rising) or (mh < 0 and not rising) else "  ⚠ Stale"
        conf_col = GREEN if "Fresh" in conf_txt else YELLOW
        macd_val = QLabel(f"{mh:+.8f}  {macd_dir}{conf_txt}")
        macd_val.setStyleSheet(f"color:{GREEN if mh > 0 else RED}; font-family:{MONO_CSS}; font-size:12px; font-weight:700;")
        ind_lay.addWidget(macd_lbl, 3, 0)
        ind_lay.addWidget(macd_val, 3, 1, 1, 2)
        self.lay.addWidget(ind_grp)

        # ── Bollinger Bands ──────────────────────────────
        if r.get("bb_upper"):
            bb_grp = QGroupBox("BOLLINGER BANDS")
            bb_lay = QHBoxLayout(bb_grp)
            for lbl, val, col in [
                ("Lower", r["bb_lower"], GREEN),
                ("Mid",   r["bb_mid"],   YELLOW),
                ("Upper", r["bb_upper"], RED),
            ]:
                c = StatCard(lbl, f"${val:.6f}", col)
                bb_lay.addWidget(c)
            self.lay.addWidget(bb_grp)

        # ── Support / Resistance ─────────────────────────
        sr_grp = QGroupBox("SUPPORT / RESISTANCE")
        sr_lay = QHBoxLayout(sr_grp)
        sr_lay.addWidget(StatCard("Support",    f"${r['support']:.6f}", GREEN))
        sr_lay.addWidget(StatCard("Resistance", f"${r['resist']:.6f}",  RED))
        sr_lay.addWidget(StatCard("Vol 24h",    f"${r['volume_24h']/1e6:.1f}M", ACCENT))
        self.lay.addWidget(sr_grp)

        # ── Trade Setups ─────────────────────────────────
        sl_pct  = CFG["sl_pct"]
        tp_pct  = CFG["tp_pct"]
        tp2_pct = CFG["tp2_pct"]
        rr      = round(tp_pct / sl_pct, 2)

        for setup_name, is_long in [("LONG SETUP", True), ("SHORT SETUP", False)]:
            active = ("BUY" in sig and is_long) or ("SELL" in sig and not is_long)
            border_col = GREEN if is_long else RED
            grp = QGroupBox(f"{'▲' if is_long else '▼'} {setup_name}  (SL {sl_pct}%  /  TP {tp_pct}%)")
            grp.setStyleSheet(f"""
                QGroupBox {{
                    color: {border_col if active else DIM};
                    border: 1px solid {border_col if active else BORDER};
                    border-left: 3px solid {border_col if active else BORDER};
                    border-radius: 6px; margin-top:16px; font-weight:700; font-size:11px;
                }}
                QGroupBox::title {{
                    subcontrol-origin: margin; subcontrol-position: top left;
                    padding: 0 8px; left: 10px;
                }}
            """)
            glay = QHBoxLayout(grp)

            if is_long:
                sl  = round(price * (1 - sl_pct  / 100), 6)
                tp1 = round(price * (1 + tp_pct  / 100), 6)
                tp2 = round(price * (1 + tp2_pct / 100), 6)
            else:
                sl  = round(price * (1 + sl_pct  / 100), 6)
                tp1 = round(price * (1 - tp_pct  / 100), 6)
                tp2 = round(price * (1 - tp2_pct / 100), 6)

            for lbl, val, col in [
                ("Entry",  f"${price:.6f}", WHITE),
                ("Stop Loss", f"${sl:.6f}",  RED),
                ("TP1",    f"${tp1:.6f}", GREEN),
                ("TP2",    f"${tp2:.6f}", GREEN),
                ("R/R",    f"{rr:.2f}x",  YELLOW),
            ]:
                glay.addWidget(StatCard(lbl, val, col))

            self.lay.addWidget(grp)

        # ── Price chart (pure QPainter — no QtCharts needed) ─────
        candles = r.get("candles", [])
        if candles:
            chart_grp = QGroupBox("PRICE  (last 50 candles)")
            chart_lay = QVBoxLayout(chart_grp)
            chart_lay.addWidget(PriceChart(candles))
            self.lay.addWidget(chart_grp)

        # ── Pattern + volume ─────────────────────────────
        pv_grp = QGroupBox("PATTERN / VOLUME")
        pv_lay = QHBoxLayout(pv_grp)
        pv_lay.addWidget(StatCard("Pattern", r["pattern"], ACCENT))
        pv_lay.addWidget(StatCard("Avg Vol", f"{r['avg_vol']:,.0f}", DIM))
        pv_lay.addWidget(StatCard("Last Vol", f"{r['last_vol']:,.0f}",
                                  GREEN if r.get("vol_ratio", 0) > 1.5 else YELLOW))
        self.lay.addWidget(pv_grp)
        self.lay.addStretch()

# ─────────────────────────────────────────────────────────────
#  MAIN WINDOW
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────
#  EQUITY CURVE WIDGET  — pure QPainter, no QtCharts
# ─────────────────────────────────────────────────────────
class _EquityCanvas(QWidget):
    """Draws a cumulative P&L line chart from a list of (label, cumulative_pnl) tuples."""
    def __init__(self):
        super().__init__()
        self._points = []   # list of float cumulative pnl values
        self._labels = []   # list of str trade labels
        self.setMinimumHeight(130)

    def set_data(self, points, labels):
        self._points = points
        self._labels = labels
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 48, 16, 8, 24

        # Background
        p.fillRect(0, 0, W, H, QColor(DARK2))

        pts = self._points
        if not pts:
            p.setPen(QColor(DIM))
            p.drawText(0, 0, W, H, Qt.AlignmentFlag.AlignCenter,
                       "No closed trades yet")
            return

        # Single trade — render as a single labelled dot at centre
        if len(pts) == 1:
            col = QColor(GREEN) if pts[0] >= 0 else QColor(RED)
            cx, cy = W // 2, H // 2
            p.setBrush(QBrush(col)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(cx - 5, cy - 5, 10, 10)
            sign = "+" if pts[0] >= 0 else ""
            p.setPen(col)
            p.setFont(QFont("monospace", 9))
            lbl = f"{sign}{pts[0]:.4f} USDT  ({self._labels[0] if self._labels else ''})"
            p.drawText(0, cy + 12, W, 20, Qt.AlignmentFlag.AlignCenter, lbl)
            sub = "1 closed trade"
            p.setPen(QColor(DIM))
            p.setFont(QFont("monospace", 8))
            p.drawText(0, cy - 28, W, 20, Qt.AlignmentFlag.AlignCenter, sub)
            return

        mn, mx = min(pts), max(pts)
        span = mx - mn if mx != mn else max(abs(mx) * 0.1, 0.0001)
        gW = W - pad_l - pad_r
        gH = H - pad_t - pad_b

        def px(i):  return pad_l + int(i / (len(pts) - 1) * gW)
        def py(v):  return pad_t + int((1 - (v - mn) / span) * gH)

        # Zero line
        if mn < 0 < mx:
            zy = py(0)
            p.setPen(QPen(QColor(BORDER), 1, Qt.PenStyle.DashLine))
            p.drawLine(pad_l, zy, W - pad_r, zy)

        # Fill under curve
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint
        poly_pts = [QPoint(px(0), pad_t + gH)]
        for i, v in enumerate(pts):
            poly_pts.append(QPoint(px(i), py(v)))
        poly_pts.append(QPoint(px(len(pts)-1), pad_t + gH))
        final_positive = pts[-1] >= 0
        fill_col = QColor(0, 180, 80, 35) if final_positive else QColor(220, 50, 50, 35)
        from PyQt6.QtGui import QPolygon as _QP
        from PyQt6.QtCore import QPoint as _QPoint
        poly = _QP([_QPoint(pt.x(), pt.y()) for pt in poly_pts])
        p.setBrush(QBrush(fill_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(poly)

        # Line
        line_col = QColor(GREEN) if pts[-1] >= 0 else QColor(RED)
        p.setPen(QPen(line_col, 2))
        for i in range(1, len(pts)):
            p.drawLine(px(i-1), py(pts[i-1]), px(i), py(pts[i]))

        # Dots
        p.setBrush(QBrush(line_col))
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(pts):
            p.drawEllipse(px(i) - 3, py(v) - 3, 6, 6)

        # Y axis labels
        p.setPen(QColor(DIM))
        p.setFont(QFont("monospace", 8))
        for val in [mn, (mn+mx)/2, mx]:
            y = py(val)
            sign = "+" if val >= 0 else ""
            p.drawText(0, y - 8, pad_l - 4, 16,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{sign}{val:.2f}")

        # X axis trade labels (first, last, maybe middle)
        p.setPen(QColor(DIM))
        p.setFont(QFont("monospace", 7))
        idxs = [0, len(pts)-1]
        if len(pts) > 4:
            idxs.insert(1, len(pts)//2)
        for i in idxs:
            if i < len(self._labels):
                lbl = self._labels[i]
                x = px(i)
                p.drawText(x - 20, H - pad_b, 40, pad_b,
                           Qt.AlignmentFlag.AlignCenter, lbl)


class CryptoScannerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Crypto Scalper Scanner — Binance")
        self.setMinimumSize(1280, 760)
        self._scanner  = Scanner()
        self._worker   = None
        self._results  = []
        self._live_prices = {}   # sym -> price, populated by tab-open fetch
        self._settings = QSettings("CryptoScalper", "CryptoScannerGUI")
        self._trades   = []   # list of trade dicts, persisted to JSON
        self._programmatic_resize = False
        self._sort_col  = None
        self._sort_asc  = True
        self._alert_log = []          # list of alert dicts for history panel
        self._flash_overlay = None    # full-window flash widget
        self._flash_anim    = None    # opacity animation
        self._title_flash_timer = QTimer(self)
        self._title_flash_timer.timeout.connect(self._flash_title_tick)
        self._title_flash_state = False
        self._title_flash_count = 0
        self._title_flash_msg   = ""
        self._status_alert_active = False  # keep status bar red until dismissed
        # Alert engine — runs in background thread
        self._alert_engine = AlertEngine()
        self._alert_engine.new_alert.connect(self._on_new_alert)
        self._alert_engine.scan_done.connect(self._on_alert_scan_done)
        self._build_ui()
        self._setup_timer()
        self._restore_settings()  # after UI is built
        self._alert_engine.start()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Top bar ─────────────────────────────────────
        topbar = QFrame()
        topbar.setStyleSheet(f"background:{PANEL}; border-bottom:1px solid {BORDER};")
        topbar.setFixedHeight(64)
        tlay = QHBoxLayout(topbar)
        tlay.setContentsMargins(20, 0, 20, 0)
        tlay.setSpacing(16)

        title = QLabel("◈ CRYPTO SCALPER")
        title.setObjectName("titleLabel")

        sub = QLabel(f"Binance Spot  ·  Price < $1  ·  Vol > $1M  ·  5m")
        sub.setObjectName("subtitleLabel")

        self.scan_btn = QPushButton("⚡  SCAN")
        self.scan_btn.setObjectName("scanBtn")
        self.scan_btn.clicked.connect(self._start_scan)


        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.setFixedHeight(6)
        self.progress.setValue(0)
        self.progress.setVisible(False)

        self.status_lbl = QLabel("Ready — press Scan")
        self.status_lbl.setObjectName("statusLabel")

        # Filter chips
        self.lbl_filter = QLabel()
        self._update_filter_label()

        tlay.addWidget(title)
        tlay.addWidget(sub)
        tlay.addStretch()
        tlay.addWidget(self.status_lbl)
        tlay.addWidget(self.progress)
        reset_col_btn = QPushButton("⇔ Cols")
        reset_col_btn.setFixedHeight(30)
        reset_col_btn.setToolTip("Reset column widths to auto-proportional")
        reset_col_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; font-size:11px; padding:0 8px;"
        )
        reset_col_btn.clicked.connect(self._reset_column_widths)
        tlay.addWidget(reset_col_btn)

        tlay.addWidget(self.scan_btn)
        root.addWidget(topbar)

        # ── Tabs — full width, no splitter ──────────────
        tabs = QTabWidget()

        # Scanner table tab
        scanner_tab = QWidget()
        slay = QVBoxLayout(scanner_tab)
        slay.setContentsMargins(8, 8, 8, 8)
        slay.setSpacing(6)

        self.table = self._build_table()
        slay.addWidget(self.table)
        tabs.addTab(scanner_tab, "📊  Scanner")

        # Top Picks tab
        self.picks_tab = QWidget()
        picks_outer = QVBoxLayout(self.picks_tab)
        picks_outer.setContentsMargins(0, 0, 0, 0)
        picks_scroll = QScrollArea()
        picks_scroll.setWidgetResizable(True)
        picks_scroll.setFrameShape(QFrame.Shape.NoFrame)
        picks_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        picks_inner = QWidget()
        self.picks_lay = QVBoxLayout(picks_inner)
        self.picks_lay.setContentsMargins(12, 12, 12, 12)
        self.picks_lay.setSpacing(8)
        self.picks_lay.addWidget(QLabel("Run a scan to see top picks."))
        picks_scroll.setWidget(picks_inner)
        picks_outer.addWidget(picks_scroll)
        tabs.addTab(self.picks_tab, "🎯  Top Picks")

        # Config tab
        tabs.addTab(self._build_config_tab(), "⚙️  Config")

        # Alerts tab
        self._alerts_tab_widget = self._build_alerts_tab()
        tabs.addTab(self._alerts_tab_widget, "🔔  Alerts")

        self._trades_tab_widget = self._build_trades_tab()
        tabs.addTab(self._trades_tab_widget, "💰  Trades")

        self._tabs_widget = tabs
        tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(tabs)

        # ── Status bar ──────────────────────────────────
        self.statusBar().showMessage("Ready")

    def _build_table(self):
        cols = ["#", "Symbol", "Price", "24h%", "RSI", "StRSI",
                "MACD", "BB%", "Vol 24h", "Signal", "Pot%", "Exp%", "L/S", "Pattern", "Chart",
                "AGE", "CONF", "1H", ""]
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(False)
        t.setSortingEnabled(False)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(False)
        t.setShowGrid(True)

        hdr = t.horizontalHeader()
        # All real columns Interactive — user can drag any of them
        for i in range(len(cols) - 1):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        # Last column (index 15) is an invisible spacer that absorbs leftover width
        hdr.setSectionResizeMode(18, QHeaderView.ResizeMode.Stretch)
        t.setColumnHidden(18, False)   # visible but empty — acts as spacer

        t.itemDoubleClicked.connect(self._on_row_double_click)
        t.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        t.horizontalHeader().setSectionsClickable(True)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.customContextMenuRequested.connect(self._scanner_context_menu)
        return t

    # Proportional widths — all real columns 0-17, col 18 is spacer (Stretch, excluded)
    _COL_FRACS = {
        0:  0.025,   # #
        1:  0.070,   # Symbol
        2:  0.075,   # Price
        3:  0.052,   # 24h%
        4:  0.044,   # RSI
        5:  0.048,   # StRSI
        6:  0.048,   # MACD
        7:  0.044,   # BB%
        8:  0.068,   # Vol 24h
        9:  0.088,   # Signal
        10: 0.044,   # Pot%
        11: 0.044,   # Exp%
        12: 0.055,   # L/S
        13: 0.100,   # Pattern
        14: 0.068,   # Chart
        15: 0.048,   # AGE
        16: 0.048,   # CONF
        17: 0.040,   # 1H
    }
    _COL_MINS = {
        0:  28,  1:  62,  2:  68,  3:  48,  4:  40,
        5:  44,  6:  44,  7:  40,  8:  64,  9:  84,
        10: 40,  11: 40,  12: 50,  13: 86,  14: 62,
        15: 42,  16: 42,  17: 32,
    }

    def _reflow_columns(self):
        """Resize all managed columns proportionally to current viewport width."""
        total = self.table.viewport().width()
        if total < 100:
            return
        total_frac = sum(self._COL_FRACS.values()) or 1.0
        self._programmatic_resize = True
        try:
            for col, frac in self._COL_FRACS.items():
                min_w = self._COL_MINS.get(col, 44)
                self.table.setColumnWidth(col, max(min_w, int(total * frac / total_frac)))
        finally:
            self._programmatic_resize = False

    # ── Alert Tab ────────────────────────────────────────

    # ─────────────────────────────────────────────────────────
    #  TRADES TAB
    # ─────────────────────────────────────────────────────────
    TRADES_FILE = os.path.expanduser("~/.crypto_scanner_trades.json")

    # ─────────────────────────────────────────────────────────
    #  TRADES TAB  — right-click scanner row to open, close inline
    # ─────────────────────────────────────────────────────────
    TRADES_FILE = os.path.expanduser("~/.crypto_scanner_trades.json")

    def _build_trades_tab(self):
        self._load_trades()
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel("Right-click any coin in the Scanner tab to open a LONG or SHORT trade.")
        hint.setStyleSheet(f"color:{DIM}; font-size:11px; padding:4px 0;")
        root.addWidget(hint)

        # ── Summary bar ──────────────────────────────────────
        self.tr_summary = QLabel("No trades yet")
        self.tr_summary.setStyleSheet(f"color:{DIM}; font-size:11px; font-weight:700; padding:2px 0;")
        root.addWidget(self.tr_summary)

        # ── Stats + Equity side-by-side ──────────────────────
        stats_equity_row = QHBoxLayout()
        stats_equity_row.setSpacing(12)

        # Stats panel (left)
        stats_frame = QFrame()
        stats_frame.setStyleSheet(f"background:{CARD}; border:1px solid {BORDER}; border-radius:6px;")
        stats_frame.setFixedWidth(260)
        stats_lay = QVBoxLayout(stats_frame)
        stats_lay.setContentsMargins(12, 10, 12, 10)
        stats_lay.setSpacing(6)
        stats_title = QLabel("📈  Trade Statistics")
        stats_title.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:700; border:none;")
        stats_lay.addWidget(stats_title)
        self._stats_labels = {}
        stat_keys = [
            ("total",    "Total trades"),
            ("open",     "Open trades"),
            ("wins",     "Wins"),
            ("losses",   "Losses"),
            ("winrate",  "Win rate"),
            ("avg_win",  "Avg win"),
            ("avg_loss", "Avg loss"),
            ("best",     "Best trade"),
            ("worst",    "Worst trade"),
            ("pf",       "Profit factor"),
            ("total_pnl","Total P&L"),
        ]
        for key, label in stat_keys:
            row_w = QWidget(); row_w.setStyleSheet("background:transparent; border:none;")
            row_h = QHBoxLayout(row_w); row_h.setContentsMargins(0,0,0,0); row_h.setSpacing(4)
            lbl = QLabel(label + ":"); lbl.setStyleSheet(f"color:{DIM}; font-size:11px; border:none;")
            val = QLabel("—");         val.setStyleSheet(f"color:{WHITE}; font-size:11px; font-weight:700; border:none;")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row_h.addWidget(lbl); row_h.addStretch(); row_h.addWidget(val)
            stats_lay.addWidget(row_w)
            self._stats_labels[key] = val
        stats_lay.addStretch()
        stats_equity_row.addWidget(stats_frame)

        # Equity curve (right) — pure QPainter, no QtCharts needed
        equity_frame = QFrame()
        equity_frame.setStyleSheet(f"background:{CARD}; border:1px solid {BORDER}; border-radius:6px;")
        equity_frame.setMinimumHeight(180)
        equity_lay = QVBoxLayout(equity_frame)
        equity_lay.setContentsMargins(12, 10, 12, 10)
        equity_lay.setSpacing(4)
        eq_title = QLabel("📊  Equity Curve  (cumulative P&L)")
        eq_title.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:700; border:none;")
        equity_lay.addWidget(eq_title)
        self._equity_canvas = _EquityCanvas()
        equity_lay.addWidget(self._equity_canvas)
        stats_equity_row.addWidget(equity_frame, 1)

        root.addLayout(stats_equity_row)

        # ── Trades table ─────────────────────────────────────
        self.tr_table = QTableWidget(0, 10)
        self.tr_table.setHorizontalHeaderLabels([
            "Opened", "Symbol", "Side", "Entry $", "Qty", "SL $", "TP $", "Exit $", "P&L", "Status"
        ])
        self.tr_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tr_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tr_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tr_table.verticalHeader().setVisible(False)
        hdr = self.tr_table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)
        self.tr_table.setAlternatingRowColors(False)
        self.tr_table.setSortingEnabled(False)
        self.tr_table.setShowGrid(True)
        self.tr_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tr_table.customContextMenuRequested.connect(self._trades_context_menu)
        root.addWidget(self.tr_table)

        # ── Action buttons ───────────────────────────────────
        btn_row = QHBoxLayout()
        close_btn = QPushButton("✓  Close Selected")
        close_btn.setFixedHeight(30)
        close_btn.setStyleSheet(
            f"background:{CARD}; color:{GREEN}; border:1px solid {GREEN}; "
            f"border-radius:4px; font-weight:700; padding:0 14px;"
        )
        close_btn.clicked.connect(self._close_trade_dialog)

        edit_btn = QPushButton("✎  Edit Selected")
        edit_btn.setFixedHeight(30)
        edit_btn.setStyleSheet(
            f"background:{CARD}; color:{ACCENT}; border:1px solid {ACCENT}; "
            f"border-radius:4px; padding:0 14px;"
        )
        edit_btn.clicked.connect(self._edit_trade_dialog)

        del_btn = QPushButton("✕  Delete")
        del_btn.setFixedHeight(30)
        del_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; padding:0 14px;"
        )
        del_btn.clicked.connect(self._delete_trade)

        csv_btn = QPushButton("⬇  Export CSV")
        csv_btn.setFixedHeight(30)
        csv_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; padding:0 14px;"
        )
        csv_btn.clicked.connect(self._export_trades_csv)

        btn_row.addWidget(close_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(csv_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._refresh_trades_table()
        return w

    # ── Context menu on SCANNER table ───────────────────────
    def _scanner_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._results):
            return
        r = self._results[row]
        sym = r["symbol"].replace("USDT", "")
        price = r["price"]
        sig   = r["signal"]

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{CARD}; border:1px solid {BORDER}; color:{WHITE}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 20px; border-radius:3px; }}"
            f"QMenu::item:selected {{ background:{ACCENT}; color:{DARK}; }}"
            f"QMenu::separator {{ height:1px; background:{BORDER}; margin:4px 8px; }}"
        )

        title_act = menu.addAction(f"  {sym}  —  ${price:.6f}")
        title_act.setEnabled(False)
        menu.addSeparator()

        long_act  = menu.addAction(f"📈  LONG  (buy {sym})")
        short_act = menu.addAction(f"📉  SHORT  (sell {sym})")
        menu.addSeparator()

        detail_act  = menu.addAction("🔍  View Details")
        binance_act = menu.addAction(f"🌐  Open {sym} on Binance")

        # Highlight recommended direction
        if "BUY" in sig:
            long_act.setText(f"📈  LONG  (buy {sym})  ← {sig}")
        elif "SELL" in sig:
            short_act.setText(f"📉  SHORT  (sell {sym})  ← {sig}")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == long_act:
            self._record_trade(r, "LONG")
        elif action == short_act:
            self._record_trade(r, "SHORT")
        elif action == detail_act:
            self._show_detail_popup(r)
        elif action == binance_act:
            QDesktopServices.openUrl(QUrl(f"https://www.binance.com/en/trade/{sym}_USDT?type=spot"))

    # ── Context menu on TRADES table ────────────────────────
    def _trades_context_menu(self, pos):
        row = self.tr_table.rowAt(pos.y())
        if row < 0:
            return
        item = self.tr_table.item(row, 0)
        if item is None:
            return
        tid   = item.data(Qt.ItemDataRole.UserRole)
        trade = next((t for t in self._trades if t["id"] == tid), None)
        if trade is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{CARD}; border:1px solid {BORDER}; color:{WHITE}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 20px; border-radius:3px; }}"
            f"QMenu::item:selected {{ background:{ACCENT}; color:{DARK}; }}"
            f"QMenu::separator {{ height:1px; background:{BORDER}; margin:4px 8px; }}"
        )

        sym    = trade["symbol"]
        side   = trade["side"]
        status = trade["status"]

        title_act = menu.addAction(f"  {side} {sym}")
        title_act.setEnabled(False)
        menu.addSeparator()

        close_act = edit_act = None
        if status == "OPEN":
            # Find current price from last scan results
            cur = (self._live_prices.get(sym + "USDT") or
                   next((r["price"] for r in self._results if r["symbol"] == sym + "USDT"), None))
            label = f"✓  Close at current price  (${cur:.6f})" if cur else "✓  Close at price..."
            close_act = menu.addAction(label)
            edit_act  = menu.addAction("✎  Edit entry / SL / TP")
            menu.addSeparator()

        del_act = menu.addAction("✕  Delete")

        binance_act2 = menu.addAction(f"🌐  Open {sym} on Binance")

        action = menu.exec(self.tr_table.viewport().mapToGlobal(pos))
        if action == close_act:
            cur = (self._live_prices.get(sym + "USDT") or
                   next((r["price"] for r in self._results if r["symbol"] == sym + "USDT"), None))
            self._close_trade_dialog(tid=tid, prefill_price=cur)
        elif action == edit_act:
            self._edit_trade_dialog(tid=tid)
        elif action == del_act:
            self._trades = [t for t in self._trades if t["id"] != tid]
            self._save_trades()
            self._refresh_trades_table()
        elif action == binance_act2:
            QDesktopServices.openUrl(QUrl(f"https://www.binance.com/en/trade/{sym}_USDT?type=spot"))

    # ── Record trade from scanner right-click ───────────────
    def _record_trade(self, r, side):
        """Open a dialog to confirm/edit the trade before recording."""
        sym   = r["symbol"].replace("USDT", "")
        price = r["price"]

        # Suggest SL/TP from support/resistance or CFG percentages
        sl_pct = CFG["sl_pct"] / 100
        tp_pct = CFG["tp_pct"] / 100
        if side == "LONG":
            suggested_sl = round(price * (1 - sl_pct), 8)
            suggested_tp = round(price * (1 + tp_pct), 8)
        else:
            suggested_sl = round(price * (1 + sl_pct), 8)
            suggested_tp = round(price * (1 - tp_pct), 8)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Open {side} — {sym}")
        dlg.setModal(True)
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(f"background:{DARK2}; color:{WHITE};")
        vlay = QVBoxLayout(dlg)
        vlay.setSpacing(10)

        accent = GREEN if side == "LONG" else RED
        icon   = "📈" if side == "LONG" else "📉"
        header = QLabel(f"{icon}  <b>{side}</b>  {sym}  —  ${price:.8f}")
        header.setStyleSheet(f"color:{accent}; font-size:13px; padding:4px 0;")
        vlay.addWidget(header)

        grid = QGridLayout(); grid.setSpacing(8)
        lbl_s = f"color:{DIM}; font-size:11px;"

        entry_spin = QDoubleSpinBox(); entry_spin.setRange(0.0000001, 999999); entry_spin.setDecimals(8); entry_spin.setValue(price)
        qty_spin   = QDoubleSpinBox(); qty_spin.setRange(0, 999999999); qty_spin.setDecimals(4); qty_spin.setValue(0)
        sl_spin    = QDoubleSpinBox(); sl_spin.setRange(0.0000001, 999999); sl_spin.setDecimals(8); sl_spin.setValue(suggested_sl)
        tp_spin    = QDoubleSpinBox(); tp_spin.setRange(0.0000001, 999999); tp_spin.setDecimals(8); tp_spin.setValue(suggested_tp)
        note_edit  = QLineEdit(); note_edit.setPlaceholderText("Optional note...")

        pnl_hint = QLabel()
        pnl_hint.setStyleSheet(f"color:{DIM}; font-size:11px;")

        def _update_hint():
            e = entry_spin.value(); q = qty_spin.value()
            if q > 0 and e > 0:
                cost = e * q
                sl_loss = abs(sl_spin.value() - e) * q * (-1 if side == "LONG" else 1)
                if side == "LONG": sl_loss = -(sl_spin.value() - e) * q if sl_spin.value() < e else 0
                else:              sl_loss = -(e - sl_spin.value()) * q if sl_spin.value() > e else 0
                tp_gain = abs(tp_spin.value() - e) * q
                pnl_hint.setText(f"Cost: ${cost:.2f} USDT  |  SL risk: -${abs(sl_loss):.4f}  |  TP gain: +${tp_gain:.4f}")
            else:
                pnl_hint.setText("Enter quantity to see cost and risk")
        for s in (entry_spin, qty_spin, sl_spin, tp_spin):
            s.valueChanged.connect(_update_hint)

        for i, (lbl, widget) in enumerate([
            ("Entry price", entry_spin), ("Quantity (coins)", qty_spin),
            ("Stop Loss",   sl_spin),    ("Take Profit",      tp_spin),
            ("Note",        note_edit),
        ]):
            l = QLabel(lbl); l.setStyleSheet(lbl_s)
            grid.addWidget(l, i, 0)
            grid.addWidget(widget, i, 1)
        vlay.addLayout(grid)
        vlay.addWidget(pnl_hint)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton(f"{icon}  Confirm Open {side}")
        ok_btn.setStyleSheet(
            f"background:{'#002a1a' if side=='LONG' else '#2a0010'}; color:{accent}; "
            f"border:1px solid {accent}; border-radius:4px; font-weight:700; padding:4px 16px;"
        )
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px; padding:4px 12px;")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(ok_btn); btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)
        _update_hint()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        trade = {
            "id":     int(datetime.now().timestamp() * 1000),
            "time":   datetime.now().strftime("%m-%d %H:%M"),
            "symbol": sym + "USDT",
            "side":   side,
            "entry":  entry_spin.value(),
            "qty":    qty_spin.value(),
            "sl":     sl_spin.value(),
            "tp":     tp_spin.value(),
            "note":   note_edit.text().strip(),
            "exit":   None, "pnl": None, "pnl_pct": None,
            "status": "OPEN",
        }
        self._trades.insert(0, trade)
        self._save_trades()
        self._refresh_trades_table()

        # Switch to Trades tab
        tabs = self.centralWidget().findChild(QTabWidget)
        if tabs:
            for i in range(tabs.count()):
                if "Trade" in tabs.tabText(i):
                    tabs.setCurrentIndex(i)
                    break
        self.statusBar().showMessage(
            f"Opened {side} {sym} @ ${entry_spin.value():.6f}  qty: {qty_spin.value()}"
        )

    # ── Close trade dialog ───────────────────────────────────
    def _close_trade_dialog(self, checked=False, tid=None, prefill_price=None):
        # If called from button (no tid), get selected row
        if tid is None:
            row = self.tr_table.currentRow()
            if row < 0:
                self.statusBar().showMessage("Select a trade row first")
                return
            item = self.tr_table.item(row, 0)
            if item is None: return
            tid = item.data(Qt.ItemDataRole.UserRole)

        trade = next((t for t in self._trades if t["id"] == tid), None)
        if trade is None or trade["status"] != "OPEN":
            self.statusBar().showMessage("Trade is already closed")
            return

        sym   = trade["symbol"].replace("USDT", "")
        side  = trade["side"]
        entry = trade["entry"]
        qty   = trade["qty"]
        close_label = "Sell" if side == "LONG" else "Buy back"
        accent = RED if side == "LONG" else GREEN  # closing a long = sell (red), closing short = buy (green)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Close {side} — {sym}")
        dlg.setModal(True)
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(f"background:{DARK2}; color:{WHITE};")
        vlay = QVBoxLayout(dlg)
        vlay.setSpacing(10)

        info = QLabel(
            f"<b>{side} {sym}</b> &nbsp; entry: <b>${entry:.8f}</b> &nbsp; qty: <b>{qty}</b>"
        )
        info.setStyleSheet(f"color:{WHITE}; font-size:12px; padding:4px 0;")
        vlay.addWidget(info)

        grid = QGridLayout(); grid.setSpacing(8)
        lbl_s = f"color:{DIM}; font-size:11px;"

        exit_spin = QDoubleSpinBox()
        exit_spin.setRange(0.0000001, 999999)
        exit_spin.setDecimals(8)
        exit_spin.setValue(prefill_price if prefill_price else entry)

        l = QLabel(f"{close_label} at price"); l.setStyleSheet(lbl_s)
        grid.addWidget(l, 0, 0)
        grid.addWidget(exit_spin, 0, 1)
        vlay.addLayout(grid)

        pnl_lbl = QLabel()
        pnl_lbl.setStyleSheet("font-size:14px; font-weight:700; padding:4px 0;")
        vlay.addWidget(pnl_lbl)

        def _calc():
            ep = exit_spin.value()
            if side == "LONG":
                pnl = (ep - entry) * qty
                pct = (ep - entry) / entry * 100
            else:
                pnl = (entry - ep) * qty
                pct = (entry - ep) / entry * 100
            sign = "+" if pnl >= 0 else ""
            col  = GREEN if pnl >= 0 else RED
            pnl_lbl.setText(f"P&L: {sign}{pnl:.6f} USDT  ({sign}{pct:.2f}%)")
            pnl_lbl.setStyleSheet(f"color:{col}; font-size:14px; font-weight:700; padding:4px 0;")
        exit_spin.valueChanged.connect(_calc)
        _calc()

        btn_row = QHBoxLayout()
        ok_btn = QPushButton(f"✓  Confirm {close_label}")
        ok_btn.setStyleSheet(
            f"background:{'#2a0010' if side=='LONG' else '#002a1a'}; color:{accent}; "
            f"border:1px solid {accent}; border-radius:4px; font-weight:700; padding:4px 16px;"
        )
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px; padding:4px 12px;")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(ok_btn); btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        ep = exit_spin.value()
        if side == "LONG":
            pnl = (ep - entry) * qty
            pct = (ep - entry) / entry * 100
        else:
            pnl = (entry - ep) * qty
            pct = (entry - ep) / entry * 100

        trade["exit"]    = ep
        trade["pnl"]     = round(pnl, 8)
        trade["pnl_pct"] = round(pct, 4)
        trade["status"]  = "WIN" if pnl >= 0 else "LOSS"
        trade["closed"]  = datetime.now().strftime("%m-%d %H:%M")
        self._save_trades()
        self._refresh_trades_table()
        sign = "+" if pnl >= 0 else ""
        self.statusBar().showMessage(
            f"Closed {side} {sym}: {sign}{pnl:.6f} USDT ({sign}{pct:.2f}%)"
        )

    # ── Edit trade dialog ────────────────────────────────────
    def _edit_trade_dialog(self, checked=False, tid=None):
        if tid is None:
            row = self.tr_table.currentRow()
            if row < 0:
                self.statusBar().showMessage("Select a trade row first")
                return
            item = self.tr_table.item(row, 0)
            if item is None: return
            tid = item.data(Qt.ItemDataRole.UserRole)

        trade = next((t for t in self._trades if t["id"] == tid), None)
        if trade is None:
            return

        sym   = trade["symbol"].replace("USDT","")
        side  = trade["side"]
        accent = GREEN if side == "LONG" else RED

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit Trade — {side} {sym}")
        dlg.setModal(True)
        dlg.setMinimumWidth(340)
        dlg.setStyleSheet(f"background:{DARK2}; color:{WHITE};")
        vlay = QVBoxLayout(dlg)
        vlay.setSpacing(10)

        grid = QGridLayout(); grid.setSpacing(8)
        lbl_s = f"color:{DIM}; font-size:11px;"

        entry_spin = QDoubleSpinBox(); entry_spin.setRange(0.0000001,999999); entry_spin.setDecimals(8); entry_spin.setValue(trade["entry"])
        qty_spin   = QDoubleSpinBox(); qty_spin.setRange(0,999999999);        qty_spin.setDecimals(4);   qty_spin.setValue(trade["qty"])
        sl_spin    = QDoubleSpinBox(); sl_spin.setRange(0.0000001,999999);    sl_spin.setDecimals(8);    sl_spin.setValue(trade["sl"] or 0)
        tp_spin    = QDoubleSpinBox(); tp_spin.setRange(0.0000001,999999);    tp_spin.setDecimals(8);    tp_spin.setValue(trade["tp"] or 0)
        note_edit  = QLineEdit(); note_edit.setText(trade.get("note",""))

        for i, (lbl, widget) in enumerate([
            ("Entry price",       entry_spin),
            ("Quantity (coins)",  qty_spin),
            ("Stop Loss",         sl_spin),
            ("Take Profit",       tp_spin),
            ("Note",              note_edit),
        ]):
            l = QLabel(lbl); l.setStyleSheet(lbl_s)
            grid.addWidget(l, i, 0)
            grid.addWidget(widget, i, 1)
        vlay.addLayout(grid)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("✓  Save Changes")
        ok_btn.setStyleSheet(
            f"background:{CARD}; color:{accent}; border:1px solid {accent}; "
            f"border-radius:4px; font-weight:700; padding:4px 16px;"
        )
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px; padding:4px 12px;")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(ok_btn); btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        trade["entry"] = entry_spin.value()
        trade["qty"]   = qty_spin.value()
        trade["sl"]    = sl_spin.value()
        trade["tp"]    = tp_spin.value()
        trade["note"]  = note_edit.text().strip()
        self._save_trades()
        self._refresh_trades_table()
        self.statusBar().showMessage(f"Trade updated: {side} {sym}")

    # ── Delete selected trade ────────────────────────────────
    def _delete_trade(self):
        row = self.tr_table.currentRow()
        if row < 0:
            self.statusBar().showMessage("Select a trade row first")
            return
        item = self.tr_table.item(row, 0)
        if item is None: return
        tid = item.data(Qt.ItemDataRole.UserRole)
        self._trades = [t for t in self._trades if t["id"] != tid]
        self._save_trades()
        self._refresh_trades_table()

    # ── Refresh trades table ─────────────────────────────────
    def _refresh_trades_table(self):
        if not hasattr(self, 'tr_table'):
            return
        self.tr_table.setRowCount(0)

        open_trades   = [t for t in self._trades if t["status"] == "OPEN"]
        closed_trades = [t for t in self._trades if t["status"] != "OPEN"]
        total_pnl  = sum(t.get("pnl") or 0 for t in closed_trades)
        wins       = sum(1 for t in closed_trades if (t.get("pnl") or 0) >= 0)
        losses     = len(closed_trades) - wins
        win_rate   = wins / len(closed_trades) * 100 if closed_trades else 0

        for trade in open_trades + closed_trades:
            r      = self.tr_table.rowCount()
            self.tr_table.insertRow(r)
            self.tr_table.setRowHeight(r, 34)

            status = trade["status"]
            if status == "OPEN":
                row_bg = QColor("#001525")
            elif (trade.get("pnl") or 0) >= 0:
                row_bg = QColor(STRONG_BUY_BG)
            else:
                row_bg = QColor(STRONG_SELL_BG)

            def cell(text, color=WHITE, bold=False,
                     align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                     tid=trade["id"]):
                it = QTableWidgetItem(str(text))
                it.setForeground(QBrush(QColor(color)))
                it.setBackground(QBrush(row_bg))
                it.setTextAlignment(align)
                if bold:
                    f = it.font(); f.setBold(True); it.setFont(f)
                it.setData(Qt.ItemDataRole.UserRole, tid)
                return it

            left   = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
            center = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter
            side   = trade["side"]
            side_col = GREEN if side == "LONG" else RED

            if trade.get("pnl") is not None:
                pnl     = trade["pnl"]
                pct     = trade.get("pnl_pct", 0)
                sign    = "+" if pnl >= 0 else ""
                pnl_str = f"{sign}{pnl:.6f}  ({sign}{pct:.2f}%)"
                pnl_col = GREEN if pnl >= 0 else RED
            else:
                # Live unrealised P&L for open trades
                sym_full = trade["symbol"]
                cur = (self._live_prices.get(sym_full) or
                       next((res["price"] for res in self._results if res["symbol"] == sym_full), None))
                if cur:
                    entry = trade["entry"]
                    qty   = trade.get("qty", 0)
                    if side == "LONG":
                        upct = (cur - entry) / entry * 100
                        upnl = (cur - entry) * qty
                    else:
                        upct = (entry - cur) / entry * 100
                        upnl = (entry - cur) * qty
                    sign = "+" if upct >= 0 else ""
                    # Always show % move; show USDT P&L only if qty set
                    if qty > 0:
                        pnl_str = f"▶ {sign}{upnl:.4f} ({sign}{upct:.2f}%)"
                    else:
                        pnl_str = f"▶ {sign}{upct:.2f}%  @${cur:.6f}"
                    pnl_col = GREEN if upct >= 0 else RED
                else:
                    pnl_str = "⏳ fetching…"
                    pnl_col = DIM

            status_col = ACCENT if status == "OPEN" else (GREEN if (trade.get("pnl") or 0) >= 0 else RED)
            if trade.get("exit"):
                exit_str = f"${trade['exit']:.8f}"
            elif status == "OPEN":
                # Show current live price in exit column while trade is open
                sym_full = trade["symbol"]
                cur2 = (self._live_prices.get(sym_full) or
                        next((res["price"] for res in self._results if res["symbol"] == sym_full), None))
                exit_str = f"~${cur2:.8f}" if cur2 else "fetching…"
            else:
                exit_str = "—"

            self.tr_table.setItem(r, 0,  cell(trade["time"],              DIM,       align=left))
            self.tr_table.setItem(r, 1,  cell(trade["symbol"].replace("USDT",""), ACCENT, bold=True, align=left))
            self.tr_table.setItem(r, 2,  cell(side,                       side_col,  bold=True, align=center))
            self.tr_table.setItem(r, 3,  cell(f"${trade['entry']:.8f}",   WHITE))
            self.tr_table.setItem(r, 4,  cell(f"{trade['qty']}",          WHITE))
            self.tr_table.setItem(r, 5,  cell(f"${trade['sl']:.8f}" if trade.get("sl") else "—", DIM))
            self.tr_table.setItem(r, 6,  cell(f"${trade['tp']:.8f}" if trade.get("tp") else "—", DIM))
            self.tr_table.setItem(r, 7,  cell(exit_str,                   WHITE))
            self.tr_table.setItem(r, 8,  cell(pnl_str,                    pnl_col, bold=True))
            self.tr_table.setItem(r, 9,  cell(status,                     status_col, bold=True, align=center))

        # Summary bar
        if hasattr(self, 'tr_summary'):
            if not self._trades:
                self.tr_summary.setText("No trades yet — right-click a coin in Scanner to begin")
                self.tr_summary.setStyleSheet(f"color:{DIM}; font-size:11px; font-weight:700; padding:2px 0;")
            else:
                sign = "+" if total_pnl >= 0 else ""
                col  = GREEN if total_pnl > 0 else RED if total_pnl < 0 else DIM
                self.tr_summary.setText(
                    f"Open: {len(open_trades)}  |  Closed: {len(closed_trades)}  |  "
                    f"Win rate: {win_rate:.0f}%  ({wins}W / {losses}L)  |  "
                    f"Total P&L: {sign}{total_pnl:.4f} USDT"
                )
                self.tr_summary.setStyleSheet(f"color:{col}; font-size:11px; font-weight:700; padding:2px 0;")

        # Stats panel
        if hasattr(self, '_stats_labels'):
            closed = [t for t in self._trades if t["status"] != "OPEN"]
            win_pnls  = [t["pnl"] for t in closed if (t.get("pnl") or 0) >= 0]
            loss_pnls = [t["pnl"] for t in closed if (t.get("pnl") or 0) <  0]
            avg_win   = sum(win_pnls)  / len(win_pnls)  if win_pnls  else 0
            avg_loss  = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
            best      = max((t.get("pnl") or 0) for t in closed) if closed else 0
            worst     = min((t.get("pnl") or 0) for t in closed) if closed else 0
            gross_win  = sum(win_pnls)
            gross_loss = abs(sum(loss_pnls))
            pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
            wr = wins / len(closed) * 100 if closed else 0

            def _sv(key, text, col=WHITE):
                lbl = self._stats_labels.get(key)
                if lbl:
                    lbl.setText(text)
                    lbl.setStyleSheet(f"color:{col}; font-size:11px; font-weight:700; border:none;")

            def _fmt_pnl(t):
                """Format a single trade's P&L — show % if USDT is 0 (no qty)."""
                pnl = t.get("pnl") or 0
                pct = t.get("pnl_pct") or 0
                sign = "+" if pnl >= 0 else ""
                if pnl == 0 and pct != 0:
                    return f"{sign}{pct:.2f}%"
                return f"{sign}{pnl:.4f}"

            # Avg win/loss using pct if usdt pnl is zero
            win_vals  = [t.get("pnl_pct") or 0 if (t.get("pnl") or 0) == 0
                         else (t.get("pnl") or 0) for t in closed if (t.get("pnl") or 0) >= 0]
            loss_vals = [t.get("pnl_pct") or 0 if (t.get("pnl") or 0) == 0
                         else (t.get("pnl") or 0) for t in closed if (t.get("pnl") or 0) < 0]
            avg_win_v  = sum(win_vals)  / len(win_vals)  if win_vals  else 0
            avg_loss_v = sum(loss_vals) / len(loss_vals) if loss_vals else 0
            best_t  = max(closed, key=lambda t: t.get("pnl") or 0) if closed else None
            worst_t = min(closed, key=lambda t: t.get("pnl") or 0) if closed else None

            _sv("total",    str(len(self._trades)))
            _sv("open",     str(len(open_trades)), ACCENT)
            _sv("wins",     str(wins),  GREEN if wins  else DIM)
            _sv("losses",   str(losses), RED if losses else DIM)
            _sv("winrate",  f"{wr:.1f}%", GREEN if wr >= 50 else RED)
            sign = "+" if avg_win_v >= 0 else ""
            _sv("avg_win",  f"{sign}{avg_win_v:.4f}",  GREEN if avg_win_v  > 0 else DIM)
            _sv("avg_loss", f"{avg_loss_v:.4f}",        RED   if avg_loss_v < 0 else DIM)
            _sv("best",     _fmt_pnl(best_t)  if best_t  else "—", GREEN)
            _sv("worst",    _fmt_pnl(worst_t) if worst_t else "—", RED)
            pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
            _sv("pf",       pf_str, GREEN if pf >= 1 else RED)
            tsign = "+" if total_pnl >= 0 else ""
            # If all trades had no qty, show total % instead
            has_usdt = any((t.get("pnl") or 0) != 0 for t in closed)
            if has_usdt:
                _sv("total_pnl", f"{tsign}{total_pnl:.4f} USDT",
                    GREEN if total_pnl > 0 else RED if total_pnl < 0 else DIM)
            else:
                total_pct = sum(t.get("pnl_pct") or 0 for t in closed)
                _sv("total_pnl", f"{tsign}{total_pct:.2f}% (no qty set)",
                    GREEN if total_pct > 0 else RED if total_pct < 0 else DIM)

        # Equity curve — sorted closed trades by open time
        if hasattr(self, '_equity_canvas'):
            closed_sorted = sorted(
                [t for t in self._trades if t["status"] != "OPEN" and t.get("pnl") is not None],
                key=lambda t: t.get("time","")
            )
            cum = 0.0
            cum_pts, labels = [], []
            for t in closed_sorted:
                cum += t["pnl"]
                cum_pts.append(round(cum, 6))
                labels.append(t["symbol"].replace("USDT",""))
            self._equity_canvas.set_data(cum_pts, labels)

    def _save_trades(self):
        try:
            with open(self.TRADES_FILE, "w") as f:
                json.dump(self._trades, f, indent=2)
        except Exception as e:
            self.statusBar().showMessage(f"Trade save error: {e}")

    def _load_trades(self):
        try:
            if os.path.exists(self.TRADES_FILE):
                with open(self.TRADES_FILE, "r") as f:
                    self._trades = json.load(f)
            else:
                self._trades = []
        except Exception:
            self._trades = []


    # ─────────────────────────────────────────────────────────
    #  TAB SWITCH — live price fetch when Trades tab opened
    # ─────────────────────────────────────────────────────────
    def _on_tab_changed(self, index):
        if self._tabs_widget.tabText(index).startswith("💰"):
            self._fetch_open_trade_prices()

    def _fetch_open_trade_prices(self):
        """Fetch current prices for open trades into self._live_prices dict.
        Never touches self._results so sorting/rendering never breaks."""
        open_syms = list({t["symbol"] for t in self._trades if t["status"] == "OPEN"})
        if not open_syms:
            return

        self.statusBar().showMessage("Fetching live prices for open trades…")

        def _worker():
            try:
                data = api_get("/api/v3/ticker/price")
                price_map = {d["symbol"]: float(d["price"]) for d in data}
                for sym in open_syms:
                    p = price_map.get(sym)
                    if p is not None:
                        self._live_prices[sym] = p
                # Also update price inside any existing scan results (safe — key always exists)
                for r in self._results:
                    if r["symbol"] in self._live_prices:
                        r["price"] = self._live_prices[r["symbol"]]
            except Exception:
                pass

        def _done():
            self._check_sltp_hits(self._results)
            self._refresh_trades_table()
            self.statusBar().showMessage("Live prices updated for open trades")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        def _poll():
            if t.is_alive():
                QTimer.singleShot(100, _poll)
            else:
                _done()
        QTimer.singleShot(100, _poll)

    # ─────────────────────────────────────────────────────────
    #  AUTO SL/TP HIT DETECTION
    # ─────────────────────────────────────────────────────────
    def _check_sltp_hits(self, results):
        """Called after every scan. Auto-closes open trades whose SL or TP has been crossed."""
        price_map = {r["symbol"]: r["price"] for r in results}
        price_map.update(getattr(self, "_live_prices", {}))
        hits = []

        for trade in self._trades:
            if trade["status"] != "OPEN":
                continue
            sym   = trade["symbol"]   # full e.g. PENGUSDT
            price = price_map.get(sym)
            if price is None:
                continue

            side  = trade["side"]
            entry = trade["entry"]
            sl    = trade.get("sl")
            tp    = trade.get("tp")
            hit_type = None

            if side == "LONG":
                if sl and price <= sl:
                    hit_type = "SL"
                elif tp and price >= tp:
                    hit_type = "TP"
            else:  # SHORT
                if sl and price >= sl:
                    hit_type = "SL"
                elif tp and price <= tp:
                    hit_type = "TP"

            if hit_type:
                hits.append((trade, hit_type, price))

        for trade, hit_type, price in hits:
            # Auto-close
            side  = trade["side"]
            entry = trade["entry"]
            qty   = trade.get("qty", 0)
            if side == "LONG":
                pnl = (price - entry) * qty
                pct = (price - entry) / entry * 100
            else:
                pnl = (entry - price) * qty
                pct = (entry - price) / entry * 100

            trade["exit"]    = price
            trade["pnl"]     = round(pnl, 8)
            trade["pnl_pct"] = round(pct, 4)
            trade["status"]  = "WIN" if pnl >= 0 else "LOSS"
            trade["closed"]  = datetime.now().strftime("%m-%d %H:%M")
            trade["close_reason"] = hit_type

            sym_short = trade["symbol"].replace("USDT", "")
            sign = "+" if pnl >= 0 else ""
            msg  = (f"{'🎯' if hit_type=='TP' else '🛑'}  {hit_type} HIT  {side} {sym_short}  "
                    f"@ ${price:.6f}  P&L: {sign}{pnl:.4f} USDT ({sign}{pct:.2f}%)")

            # Status bar
            self.statusBar().showMessage(msg)

            # Desktop notification
            try:
                urgency = "normal" if hit_type == "TP" else "critical"
                icon    = "dialog-information" if hit_type == "TP" else "dialog-warning"
                subprocess.Popen([
                    "notify-send", "-u", urgency, "-i", icon,
                    f"{hit_type} Hit — {side} {sym_short}",
                    f"Price: ${price:.6f}\nP&L: {sign}{pnl:.4f} USDT ({sign}{pct:.2f}%)"
                ])
            except Exception:
                pass

            # Sound — reuse alert sounds
            try:
                wav = _SOUNDS.get("STRONG BUY" if hit_type == "TP" else "STRONG SELL")
                if wav and os.path.exists(wav):
                    players = ["ffplay -nodisp -autoexit",
                               "aplay", "paplay", "pw-play"]
                    for pl in players:
                        parts = pl.split() + [wav]
                        try:
                            subprocess.Popen(parts, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
                            break
                        except FileNotFoundError:
                            continue
            except Exception:
                pass

            # Telegram
            try:
                if ALERT_CFG.get("telegram") and ALERT_CFG.get("tg_token") and ALERT_CFG.get("tg_chat_id"):
                    text = (f"{'🎯 TP HIT' if hit_type=='TP' else '🛑 SL HIT'}\n"
                            f"{side} {sym_short} closed @ ${price:.6f}\n"
                            f"P&L: {sign}{pnl:.4f} USDT ({sign}{pct:.2f}%)")
                    requests.post(
                        f"https://api.telegram.org/bot{ALERT_CFG['tg_token']}/sendMessage",
                        json={"chat_id": ALERT_CFG["tg_chat_id"], "text": text},
                        timeout=5
                    )
            except Exception:
                pass

        if hits:
            self._save_trades()
            self._refresh_trades_table()

    # ─────────────────────────────────────────────────────────
    #  CSV EXPORT
    # ─────────────────────────────────────────────────────────
    def _export_trades_csv(self):
        import csv
        fname = f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        try:
            with open(fname, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "id","time","symbol","side","entry","qty","sl","tp",
                    "exit","pnl","pnl_pct","status","closed","close_reason","note"
                ])
                writer.writeheader()
                for t in self._trades:
                    writer.writerow({k: t.get(k,"") for k in writer.fieldnames})
            self.statusBar().showMessage(f"Trades exported → {fname}")
        except Exception as e:
            self.statusBar().showMessage(f"CSV export error: {e}")

    def _build_alerts_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        # ── Auto-scan settings ────────────────────────
        auto_grp = QGroupBox("AUTO-SCAN & TRIGGER")
        aglay = QGridLayout(auto_grp)
        aglay.setSpacing(10)

        self.al_enabled = QCheckBox("Enable auto-scan alerts")
        self.al_enabled.setChecked(ALERT_CFG["enabled"])
        self.al_enabled.setStyleSheet(f"color:{WHITE};")

        self.al_interval = QSpinBox()
        self.al_interval.setRange(30, 3600)
        self.al_interval.setValue(ALERT_CFG["interval_sec"])
        self.al_interval.setSuffix("s")

        self.al_min_signal = QComboBox()
        for s in ["BUY", "STRONG BUY"]:
            self.al_min_signal.addItem(s)
        self.al_min_signal.setCurrentText(ALERT_CFG["min_signal"])

        self.al_min_pot = QSpinBox()
        self.al_min_pot.setRange(0, 100)
        self.al_min_pot.setValue(ALERT_CFG["min_potential"])
        self.al_min_pot.setSuffix("%")

        self.al_min_exp = QDoubleSpinBox()
        self.al_min_exp.setRange(0, 50)
        self.al_min_exp.setValue(ALERT_CFG["min_exp_move"])
        self.al_min_exp.setSuffix("%")

        aglay.addWidget(self.al_enabled,                             0, 0, 1, 2)
        aglay.addWidget(QLabel("Scan interval"),                     1, 0)
        aglay.addWidget(self.al_interval,                            1, 1)
        aglay.addWidget(QLabel("Minimum signal"),                    2, 0)
        aglay.addWidget(self.al_min_signal,                          2, 1)
        aglay.addWidget(QLabel("Min Potential %"),                   3, 0)
        aglay.addWidget(self.al_min_pot,                             3, 1)
        aglay.addWidget(QLabel("Min Exp Move %"),                    4, 0)
        aglay.addWidget(self.al_min_exp,                             4, 1)
        for i in range(aglay.rowCount()):
            lbl = aglay.itemAtPosition(i, 0)
            if lbl and lbl.widget():
                lbl.widget().setStyleSheet(f"color:{DIM};")
        lay.addWidget(auto_grp)

        # ── Notification channels ─────────────────────
        ch_grp = QGroupBox("NOTIFICATION CHANNELS")
        chlay = QVBoxLayout(ch_grp)
        chlay.setSpacing(8)

        self.al_sound   = QCheckBox("🔊  Sound alert  (via ffplay — ascending beep = long, descending = short)")
        self.al_desktop = QCheckBox("🖥  Desktop notification  (via notify-send)")
        self.al_tg      = QCheckBox("✈️  Telegram bot message")
        for cb in (self.al_sound, self.al_desktop, self.al_tg):
            cb.setStyleSheet(f"color:{WHITE};")
        self.al_sound.setChecked(ALERT_CFG["sound"])
        self.al_desktop.setChecked(ALERT_CFG["desktop"])
        self.al_tg.setChecked(ALERT_CFG["telegram"])

        # Telegram fields
        tg_frame = QFrame()
        tg_frame.setStyleSheet(f"background:{CARD}; border-radius:4px; padding:4px;")
        tglay = QGridLayout(tg_frame)
        tglay.setSpacing(8)
        tg_note = QLabel(
            "Get your token from @BotFather on Telegram.\n"
            "Get your chat ID by messaging @userinfobot.\n"
            "Format: 123456789  (just the number)")
        tg_note.setStyleSheet(f"color:{DIM}; font-size:11px;")
        tg_note.setWordWrap(True)

        self.al_tg_token   = QLineEdit(); self.al_tg_token.setPlaceholderText("Bot token  e.g. 123456789:ABCdef...")
        self.al_tg_chat    = QLineEdit(); self.al_tg_chat.setPlaceholderText("Chat ID  e.g. 123456789")
        self.al_tg_token.setText(ALERT_CFG["tg_token"])
        self.al_tg_chat.setText(ALERT_CFG["tg_chat_id"])
        for f in (self.al_tg_token, self.al_tg_chat):
            f.setStyleSheet(f"background:{DARK2}; color:{WHITE}; border:1px solid {BORDER}; padding:4px; border-radius:3px;")

        tglay.addWidget(tg_note,             0, 0, 1, 2)
        tglay.addWidget(QLabel("Token:"),    1, 0)
        tglay.addWidget(self.al_tg_token,    1, 1)
        tglay.addWidget(QLabel("Chat ID:"),  2, 0)
        tglay.addWidget(self.al_tg_chat,     2, 1)
        for i in (1, 2):
            tglay.itemAtPosition(i, 0).widget().setStyleSheet(f"color:{DIM};")
        # Show/hide telegram fields based on checkbox
        tg_frame.setVisible(ALERT_CFG["telegram"])
        self.al_tg.toggled.connect(tg_frame.setVisible)

        chlay.addWidget(self.al_sound)
        chlay.addWidget(self.al_desktop)
        chlay.addWidget(self.al_tg)
        chlay.addWidget(tg_frame)

        # ── WhatsApp via PicoClaw ──────────────────────
        self.al_wa = QCheckBox("📱  WhatsApp  (via PicoClaw — scan QR once, alerts forever)")
        self.al_wa.setStyleSheet(f"color:{WHITE};")
        self.al_wa.setChecked(ALERT_CFG["whatsapp"])

        wa_frame = QFrame()
        wa_frame.setStyleSheet(f"background:{CARD}; border-radius:4px; padding:4px;")
        walay = QGridLayout(wa_frame)
        walay.setSpacing(8)

        wa_note = QLabel(
            "PicoClaw is a lightweight AI agent that links to your WhatsApp\n"
            "as a Linked Device — just like WhatsApp Web.\n\n"
            "Setup (one-time, 3 minutes):\n"
            "  1. yay -S picoclaw-bin   (or download from github.com/sipeed/picoclaw)\n"
            "  2. picoclaw onboard\n"
            "  3. Add WhatsApp to ~/.picoclaw/config.json  (see button below)\n"
            "  4. picoclaw gateway   → scan QR code in WhatsApp → Linked Devices\n"
            "  5. Leave gateway running. Done — alerts arrive instantly.\n\n"
            "Your phone number below (recipient for alerts):\n"
            "Format: country code + number, no + or spaces\n"
            "Example Pakistan: 923001234567  |  Example US: 12125551234")
        wa_note.setStyleSheet(f"color:{DIM}; font-size:11px;")
        wa_note.setWordWrap(True)

        self.al_wa_number = QLineEdit()
        self.al_wa_number.setPlaceholderText("e.g.  923001234567")
        self.al_wa_number.setText(ALERT_CFG["wa_number"])
        self.al_wa_number.setStyleSheet(
            f"background:{DARK2}; color:{WHITE}; border:1px solid {BORDER}; "
            f"padding:4px; border-radius:3px;")

        self.al_wa_queue = QLineEdit()
        self.al_wa_queue.setText(ALERT_CFG["picoclaw_queue"])
        self.al_wa_queue.setStyleSheet(
            f"background:{DARK2}; color:{DIM}; border:1px solid {BORDER}; "
            f"padding:4px; border-radius:3px; font-size:10px;")

        gen_cfg_btn = QPushButton("📋  Copy PicoClaw config snippet")
        gen_cfg_btn.clicked.connect(self._copy_picoclaw_config)
        gen_hb_btn  = QPushButton("📋  Copy HEARTBEAT.md task")
        gen_hb_btn.clicked.connect(self._copy_picoclaw_heartbeat)

        walay.addWidget(wa_note,                          0, 0, 1, 2)
        walay.addWidget(QLabel("Your number:"),           1, 0)
        walay.addWidget(self.al_wa_number,                1, 1)
        walay.addWidget(QLabel("Queue file:"),            2, 0)
        walay.addWidget(self.al_wa_queue,                 2, 1)
        walay.addWidget(gen_cfg_btn,                      3, 0)
        walay.addWidget(gen_hb_btn,                       3, 1)
        for i in (1, 2):
            walay.itemAtPosition(i, 0).widget().setStyleSheet(f"color:{DIM};")

        wa_frame.setVisible(ALERT_CFG["whatsapp"])
        self.al_wa.toggled.connect(wa_frame.setVisible)

        chlay.addWidget(self.al_wa)
        chlay.addWidget(wa_frame)

        lay.addWidget(ch_grp)

        # ── Buttons ───────────────────────────────────
        btn_row = QHBoxLayout()
        apply_btn = QPushButton("✓  Apply Alert Settings")
        apply_btn.clicked.connect(self._apply_alert_config)
        test_btn = QPushButton("🔔  Test Alerts Now")
        test_btn.clicked.connect(self._test_alert)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(test_btn)
        lay.addLayout(btn_row)

        # ── Alert history log ─────────────────────────
        log_grp = QGroupBox("ALERT HISTORY")
        loglay = QVBoxLayout(log_grp)
        loglay.setSpacing(4)

        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedWidth(90)
        clear_btn.clicked.connect(self._clear_alert_log)
        loglay.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self.alert_log_widget = QWidget()
        self.alert_log_layout = QVBoxLayout(self.alert_log_widget)
        self.alert_log_layout.setSpacing(3)
        self.alert_log_layout.setContentsMargins(0, 0, 0, 0)
        self.alert_log_layout.addStretch()

        log_scroll = QScrollArea()
        log_scroll.setWidget(self.alert_log_widget)
        log_scroll.setWidgetResizable(True)
        log_scroll.setMinimumHeight(180)
        log_scroll.setStyleSheet(f"background:{DARK}; border:1px solid {BORDER};")
        loglay.addWidget(log_scroll)
        lay.addWidget(log_grp)

        lay.addStretch()
        return w

    def _apply_alert_config(self):
        ALERT_CFG["enabled"]          = self.al_enabled.isChecked()
        self._refresh_alert_toggle()
        ALERT_CFG["interval_sec"]     = self.al_interval.value()
        ALERT_CFG["min_signal"]       = self.al_min_signal.currentText()
        ALERT_CFG["min_potential"]    = self.al_min_pot.value()
        ALERT_CFG["min_exp_move"]     = self.al_min_exp.value()
        ALERT_CFG["sound"]            = self.al_sound.isChecked()
        ALERT_CFG["desktop"]          = self.al_desktop.isChecked()
        ALERT_CFG["telegram"]         = self.al_tg.isChecked()
        ALERT_CFG["tg_token"]         = self.al_tg_token.text().strip()
        ALERT_CFG["tg_chat_id"]       = self.al_tg_chat.text().strip()
        ALERT_CFG["whatsapp"]         = self.al_wa.isChecked()
        ALERT_CFG["wa_number"]        = self.al_wa_number.text().strip()
        ALERT_CFG["picoclaw_queue"]   = self.al_wa_queue.text().strip()
        self.statusBar().showMessage(
            f"Alert settings applied — auto-scan every {ALERT_CFG['interval_sec']}s")
        s = self._settings
        for k, v in ALERT_CFG.items():
            s.setValue(f"alert_{k}", v)

    def _test_alert(self):
        """Fire a fake alert to test all enabled channels."""
        fake = {
            "time":        datetime.now().strftime("%H:%M:%S"),
            "symbol":      "TEST",
            "signal":      "STRONG BUY",
            "price":       0.04567,
            "rsi":         27.3,
            "exp":         8.4,
            "pot":         81,
            "pattern":     "Hammer ↑",
            "vol":         2.4,
            "macd_rising": True,
        }
        self._alert_engine._fire(fake)
        self._on_new_alert(fake)
        self.statusBar().showMessage("Test alert fired — check sound / desktop / Telegram / WhatsApp queue")

    def _copy_picoclaw_config(self):
        """Copy the PicoClaw config.json WhatsApp snippet to clipboard."""
        queue = ALERT_CFG["picoclaw_queue"].replace("\\", "/")
        snippet = f'''\
Add this to ~/.picoclaw/config.json inside the "channels" object:

  "whatsapp": {{
    "enabled": true,
    "use_native": true,
    "allow_from": ["{ALERT_CFG["wa_number"] or "YOUR_NUMBER_HERE"}"]
  }}

Full minimal config.json example:
{{
  "agents": {{
    "defaults": {{
      "model": "anthropic/claude-sonnet-4-6",
      "workspace": "~/.picoclaw/workspace"
    }}
  }},
  "providers": {{
    "anthropic": {{
      "api_key": "YOUR_ANTHROPIC_API_KEY"
    }}
  }},
  "channels": {{
    "whatsapp": {{
      "enabled": true,
      "use_native": true,
      "allow_from": ["{ALERT_CFG["wa_number"] or "923001234567"}"]
    }}
  }},
  "heartbeat": {{
    "enabled": true,
    "interval": 1
  }}
}}

Then run:  picoclaw gateway
Scan QR in WhatsApp → Settings → Linked Devices → Link a Device
'''
        QApplication.clipboard().setText(snippet)
        self.statusBar().showMessage("PicoClaw config snippet copied to clipboard!")

    def _copy_picoclaw_heartbeat(self):
        """Copy the HEARTBEAT.md task that delivers WhatsApp alerts."""
        queue = ALERT_CFG["picoclaw_queue"].replace("\\", "/")
        number = ALERT_CFG["wa_number"] or "923001234567"
        heartbeat = f'''\
# Crypto Scanner Alert Delivery

## Send pending crypto alerts via WhatsApp

Read the file `{queue}` as JSON.
Find all entries where "sent" is false.
For each unsent entry:
  - Send a WhatsApp message to "{number}@s.whatsapp.net" with the text from the "text" field
  - Mark that entry's "sent" field as true
Write the updated JSON back to `{queue}`.
If the file does not exist or is empty, do nothing and respond HEARTBEAT_OK.
'''
        QApplication.clipboard().setText(heartbeat)
        self.statusBar().showMessage("HEARTBEAT.md task copied to clipboard — paste into ~/.picoclaw/workspace/HEARTBEAT.md")

    def _on_new_alert(self, alert):
        """Called on main thread via signal — add to log + trigger all visual alerts."""
        self._alert_log.append(alert)
        sig = alert["signal"]
        sym = alert["symbol"]
        col = GREEN if "BUY" in sig else RED

        # ── Visual alerts ────────────────────────────────────────────── #
        self._flash_window(sig)                   # full-window color flash
        self._start_title_flash(sig, sym)         # taskbar / title bar flash
        self._update_status_alert(sig, sym)       # status bar stays colored
        if "STRONG" in sig:
            self._show_strong_popup(alert)        # popup only for STRONG signals

        row_w = QFrame()
        row_w.setStyleSheet(
            f"background:{CARD}; border-left:3px solid {col}; "
            f"border-radius:3px; margin:1px 0;")
        rlay  = QHBoxLayout(row_w)
        rlay.setContentsMargins(8, 5, 8, 5)
        rlay.setSpacing(12)

        time_lbl = QLabel(alert["time"])
        time_lbl.setStyleSheet(f"color:{DIM}; font-size:11px; font-family:{MONO_CSS};")
        time_lbl.setMinimumWidth(54)

        sym_lbl = QLabel(alert["symbol"])
        sym_lbl.setStyleSheet(f"color:{ACCENT}; font-family:{MONO_CSS}; font-weight:700; font-size:13px;")
        sym_lbl.setMinimumWidth(80)

        sig_lbl = QLabel(sig)
        sig_lbl.setStyleSheet(f"color:{col}; font-weight:700; font-size:12px;")
        sig_lbl.setMinimumWidth(110)

        detail_lbl = QLabel(
            f"RSI {alert['rsi']:.0f}  ·  Exp {alert['exp']:.1f}%  ·  "
            f"Pot {alert['pot']}%  ·  Vol {alert['vol']:.1f}x  ·  {alert['pattern']}")
        detail_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")

        price_lbl = QLabel(f"${alert['price']:.5f}")
        price_lbl.setStyleSheet(f"color:{WHITE}; font-family:{MONO_CSS}; font-size:12px;")
        price_lbl.setMinimumWidth(80)
        price_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        rlay.addWidget(time_lbl)
        rlay.addWidget(sym_lbl)
        rlay.addWidget(sig_lbl)
        rlay.addWidget(detail_lbl)
        rlay.addStretch()
        rlay.addWidget(price_lbl)

        # Insert at top of log (newest first) — before the stretch at end
        count = self.alert_log_layout.count()
        self.alert_log_layout.insertWidget(count - 1, row_w)

    def _on_alert_scan_done(self, results):
        """Background alert scan completed — update table silently if no manual scan running."""
        if self._worker is None or not self._worker.isRunning():
            self._results = results
            self._refresh_display()
            self._populate_picks(results)
            self._check_sltp_hits(results)     # auto-close SL/TP hit trades
            self._refresh_trades_table()       # update unrealised P&L on open trades
            n = len(results)
            self.statusBar().showMessage(
                f"Auto-scan: {n} coins  [{datetime.now().strftime('%H:%M:%S')}]")

    def _clear_alert_log(self):
        self._alert_log.clear()
        while self.alert_log_layout.count() > 1:  # keep the stretch
            item = self.alert_log_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ------------------------------------------------------------------ #
    #  VISUAL ALERT METHODS                                                #
    # ------------------------------------------------------------------ #

    def _flash_window(self, signal):
        """Flash a full-window color overlay using QTimer — robust, no animation lifecycle issues."""
        is_buy    = "BUY" in signal
        is_strong = "STRONG" in signal
        color     = "#00ee77" if is_buy else "#ee2222"
        flashes   = 3 if is_strong else 2

        # Safely destroy any previous overlay without calling deleteLater on dead object
        if self._flash_overlay is not None:
            try:
                self._flash_overlay.hide()
                self._flash_overlay.setParent(None)
            except RuntimeError:
                pass
            self._flash_overlay = None
        if self._flash_anim is not None:
            try:
                self._flash_anim.stop()
            except RuntimeError:
                pass
            self._flash_anim = None

        overlay = QWidget(self.centralWidget())
        overlay.setGeometry(self.centralWidget().rect())
        overlay.setStyleSheet(f"background-color: {color};")
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        overlay.raise_()
        overlay.show()
        self._flash_overlay = overlay

        # Toggle visibility on/off — simple, no C++ object lifetime issues
        state    = {"count": flashes * 2}   # on+off counts as 2 each flash
        interval = 110                       # ms per toggle

        timer = QTimer(self)
        self._flash_anim = timer

        def tick():
            state["count"] -= 1
            try:
                if state["count"] <= 0:
                    timer.stop()
                    overlay.hide()
                    overlay.setParent(None)
                    self._flash_overlay = None
                    self._flash_anim    = None
                else:
                    overlay.setVisible(state["count"] % 2 == 0)
            except RuntimeError:
                timer.stop()

        timer.timeout.connect(tick)
        timer.start(interval)

    def _start_title_flash(self, signal, symbol):
        """Flash the window title bar with the alert — visible in taskbar too."""
        is_buy = "BUY" in signal
        arrow  = "🚀" if is_buy else "🔴"
        self._title_flash_msg   = f"{arrow} {signal}: {symbol}"
        self._title_flash_count = 20   # 10 flashes (on/off × 20)
        self._title_flash_state = False
        self._title_flash_timer.start(400)

    def _flash_title_tick(self):
        if self._title_flash_count <= 0:
            self._title_flash_timer.stop()
            self.setWindowTitle("Crypto Scalp Scanner")
            return
        self._title_flash_state = not self._title_flash_state
        if self._title_flash_state:
            self.setWindowTitle(f"⚡ {self._title_flash_msg} ⚡")
        else:
            self.setWindowTitle("Crypto Scalp Scanner")
        self._title_flash_count -= 1

    def _show_strong_popup(self, alert):
        """Non-blocking popup dialog for STRONG signals — auto-dismisses after 15s."""
        sig    = alert["signal"]
        sym    = alert["symbol"]
        is_buy = "BUY" in sig
        color  = "#00ff88" if is_buy else "#ff4444"
        arrow  = "🚀" if is_buy else "🔻"
        direction = "LONG" if is_buy else "SHORT"

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{arrow} {sig}")
        dlg.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint
        )
        dlg.setStyleSheet(
            f"background: #0d0d0d; border: 3px solid {color}; border-radius: 10px;"
        )
        dlg.setFixedWidth(420)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        # Header row
        hdr = QLabel(f"{arrow}  {sig}")
        hdr.setStyleSheet(
            f"color: {color}; font-size: 22px; font-weight: 900; "
            f"font-family: monospace; border: none;"
        )
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sym_lbl = QLabel(sym)
        sym_lbl.setStyleSheet(
            "color: #ffffff; font-size: 32px; font-weight: 900; "
            "font-family: monospace; border: none;"
        )
        sym_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        dir_lbl = QLabel(f"[ {direction} ]")
        dir_lbl.setStyleSheet(
            f"color: {color}; font-size: 16px; font-weight: 700; "
            f"font-family: monospace; border: none;"
        )
        dir_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {color}; border: 1px solid {color};")

        # Stats grid
        def stat_row(label, value, val_color="#e0e0e0"):
            w = QWidget()
            w.setStyleSheet("border: none; background: transparent;")
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #888; font-size: 13px; font-family: monospace; border: none;")
            val = QLabel(value)
            val.setStyleSheet(f"color: {val_color}; font-size: 13px; font-weight: 700; font-family: monospace; border: none;")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hl.addWidget(lbl)
            hl.addStretch()
            hl.addWidget(val)
            return w

        price_row   = stat_row("Price",       f"${alert['price']:.5f}", "#ffffff")
        rsi_row     = stat_row("RSI",          f"{alert['rsi']:.1f}", "#f0c040")
        exp_row     = stat_row("Exp Move",     f"{alert['exp']:.1f}%", color)
        pot_row     = stat_row("Potential",    f"{alert['pot']}%", color)
        vol_row     = stat_row("Volume",       f"{alert['vol']:.1f}x avg", "#aaaaaa")
        pat_row     = stat_row("Pattern",      alert["pattern"], "#cccccc")

        # Countdown + dismiss button
        countdown_lbl = QLabel("Auto-dismiss in 15s")
        countdown_lbl.setStyleSheet(
            "color: #555; font-size: 11px; border: none; font-family: monospace;"
        )
        countdown_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        dismiss_btn = QPushButton("✕  Dismiss")
        dismiss_btn.setStyleSheet(
            f"background: {color}22; color: {color}; border: 1px solid {color}; "
            f"border-radius: 5px; padding: 6px 20px; font-weight: 700; font-size: 13px;"
        )
        dismiss_btn.clicked.connect(dlg.accept)

        lay.addWidget(hdr)
        lay.addWidget(sym_lbl)
        lay.addWidget(dir_lbl)
        lay.addWidget(sep)
        lay.addWidget(price_row)
        lay.addWidget(rsi_row)
        lay.addWidget(exp_row)
        lay.addWidget(pot_row)
        lay.addWidget(vol_row)
        lay.addWidget(pat_row)
        lay.addSpacing(6)
        lay.addWidget(countdown_lbl)
        lay.addWidget(dismiss_btn)

        # Position: top-right corner of main window
        geo  = self.geometry()
        dlg.adjustSize()
        dlg.move(geo.right() - dlg.width() - 20, geo.top() + 60)

        # Auto-dismiss countdown
        remaining = [15]
        def tick():
            remaining[0] -= 1
            countdown_lbl.setText(f"Auto-dismiss in {remaining[0]}s")
            if remaining[0] <= 0:
                timer.stop()
                dlg.accept()
        timer = QTimer(dlg)
        timer.timeout.connect(tick)
        timer.start(1000)

        dlg.show()   # non-blocking — user can keep trading

    def _update_status_alert(self, signal, symbol):
        """Paint the status bar red/green with the alert until next scan clears it."""
        is_buy = "BUY" in signal
        color  = "#00cc66" if is_buy else "#cc2222"
        self.statusBar().setStyleSheet(f"background: {color}; color: #ffffff; font-weight: 700;")
        self.statusBar().showMessage(
            f"  ⚡ {signal}: {symbol}  —  click Scan to refresh"
        )
        self._status_alert_active = True

        # Auto-clear after 60s so it doesn't stay forever
        QTimer.singleShot(60000, self._clear_status_alert)

    def _clear_status_alert(self):
        if self._status_alert_active:
            self.statusBar().setStyleSheet("")
            self.statusBar().showMessage("Ready")
            self._status_alert_active = False

    # ------------------------------------------------------------------ #
    #  CONFIG TAB                                                          #
    # ------------------------------------------------------------------ #

    def _build_config_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)

        # Filter settings
        filter_grp = QGroupBox("SCAN FILTERS")
        flay = QGridLayout(filter_grp)
        flay.setSpacing(12)

        self.cfg_max_price = QDoubleSpinBox()
        self.cfg_max_price.setRange(0.01, 100); self.cfg_max_price.setValue(CFG["max_price"])
        self.cfg_max_price.setPrefix("$"); self.cfg_max_price.setDecimals(2)

        self.cfg_min_vol = QDoubleSpinBox()
        self.cfg_min_vol.setRange(100000, 1e9); self.cfg_min_vol.setValue(CFG["min_volume_usdt"])
        self.cfg_min_vol.setPrefix("$"); self.cfg_min_vol.setDecimals(0)
        self.cfg_min_vol.setSingleStep(100000)

        self.cfg_interval = QComboBox()
        for iv in ["1m","3m","5m","15m","30m","1h"]:
            self.cfg_interval.addItem(iv)
        self.cfg_interval.setCurrentText(CFG["interval"])

        self.cfg_top_n = QSpinBox()
        self.cfg_top_n.setRange(5, 100); self.cfg_top_n.setValue(CFG["top_n"])

        self.cfg_candles = QSpinBox()
        self.cfg_candles.setRange(20, 200); self.cfg_candles.setValue(CFG["candle_limit"])

        rows = [
            ("Max Price ($)",      self.cfg_max_price),
            ("Min Volume (USDT)",  self.cfg_min_vol),
            ("Interval",           self.cfg_interval),
            ("Top N coins",        self.cfg_top_n),
            ("Candles to fetch",   self.cfg_candles),
        ]
        for i, (lbl, widget) in enumerate(rows):
            l = QLabel(lbl); l.setStyleSheet(f"color:{DIM};")
            flay.addWidget(l, i, 0)
            flay.addWidget(widget, i, 1)

        lay.addWidget(filter_grp)

        # Risk settings
        risk_grp = QGroupBox("RISK MANAGEMENT")
        rlay = QGridLayout(risk_grp)
        rlay.setSpacing(12)

        self.cfg_sl  = QDoubleSpinBox(); self.cfg_sl.setRange(0.5, 20); self.cfg_sl.setValue(CFG["sl_pct"]); self.cfg_sl.setSuffix("%")
        self.cfg_tp  = QDoubleSpinBox(); self.cfg_tp.setRange(0.5, 50); self.cfg_tp.setValue(CFG["tp_pct"]); self.cfg_tp.setSuffix("%")
        self.cfg_tp2 = QDoubleSpinBox(); self.cfg_tp2.setRange(1.0, 100); self.cfg_tp2.setValue(CFG["tp2_pct"]); self.cfg_tp2.setSuffix("%")

        self.rr_lbl = QLabel()
        self._update_rr_label()
        self.cfg_sl.valueChanged.connect(self._update_rr_label)
        self.cfg_tp.valueChanged.connect(self._update_rr_label)

        risk_rows = [
            ("Stop Loss %",     self.cfg_sl),
            ("Take Profit %",   self.cfg_tp),
            ("TP2 % (extended)",self.cfg_tp2),
        ]
        for i, (lbl, widget) in enumerate(risk_rows):
            l = QLabel(lbl); l.setStyleSheet(f"color:{DIM};")
            rlay.addWidget(l, i, 0)
            rlay.addWidget(widget, i, 1)

        rr_lbl_title = QLabel("R/R Ratio"); rr_lbl_title.setStyleSheet(f"color:{DIM};")
        rlay.addWidget(rr_lbl_title, len(risk_rows), 0)
        rlay.addWidget(self.rr_lbl, len(risk_rows), 1)
        lay.addWidget(risk_grp)

        # ── UI Appearance ────────────────────────────────
        ui_grp = QGroupBox("UI APPEARANCE")
        ulay   = QGridLayout(ui_grp)
        ulay.setSpacing(12)

        self.cfg_font_size = QSpinBox()
        self.cfg_font_size.setRange(8, 20)
        self.cfg_font_size.setValue(FONT_SIZE)
        self.cfg_font_size.setSuffix(" px")
        self.cfg_font_size.setToolTip("Base font size — all text scales proportionally")

        fs_lbl = QLabel("Font Size")
        fs_lbl.setStyleSheet(f"color:{DIM};")
        fs_hint = QLabel("Resize the window to test layout at any font size")
        fs_hint.setStyleSheet(f"color:{DIM}; font-size:10px;")

        ulay.addWidget(fs_lbl,            0, 0)
        ulay.addWidget(self.cfg_font_size, 0, 1)
        ulay.addWidget(fs_hint,            1, 0, 1, 2)
        lay.addWidget(ui_grp)

        # ── Alert Master Toggle ──────────────────────────
        alert_grp = QGroupBox("ALERTS")
        alay = QHBoxLayout(alert_grp)
        alay.setSpacing(12)

        self.cfg_alert_enabled = QPushButton()
        self.cfg_alert_enabled.setCheckable(True)
        self.cfg_alert_enabled.setChecked(ALERT_CFG["enabled"])
        self.cfg_alert_enabled.setFixedHeight(34)
        self._refresh_alert_toggle()
        self.cfg_alert_enabled.clicked.connect(self._on_alert_toggle)

        alay.addWidget(self.cfg_alert_enabled)
        alay.addStretch()
        lay.addWidget(alert_grp)

        # ── Export ───────────────────────────────────────
        export_grp = QGroupBox("EXPORT SCAN RESULTS")
        elay = QHBoxLayout(export_grp)

        export_btn = QPushButton("↓  Export Last Scan to JSON")
        export_btn.setFixedHeight(34)
        export_btn.setStyleSheet(
            f"background:{CARD}; color:{ACCENT}; border:1px solid {ACCENT}; "
            f"border-radius:4px; font-weight:700; padding:0 14px;"
        )
        export_btn.clicked.connect(self._export)

        self.cfg_export_lbl = QLabel("No scan yet")
        self.cfg_export_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")

        elay.addWidget(export_btn)
        elay.addSpacing(12)
        elay.addWidget(self.cfg_export_lbl)
        elay.addStretch()
        lay.addWidget(export_grp)

        apply_btn = QPushButton("✓  Apply Settings")
        apply_btn.clicked.connect(self._apply_config)
        lay.addWidget(apply_btn)
        lay.addStretch()
        return w

    def _update_rr_label(self):
        rr = self.cfg_tp.value() / self.cfg_sl.value()
        col = GREEN if rr >= 1.5 else YELLOW if rr >= 1 else RED
        self.rr_lbl.setText(f"{rr:.2f}x")
        self.rr_lbl.setStyleSheet(f"color:{col}; font-family:{MONO_CSS}; font-weight:800; font-size:14px;")

    def _update_filter_label(self):
        pass

    def _apply_config(self):
        global FONT_SIZE
        CFG["max_price"]       = self.cfg_max_price.value()
        CFG["min_volume_usdt"] = self.cfg_min_vol.value()
        CFG["interval"]        = self.cfg_interval.currentText()
        CFG["top_n"]           = self.cfg_top_n.value()
        CFG["candle_limit"]    = self.cfg_candles.value()
        CFG["sl_pct"]          = self.cfg_sl.value()
        CFG["tp_pct"]          = self.cfg_tp.value()
        CFG["tp2_pct"]         = self.cfg_tp2.value()

        # Font size — rebuild and reapply stylesheet if changed
        new_fs = self.cfg_font_size.value()
        if new_fs != FONT_SIZE:
            FONT_SIZE = new_fs
            QApplication.instance().setStyleSheet(make_stylesheet(FONT_SIZE))
            self._settings.setValue("fontSize", FONT_SIZE)

        self.statusBar().showMessage(
            f"Config applied — font {FONT_SIZE}px  |  press Scan to refresh"
        )

    def _restore_settings(self):
        global FONT_SIZE
        s = self._settings

        # Font size — restore first so stylesheet is correct before layout
        saved_fs = s.value("fontSize")
        if saved_fs is not None:
            FONT_SIZE = int(saved_fs)
            self.cfg_font_size.setValue(FONT_SIZE)
            QApplication.instance().setStyleSheet(make_stylesheet(FONT_SIZE))

        # Window geometry
        geo = s.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            # Start at 85% of available screen — responsive to any monitor
            screen = QApplication.primaryScreen().availableGeometry()
            w = int(screen.width()  * 0.85)
            h = int(screen.height() * 0.85)
            self.resize(w, h)
            self.move(
                screen.x() + (screen.width()  - w) // 2,
                screen.y() + (screen.height() - h) // 2
            )
        # Window state (maximised etc)
        state = s.value("windowState")
        if state:
            self.restoreState(state)
        # Column widths — always reflow proportionally on start
        QTimer.singleShot(0, self._reflow_columns)

        # Alert settings
        for k, default in ALERT_CFG.items():
            saved = s.value(f"alert_{k}")
            if saved is not None:
                # QSettings returns strings — cast to original type
                try:
                    if isinstance(default, bool):
                        ALERT_CFG[k] = saved in (True, "true", "True", "1")
                    elif isinstance(default, int):
                        ALERT_CFG[k] = int(saved)
                    elif isinstance(default, float):
                        ALERT_CFG[k] = float(saved)
                    else:
                        ALERT_CFG[k] = str(saved)
                except Exception:
                    pass
        # Reflect loaded alert settings back into the alert tab widgets
        try:
            self.al_enabled.setChecked(ALERT_CFG["enabled"])
            self.al_interval.setValue(int(ALERT_CFG["interval_sec"]))
            self.al_min_signal.setCurrentText(ALERT_CFG["min_signal"])
            self.al_min_pot.setValue(int(ALERT_CFG["min_potential"]))
            self.al_min_exp.setValue(float(ALERT_CFG["min_exp_move"]))
            self.al_sound.setChecked(ALERT_CFG["sound"])
            self.al_desktop.setChecked(ALERT_CFG["desktop"])
            self.al_tg.setChecked(ALERT_CFG["telegram"])
            self.al_tg_token.setText(ALERT_CFG["tg_token"])
            self.al_tg_chat.setText(ALERT_CFG["tg_chat_id"])
            self.al_wa.setChecked(ALERT_CFG["whatsapp"])
            self.al_wa_number.setText(ALERT_CFG["wa_number"])
            self.al_wa_queue.setText(ALERT_CFG["picoclaw_queue"])
        except Exception:
            pass

    def _save_settings(self):
        s = self._settings
        s.setValue("geometry",    self.saveGeometry())
        s.setValue("windowState", self.saveState())
        s.setValue("fontSize",    FONT_SIZE)
        s.sync()

    def _refresh_alert_toggle(self):
        """Refresh the config tab alert toggle button appearance."""
        if not hasattr(self, 'cfg_alert_enabled'):
            return
        on = ALERT_CFG["enabled"]
        self.cfg_alert_enabled.setChecked(on)
        if on:
            self.cfg_alert_enabled.setText("🔔  Alerts are ON  —  click to disable")
            self.cfg_alert_enabled.setStyleSheet(
                f"background:#003a1a; color:{GREEN}; border:1px solid {GREEN}; "
                f"border-radius:4px; font-size:12px; font-weight:700; padding:0 14px;"
            )
        else:
            self.cfg_alert_enabled.setText("🔕  Alerts are OFF  —  click to enable")
            self.cfg_alert_enabled.setStyleSheet(
                f"background:#2a0000; color:{RED}; border:1px solid {RED}; "
                f"border-radius:4px; font-size:12px; font-weight:700; padding:0 14px;"
            )

    def _on_alert_toggle(self):
        ALERT_CFG["enabled"] = self.cfg_alert_enabled.isChecked()
        self._refresh_alert_toggle()
        if hasattr(self, 'al_enabled'):
            self.al_enabled.setChecked(ALERT_CFG["enabled"])
        state = "ON" if ALERT_CFG["enabled"] else "OFF"
        self.statusBar().showMessage(f"Alerts turned {state}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'table'):
            QTimer.singleShot(0, self._reflow_columns)

    def _reset_column_widths(self):
        for i in range(15):
            self._settings.remove(f"col_{i}")
        self._reflow_columns()
        self.statusBar().showMessage("Column widths reset to auto")

    def keyPressEvent(self, event):
        key  = event.key()
        mods = event.modifiers()
        # Close on Q or Ctrl+Q — but NOT on Escape
        if key == Qt.Key.Key_Q and (mods == Qt.KeyboardModifier.NoModifier or
                                     mods == Qt.KeyboardModifier.ControlModifier):
            self.close()
        elif key == Qt.Key.Key_Escape:
            event.ignore()   # swallow Escape — do nothing
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._alert_engine.stop()
        self._save_settings()
        super().closeEvent(event)

    def _setup_timer(self):
        self._progress_timer = QTimer()
        self._progress_timer.timeout.connect(self._poll_progress)

    def _start_scan(self):
        if self._worker and self._worker.isRunning():
            return
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("⏳  Scanning...")
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.table.setRowCount(0)
        self.status_lbl.setText("Fetching tickers...")

        self._scanner = Scanner()
        self._worker  = ScanWorker(self._scanner)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, done, total, status):
        if total > 0:
            self.progress.setMaximum(total)
            self.progress.setValue(done)
        self.status_lbl.setText(status[:80])

    def _poll_progress(self):
        pass

    def _on_finished(self, results):
        self._results = results
        self._refresh_display()
        self._populate_picks(results)
        self._check_sltp_hits(results)     # auto-close SL/TP hit trades
        self._refresh_trades_table()       # update unrealised P&L
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("⚡  SCAN")
        self.progress.setVisible(False)
        # export moved to config tab
        n = len(results)
        self.status_lbl.setText(f"Done — {n} coins  [{datetime.now().strftime('%H:%M:%S')}]")
        self._clear_status_alert()
        self.statusBar().showMessage(f"Scan complete — {n} coins analysed")

    def _refresh_display(self):
        """Populate table respecting current sort column and direction."""
        if self._sort_col is not None and self._results:
            key_fn = self._SORT_KEY.get(self._sort_col)
            if key_fn:
                self._results = sorted(
                    self._results,
                    key=lambda r: key_fn(r, 0),
                    reverse=not self._sort_asc
                )
        self._populate_table(self._results)
        if self._sort_col is not None:
            self._update_header_arrows()

    def _on_error(self, msg):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("⚡  SCAN")
        self.progress.setVisible(False)
        self.status_lbl.setText(f"Error: {msg[:60]}")
        self.statusBar().showMessage(f"Error: {msg}")

    # col index → function that extracts a numeric sort key from a result dict
    # col 14 (Chart), 18 (spacer) excluded — no sort
    _SORT_KEY = {
        0:  lambda r, i: i,                                         # #
        1:  lambda r, i: r["symbol"],                               # Symbol (alpha)
        2:  lambda r, i: r["price"],                                # Price
        3:  lambda r, i: r["change_24h"],                           # 24h%
        4:  lambda r, i: r["rsi"],                                  # RSI
        5:  lambda r, i: r["stoch_rsi"],                            # StRSI
        6:  lambda r, i: r["macd_hist"],                            # MACD
        7:  lambda r, i: (                                          # BB%
                (r["price"] - r["bb_lower"]) / (r["bb_upper"] - r["bb_lower"]) * 100
                if r.get("bb_upper") and r.get("bb_lower") and r["bb_upper"] != r["bb_lower"]
                else 50.0),
        8:  lambda r, i: r["volume_24h"],                           # Vol 24h
        9:  lambda r, i: {"STRONG BUY":0,"BUY":1,                  # Signal tier
                           "NEUTRAL":2,"SELL":3,"STRONG SELL":4}.get(r["signal"], 2),
        10: lambda r, i: r.get("potential", 0),                     # Pot%
        11: lambda r, i: r.get("expected_move", 0),                 # Exp%
        12: lambda r, i: r.get("long_score", 0) - r.get("short_score", 0),  # L/S
        13: lambda r, i: r["pattern"],                              # Pattern (alpha)
        15: lambda r, i: (datetime.now() - r["signal_age"]).total_seconds() if r.get("signal_age") and r["signal"] != "NEUTRAL" else 99999,  # AGE
        16: lambda r, i: r.get("signal_conf", 0),                  # CONF
        17: lambda r, i: {"up": 0, "flat": 1, "down": 2}.get(r.get("trend_1h", "flat"), 1),  # 1H
    }

    def _on_header_clicked(self, col):
        if col in (14, 18):   # Chart and spacer — not sortable
            return
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc   # toggle direction
        else:
            self._sort_col = col
            self._sort_asc = True                  # first click = ascending
        self._apply_sort()
        self._update_header_arrows()

    def _apply_sort(self):
        if self._sort_col is None or not self._results:
            return
        self._refresh_display()

    def _update_header_arrows(self):
        cols = ["#", "Symbol", "Price", "24h%", "RSI", "StRSI",
                "MACD", "BB%", "Vol 24h", "Signal", "Pot%", "Exp%", "L/S", "Pattern", "Chart",
                "AGE", "CONF", "1H"]
        for i, base in enumerate(cols):
            if i == self._sort_col:
                arrow = " ▲" if self._sort_asc else " ▼"
                self.table.horizontalHeaderItem(i).setText(base + arrow)
            else:
                self.table.horizontalHeaderItem(i).setText(base)

    def _populate_table(self, results):
        self.table.setRowCount(0)

        for idx, r in enumerate(results):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 38)

            sig   = r["signal"]
            chg   = r["change_24h"]
            rsi   = r["rsi"]
            srsi  = r["stoch_rsi"]
            mh    = r["macd_hist"]
            pot   = r.get("potential", 0)
            exp   = r.get("expected_move", 0)
            vol_m = r["volume_24h"] / 1_000_000
            sym   = r["symbol"].replace("USDT", "")

            # row background
            if   sig == "STRONG BUY":  row_bg = QColor(STRONG_BUY_BG)
            elif sig == "STRONG SELL": row_bg = QColor(STRONG_SELL_BG)
            elif sig == "BUY":         row_bg = QColor(BUY_BG)
            elif sig == "SELL":        row_bg = QColor(SELL_BG)
            else:                      row_bg = None

            def cell(text, color=WHITE, bold=False,
                     align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                     sort_val=None):
                item = QTableWidgetItem(str(text))
                item.setForeground(QBrush(QColor(color)))
                if row_bg:
                    item.setBackground(QBrush(row_bg))
                if bold:
                    f = item.font(); f.setBold(True); item.setFont(f)
                item.setTextAlignment(align)
                # store raw numeric value for sorting
                if sort_val is not None:
                    item.setData(Qt.ItemDataRole.UserRole, sort_val)
                return item

            left = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft

            # BB% numeric value
            bb_num = 50.0
            bb_pos = "—"
            if r.get("bb_upper") and r.get("bb_lower") and r["bb_upper"] != r["bb_lower"]:
                bb_num = (r["price"] - r["bb_lower"]) / (r["bb_upper"] - r["bb_lower"]) * 100
                bb_pos = f"{bb_num:.0f}%"

            self.table.setItem(row, 0,  cell(str(idx+1), DIM, sort_val=idx))
            self.table.setItem(row, 1,  cell(sym, ACCENT, bold=True, align=left, sort_val=sym))
            self.table.setItem(row, 2,  cell(f"${r['price']:.5f}", WHITE, sort_val=r["price"]))
            self.table.setItem(row, 3,  cell(f"{chg:+.1f}%", GREEN if chg >= 0 else RED, sort_val=chg))
            self.table.setItem(row, 4,  cell(f"{rsi:.1f}", GREEN if rsi < 40 else RED if rsi > 60 else YELLOW, sort_val=rsi))
            self.table.setItem(row, 5,  cell(f"{srsi:.1f}", GREEN if srsi < 30 else RED if srsi > 70 else YELLOW, sort_val=srsi))
            self.table.setItem(row, 6,  cell("▲" if mh > 0 else "▼", GREEN if mh > 0 else RED, sort_val=mh))
            self.table.setItem(row, 7,  cell(bb_pos, YELLOW, sort_val=bb_num))
            self.table.setItem(row, 8,  cell(f"${vol_m:.1f}M", ACCENT, sort_val=r["volume_24h"]))

            sig_tier = {"STRONG BUY":0,"BUY":1,"NEUTRAL":2,"SELL":3,"STRONG SELL":4}.get(sig, 2)
            sig_item = QTableWidgetItem(sig)
            sig_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sig_c = GREEN if "BUY" in sig else RED if "SELL" in sig else DIM
            sig_item.setForeground(QBrush(QColor(sig_c)))
            if row_bg: sig_item.setBackground(QBrush(row_bg))
            f = sig_item.font(); f.setBold("STRONG" in sig); sig_item.setFont(f)
            sig_item.setData(Qt.ItemDataRole.UserRole, sig_tier)
            self.table.setItem(row, 9, sig_item)

            pot_c = GREEN if pot >= 70 else YELLOW if pot >= 40 else RED
            exp_c = GREEN if exp >= 8  else GREEN  if exp >= 5  else YELLOW
            ls_val = r.get("long_score", 0) - r.get("short_score", 0)
            self.table.setItem(row, 10, cell(f"{pot}%",   pot_c, sort_val=pot))
            self.table.setItem(row, 11, cell(f"{exp:.1f}%", exp_c, sort_val=exp))
            self.table.setItem(row, 12, cell(f"L{r.get('long_score',0)}/S{r.get('short_score',0)}", DIM, sort_val=ls_val))
            self.table.setItem(row, 13, cell(r["pattern"], DIM, align=left, sort_val=r["pattern"]))

            # Sparkline — no sort
            candles = r.get("candles", [])
            if candles:
                closes   = [c["close"] for c in candles[-20:]]
                trend_up = closes[-1] > closes[0]
                spark    = Sparkline(closes, GREEN if trend_up else RED)
                self.table.setCellWidget(row, 14, spark)

            # Signal age (col 15)
            sig_age_dt = r.get("signal_age")
            if sig_age_dt and sig != "NEUTRAL":
                age_secs = int((datetime.now() - sig_age_dt).total_seconds())
                if age_secs < 60:
                    age_str  = f"{age_secs}s"
                    age_col  = GREEN if age_secs < 30 else YELLOW
                else:
                    age_mins = age_secs // 60
                    age_str  = f"{age_mins}m"
                    age_col  = YELLOW if age_mins < 5 else RED
                age_sort = age_secs
            else:
                age_str, age_col, age_sort = "—", DIM, 99999
            self.table.setItem(row, 15, cell(age_str, age_col, sort_val=age_sort))

            # Signal confirmation count (col 16)
            conf = r.get("signal_conf", 0) if sig != "NEUTRAL" else 0
            if conf == 0:
                conf_str = "—"
                conf_col = DIM
            elif conf == 1:
                conf_str = "▮░░░░"
                conf_col = YELLOW
            elif conf == 2:
                conf_str = "▮▮░░░"
                conf_col = YELLOW
            elif conf == 3:
                conf_str = "▮▮▮░░"
                conf_col = GREEN
            elif conf == 4:
                conf_str = "▮▮▮▮░"
                conf_col = GREEN
            else:
                conf_str = "▮▮▮▮▮"
                conf_col = ACCENT
            self.table.setItem(row, 16, cell(conf_str, conf_col, sort_val=conf))

            # 1H trend filter (col 17)
            trend_1h = r.get("trend_1h", "flat")
            if trend_1h == "up":
                t1h_str = "↑"
                # Aligned with BUY signal = good, with SELL = warning
                t1h_col = GREEN if "BUY" in sig else (RED if "SELL" in sig else ACCENT)
                t1h_sort = 0
            elif trend_1h == "down":
                t1h_str = "↓"
                t1h_col = RED if "BUY" in sig else (GREEN if "SELL" in sig else RED)
                t1h_sort = 2
            else:
                t1h_str = "→"
                t1h_col = DIM
                t1h_sort = 1
            self.table.setItem(row, 17, cell(t1h_str, t1h_col,
                align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter,
                sort_val=t1h_sort))

        # Restore sort arrows if active
        if self._sort_col is not None:
            self._update_header_arrows()


    def _populate_picks(self, results):
        # Clear picks tab
        while self.picks_lay.count():
            item = self.picks_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        buys  = [r for r in results if "BUY"  in r["signal"]]
        sells = [r for r in results if "SELL" in r["signal"]]

        for section_name, section_results, color in [
            ("🟢  LONG CANDIDATES", buys,  GREEN),
            ("🔴  SHORT CANDIDATES", sells, RED),
        ]:
            lbl = QLabel(section_name)
            lbl.setStyleSheet(f"color:{color}; font-size:15px; font-weight:800; padding:8px 0 4px 0;")
            self.picks_lay.addWidget(lbl)

            if not section_results:
                none_lbl = QLabel("None this scan.")
                none_lbl.setStyleSheet(f"color:{DIM}; padding:4px 16px;")
                self.picks_lay.addWidget(none_lbl)
                continue

            for r in section_results:
                self.picks_lay.addWidget(self._build_pick_card(r, color == GREEN))

        self.picks_lay.addStretch()

    def _build_pick_card(self, r, is_long):
        price    = r["price"]
        sl_pct   = CFG["sl_pct"]
        tp_pct   = CFG["tp_pct"]
        tp2_pct  = CFG["tp2_pct"]
        sl       = round(price * (1 - sl_pct/100)  if is_long else price * (1 + sl_pct/100),  8)
        tp1      = round(price * (1 + tp_pct/100)  if is_long else price * (1 - tp_pct/100),  8)
        tp2      = round(price * (1 + tp2_pct/100) if is_long else price * (1 - tp2_pct/100), 8)
        rr       = round(tp_pct / sl_pct, 2)
        accent   = GREEN if is_long else RED
        sym      = r["symbol"].replace("USDT", "/USDT")
        sym_bare = r["symbol"].replace("USDT", "")
        pot      = r.get("potential", 0)
        exp      = r.get("expected_move", 0)
        sig      = r["signal"]
        rsi      = r.get("rsi", 50)
        strsi    = r.get("stoch_rsi", 50)
        bb_pct   = r.get("bb_pct", 50)
        macd_h   = r.get("macd_hist", 0)
        chg24    = r.get("change_24h", 0)
        vol_r    = r.get("vol_ratio", 1)
        pattern  = r.get("pattern", "—")
        trend_1h = r.get("trend_1h", "flat")
        conf     = r.get("signal_conf", 1)
        age_dt   = r.get("signal_age")
        support  = r.get("support", 0)
        resist   = r.get("resist", 0)
        lscore   = r.get("long_score", 0)
        sscore   = r.get("short_score", 0)
        total_sc = lscore + sscore
        win_sc   = lscore if is_long else sscore

        # Age string
        if age_dt and sig != "NEUTRAL":
            secs = int((datetime.now() - age_dt).total_seconds())
            age_str = f"{secs}s" if secs < 60 else f"{secs//60}m{secs%60:02d}s"
            age_col = GREEN if secs < 30 else (YELLOW if secs < 300 else RED)
        else:
            age_str, age_col = "—", DIM

        # Conf bar
        conf_filled = min(conf, 5)
        conf_bar    = "▮" * conf_filled + "░" * (5 - conf_filled)
        conf_col    = ACCENT if conf >= 5 else (GREEN if conf >= 3 else YELLOW)

        # 1H trend
        trend_sym = {"up": "↑", "down": "↓", "flat": "→"}.get(trend_1h, "→")
        if (is_long and trend_1h == "up") or (not is_long and trend_1h == "down"):
            trend_col = GREEN
        elif (is_long and trend_1h == "down") or (not is_long and trend_1h == "up"):
            trend_col = RED
        else:
            trend_col = DIM

        # Distance to support/resistance
        if support > 0 and price > 0:
            sup_pct = (price - support) / price * 100
            sup_str = f"-{sup_pct:.1f}%"
        else:
            sup_str = "—"
        if resist > 0 and price > 0:
            res_pct = (resist - price) / price * 100
            res_str = f"+{res_pct:.1f}%"
        else:
            res_str = "—"

        badge_colors = {
            "STRONG BUY":  ("#003322", "#00ff88"),
            "BUY":         ("#002211", "#00cc66"),
            "STRONG SELL": ("#330011", "#ff3366"),
            "SELL":        ("#220011", "#cc2244"),
            "NEUTRAL":     ("#1a2235", "#4a5568"),
        }

        class PickCard(QWidget):
            def __init__(self_):
                super().__init__()
                self_.setMinimumHeight(148)
                self_.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            def paintEvent(self_, event):
                p = QPainter(self_)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                W, H = self_.width(), self_.height()

                # ── Card background + border ──────────────────
                p.setPen(QPen(QColor(BORDER), 1))
                p.setBrush(QBrush(QColor(CARD)))
                p.drawRoundedRect(1, 1, W-2, H-2, 7, 7)

                # Accent left bar
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(accent)))
                p.drawRoundedRect(1, 1, 4, H-2, 2, 2)

                L = 14   # left content margin

                def txt(x, y, text, color, pt=10, bold=False, mono=False,
                        align=Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop,
                        w=None, h=18):
                    f = mono_font(pt, bold) if mono else QFont()
                    if not mono:
                        f.setPointSize(pt); f.setBold(bold)
                    p.setFont(f)
                    p.setPen(QColor(color))
                    draw_w = (w if w is not None else W - x - 4)
                    fm = p.fontMetrics()
                    elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, draw_w)
                    p.drawText(x, y, draw_w, h, align, elided)

                def txt_w(text, pt=10, bold=False, mono=False):
                    f = mono_font(pt, bold) if mono else QFont()
                    if not mono:
                        f.setPointSize(pt); f.setBold(bold)
                    p.setFont(f)
                    return p.fontMetrics().horizontalAdvance(text)

                # ════════════════════════════════════════════
                # ROW 1  — Symbol | Badge | Pot | Exp | 24h%
                # ════════════════════════════════════════════
                y1 = 12

                # Pre-measure everything in row 1
                f_sym = mono_font(14, bold=True)
                p.setFont(f_sym)
                sym_w = p.fontMetrics().horizontalAdvance(sym)

                f_b = QFont(); f_b.setPointSize(8); f_b.setBold(True); p.setFont(f_b)
                btext = f" {sig} "
                bw = p.fontMetrics().horizontalAdvance(btext) + 4
                bh = 18

                pot_str = f"⚡{pot}%"
                exp_str = f"Exp {exp:.1f}%"
                chg_str = f"{'+'if chg24>=0 else ''}{chg24:.2f}% 24h"
                f_med = QFont(); f_med.setPointSize(10); f_med.setBold(True); p.setFont(f_med)
                pot_w  = p.fontMetrics().horizontalAdvance(pot_str) + 10
                exp_w  = p.fontMetrics().horizontalAdvance(exp_str) + 10
                chg_w  = p.fontMetrics().horizontalAdvance(chg_str) + 6

                # Layout: L | sym | gap | badge | gap | pot | exp | ... | chg (right)
                badge_x = L + sym_w + 8
                pot_x   = badge_x + bw + 10
                exp_x   = pot_x + pot_w
                chg_x   = W - chg_w - 8

                # Row 1 — draw everything using rect form so baseline is consistent
                ROW_H = 22   # height of row 1 text area
                ry = 6       # top of row 1 rect

                # Symbol
                p.setFont(f_sym)
                p.setPen(QColor(ACCENT))
                p.drawText(L, ry, sym_w + 2, ROW_H,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sym)

                # Signal badge — vertically centred in row
                bg_c, fg_c = badge_colors.get(sig, ("#1a2235","#4a5568"))
                p.setBrush(QBrush(QColor(bg_c))); p.setPen(QPen(QColor(fg_c), 1))
                p.drawRoundedRect(badge_x, ry + 2, bw, bh - 4, 3, 3)
                p.setFont(f_b); p.setPen(QColor(fg_c))
                p.drawText(badge_x, ry + 2, bw, bh - 4,
                           Qt.AlignmentFlag.AlignCenter, btext)

                # Pot%
                pot_col = "#00ff88" if pot >= 70 else (YELLOW if pot >= 40 else DIM)
                p.setFont(f_med); p.setPen(QColor(pot_col))
                p.drawText(pot_x, ry, pot_w, ROW_H,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pot_str)

                # Exp%
                exp_col = GREEN if exp >= 3 else DIM
                p.setPen(QColor(exp_col))
                p.drawText(exp_x, ry, exp_w, ROW_H,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, exp_str)

                # 24h% right-aligned
                chg_col = GREEN if chg24 >= 0 else RED
                p.setPen(QColor(chg_col))
                p.drawText(W - chg_w - 8, ry, chg_w, ROW_H,
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, chg_str)

                # ════════════════════════════════════════════
                # ROW 2  — Score bar | RSI bar | BB bar | 1H | Conf | Age
                # ════════════════════════════════════════════
                y2 = ry + ROW_H + 4
                bar_h = 6
                bar_r = 3

                # Score bar (0–10 scale)
                score_w = 90
                txt(L, y2, "Score", DIM, 8)
                bx = L + 38
                # background
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(bx, y2+2, score_w, bar_h, bar_r, bar_r)
                # fill
                fill_w = int(min(win_sc, 10) / 10 * score_w)
                p.setBrush(QBrush(QColor(accent)))
                p.drawRoundedRect(bx, y2+2, max(fill_w, 4), bar_h, bar_r, bar_r)
                txt(bx + score_w + 4, y2, f"{win_sc}/10", accent, 8, bold=True)

                # RSI mini bar
                rsi_x = bx + score_w + 44
                txt(rsi_x, y2, "RSI", DIM, 8)
                rbx = rsi_x + 24
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(rbx, y2+2, 60, bar_h, bar_r, bar_r)
                rsi_col = GREEN if rsi < 40 else (RED if rsi > 60 else YELLOW)
                p.setBrush(QBrush(QColor(rsi_col)))
                p.drawRoundedRect(rbx, y2+2, int(rsi/100*60), bar_h, bar_r, bar_r)
                txt(rbx+63, y2, f"{rsi:.0f}", rsi_col, 8, bold=True)

                # BB% mini bar
                bb_x = rbx + 90
                txt(bb_x, y2, "BB%", DIM, 8)
                bbx = bb_x + 24
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(bbx, y2+2, 60, bar_h, bar_r, bar_r)
                bb_col = GREEN if bb_pct < 25 else (RED if bb_pct > 75 else YELLOW)
                p.setBrush(QBrush(QColor(bb_col)))
                p.drawRoundedRect(bbx, y2+2, int(bb_pct/100*60), bar_h, bar_r, bar_r)
                txt(bbx+63, y2, f"{bb_pct:.0f}", bb_col, 8, bold=True)

                # Right side: 1H trend | CONF | AGE
                rx = W - 10
                # Age
                age_label = f"Age: {age_str}"
                txt(0, y2, age_label, age_col, 8, bold=True,
                    align=Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignTop, w=rx)
                # Conf bar above age
                conf_label = f"Conf: {conf_bar}"
                # 1H
                trend_label = f"1H: {trend_sym}"
                # Stack them right-aligned
                f_sm = QFont(); f_sm.setPointSize(8); p.setFont(f_sm)
                cw = p.fontMetrics().horizontalAdvance(conf_label) + 8
                tw = p.fontMetrics().horizontalAdvance(trend_label) + 8
                aw = p.fontMetrics().horizontalAdvance(age_label) + 8
                max_rw = max(cw, tw, aw)
                rx2 = W - max_rw - 6

                txt(rx2, y2, conf_label, conf_col, 8,
                    align=Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop)

                # ════════════════════════════════════════════
                # ROW 3  — Trade setup pills
                # ════════════════════════════════════════════
                y3 = y2 + 20
                pill_h = 34
                pill_items = [
                    ("Entry",  f"${price:.6f}", WHITE),
                    ("SL",     f"${sl:.6f}",    RED),
                    ("TP1",    f"${tp1:.6f}",   GREEN),
                    ("TP2",    f"${tp2:.6f}",   "#00cc66"),
                    ("R/R",    f"{rr:.2f}x",    YELLOW),
                    ("Sup",    sup_str,          "#00aaff"),
                    ("Res",    res_str,          "#ff6699"),
                ]

                px = L
                for plbl, pval, pcol in pill_items:
                    f_lbl = QFont(); f_lbl.setPointSize(7); p.setFont(f_lbl)
                    lw = p.fontMetrics().horizontalAdvance(plbl)
                    f_val = mono_font(9, bold=True); p.setFont(f_val)
                    vw = p.fontMetrics().horizontalAdvance(pval)
                    pw = max(lw, vw) + 14
                    if px + pw > W - 8:
                        break
                    # pill bg
                    p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(px, y3, pw, pill_h, 4, 4)
                    # label
                    p.setFont(f_lbl); p.setPen(QColor(DIM))
                    p.drawText(px, y3+2, pw, 14, Qt.AlignmentFlag.AlignCenter, plbl)
                    # value
                    p.setFont(f_val); p.setPen(QColor(pcol))
                    p.drawText(px, y3+16, pw, 16, Qt.AlignmentFlag.AlignCenter, pval)
                    px += pw + 5

                # 1H and vol right of pills
                txt(0, y3+8, f"1H {trend_sym}  Vol {vol_r:.1f}x  {pattern}",
                    DIM, 9,
                    align=Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignTop,
                    w=W-8)

                # ════════════════════════════════════════════
                # ROW 4  — MACD direction + StRSI + Vol ratio bar
                # ════════════════════════════════════════════
                y4 = y3 + pill_h + 6
                macd_str  = f"MACD {'▲ Positive' if macd_h > 0 else '▼ Negative'}  ({macd_h:+.4f})"
                macd_col  = GREEN if macd_h > 0 else RED
                strsi_str = f"StRSI {strsi:.0f}"
                strsi_col = GREEN if strsi < 30 else (RED if strsi > 70 else YELLOW)

                txt(L, y4, macd_str,  macd_col,  9, bold=True)
                mx = L + txt_w(macd_str, 9, bold=True) + 14
                txt(mx, y4, strsi_str, strsi_col, 9, bold=True)

                # Vol ratio mini bar far right
                vol_bar_w = 50
                vbx = W - vol_bar_w - 8
                txt(vbx - 28, y4, "Vol", DIM, 8)
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(vbx, y4+2, vol_bar_w, bar_h, bar_r, bar_r)
                vol_fill = min(int((vol_r / 5) * vol_bar_w), vol_bar_w)
                vol_col = "#00ff88" if vol_r >= 2 else (YELLOW if vol_r >= 1.2 else DIM)
                p.setBrush(QBrush(QColor(vol_col)))
                p.drawRoundedRect(vbx, y4+2, max(vol_fill,3), bar_h, bar_r, bar_r)
                txt(vbx + vol_bar_w + 3, y4, f"{vol_r:.1f}x", vol_col, 8, bold=True)

            def sizeHint(self_):
                from PyQt6.QtCore import QSize
                return QSize(500, 148)

            def minimumSizeHint(self_):
                from PyQt6.QtCore import QSize
                return QSize(400, 148)

        return PickCard()
    def _on_row_double_click(self, item):
        row = self.table.currentRow()
        if 0 <= row < len(self._results):
            self._show_detail_popup(self._results[row])

    def _show_detail_popup(self, r):
        """Floating overlay — closes on Escape, ✕ button, or click outside."""
        sig    = r["signal"]
        is_buy = "BUY" in sig
        accent = GREEN if is_buy else RED

        dlg = QDialog(self)
        dlg.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dlg.setModal(False)

        # ── Outer frame ────────────────────────────────
        outer = QFrame(dlg)
        outer.setObjectName("detailPopup")
        outer.setStyleSheet(f"""
            QFrame#detailPopup {{
                background: {DARK2};
                border: 2px solid {accent};
                border-radius: 12px;
            }}
        """)

        outer_lay = QVBoxLayout(dlg)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.addWidget(outer)

        main_lay = QVBoxLayout(outer)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # ── Title bar ──────────────────────────────────
        title_bar = QFrame()
        title_bar.setFixedHeight(42)
        title_bar.setStyleSheet(f"background: {accent}22; border-radius: 10px 10px 0 0;")
        tb_lay = QHBoxLayout(title_bar)
        tb_lay.setContentsMargins(16, 0, 12, 0)

        sym = r["symbol"].replace("USDT", "/USDT")
        sym_lbl = QLabel(sym)
        sym_lbl.setStyleSheet(f"color:{ACCENT}; font-size:18px; font-weight:900; font-family:{MONO_CSS};")

        price_lbl = QLabel(f"${r['price']:.6f}")
        price_lbl.setStyleSheet(f"color:{WHITE}; font-size:15px; font-weight:700; font-family:{MONO_CSS};")

        chg   = r["change_24h"]
        chg_c = GREEN if chg >= 0 else RED
        chg_lbl = QLabel(f"{chg:+.2f}%")
        chg_lbl.setStyleSheet(f"color:{chg_c}; font-size:13px; font-weight:700;")

        badge = SignalBadge(sig)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet(
            f"background: transparent; color: {DIM}; border: none; "
            f"font-size: 14px; font-weight: 700;"
        )
        close_btn.clicked.connect(dlg.accept)

        tb_lay.addWidget(sym_lbl)
        tb_lay.addSpacing(12)
        tb_lay.addWidget(price_lbl)
        tb_lay.addSpacing(8)
        tb_lay.addWidget(chg_lbl)
        tb_lay.addStretch()
        tb_lay.addWidget(badge)
        tb_lay.addSpacing(8)
        tb_lay.addWidget(close_btn)
        main_lay.addWidget(title_bar)

        # ── Scrollable detail content ──────────────────
        detail_panel = DetailPanel()
        detail_panel.load(r)
        main_lay.addWidget(detail_panel)

        # ── Hint footer ────────────────────────────────
        hint = QLabel("Right-click row in Scanner to open a trade  |  Click outside or Esc to close")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color:{DIM}; font-size:10px; padding:6px; background:transparent;")
        main_lay.addWidget(hint)

        # ── Size and position ──────────────────────────
        mw = self.geometry()
        w  = max(700, int(mw.width()  * 0.55))
        h  = max(600, int(mw.height() * 0.80))
        dlg.setFixedSize(w, h)
        dlg.move(
            mw.x() + (mw.width()  - w) // 2,
            mw.y() + (mw.height() - h) // 2
        )

        # ── Close on Escape ────────────────────────────
        QShortcut(QKeySequence("Escape"), dlg).activated.connect(dlg.accept)

        # ── Close on click outside — app-level event filter ──
        # Any mouse press that lands outside dlg's geometry closes it.
        class _OutsideClickFilter(QObject):
            def eventFilter(self_, obj, event):
                if event.type() == event.Type.MouseButtonPress:
                    # globalPosition() returns QPointF in PyQt6
                    gpos = event.globalPosition().toPoint()
                    if not dlg.geometry().contains(gpos):
                        dlg.accept()
                        QApplication.instance().removeEventFilter(self_)
                        return False
                return False

        click_filter = _OutsideClickFilter(dlg)
        QApplication.instance().installEventFilter(click_filter)
        # Clean up filter when dialog closes normally too
        dlg.finished.connect(
            lambda: QApplication.instance().removeEventFilter(click_filter)
        )

        dlg.show()

    def _export(self):
        if not self._results:
            self.statusBar().showMessage("Nothing to export — run a scan first")
            return
        fname = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        clean = [{k: v for k, v in r.items() if k not in ("sig_clr","candles")}
                 for r in self._results]
        with open(fname, "w") as f:
            json.dump(clean, f, indent=2)
        self.statusBar().showMessage(f"Exported → {fname}")
        if hasattr(self, 'cfg_export_lbl'):
            self.cfg_export_lbl.setText(f"Saved: {fname}")
            self.cfg_export_lbl.setStyleSheet(f"color:{GREEN}; font-size:11px;")

# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
def main():
    # Suppress Qt stderr noise about missing dbus portal and unknown CSS properties.
    # These are harmless Qt/desktop-environment warnings, not Python errors.
    os.environ.setdefault("QT_LOGGING_RULES",
        "qt.qpa.theme=false;qt.qpa.theme.gnome=false")

    app = QApplication(sys.argv)
    app.setApplicationName("Crypto Scalper Scanner")
    app.setStyleSheet(make_stylesheet(FONT_SIZE))

    # Set dark palette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(DARK))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(WHITE))
    palette.setColor(QPalette.ColorRole.Base,            QColor(DARK2))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(PANEL))
    palette.setColor(QPalette.ColorRole.Text,            QColor(WHITE))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(WHITE))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(DARK))
    app.setPalette(palette)

    win = CryptoScannerWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
