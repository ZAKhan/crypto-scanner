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
try:
    import websocket as _websocket
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QLabel, QPushButton,
    QFrame, QHeaderView, QAbstractItemView, QProgressBar, QTabWidget,
    QScrollArea, QGridLayout, QSizePolicy, QSpacerItem, QGroupBox,
    QLineEdit, QTextEdit, QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox,
    QStatusBar, QToolBar, QMessageBox, QDialog, QMenu
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize, QPropertyAnimation,
    QEasingCurve, pyqtProperty, QObject, QSettings, QByteArray
)
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QBrush, QLinearGradient,
    QPainter, QPen, QIcon, QAction, QFontDatabase,
    QShortcut, QKeySequence
)
try:
    from PyQt6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
    HAS_CHARTS = True
except ImportError:
    HAS_CHARTS = False

APP_VERSION = "v2.4.2"

# ─────────────────────────────────────────────────────────────
#  CROSS-PLATFORM DATA DIRECTORY
#  Linux:   ~/.config/CryptoScalper/
#  Windows: %APPDATA%/CryptoScalper/
#  macOS:   ~/Library/Application Support/CryptoScalper/
# ─────────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────
#  CONFIG  (edit these to change scan behaviour)
# ─────────────────────────────────────────────────────────
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
    "min_expected_move":  2.0,   # Filter: only show coins expected to move >2% (lower = more results)
}

# ─────────────────────────────────────────────────────────
#  COLOUR PAIRS
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
#  URL OPENER  — works in source, PyInstaller binary, Wayland, X11
# ─────────────────────────────────────────────────────────
def open_url(url: str) -> None:
    """
    Open a URL in the system browser. If BROWSER_PATH is set (via Config tab)
    that binary is used directly. Otherwise tries multiple fallbacks so it works
    correctly when running as a PyInstaller binary on any desktop (Wayland, X11).
    """
    import shutil, os
    env = os.environ.copy()

    # 0. User-specified browser from Config tab
    if BROWSER_PATH and BROWSER_PATH.strip():
        try:
            subprocess.Popen(
                [BROWSER_PATH.strip(), url], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass  # fall through to defaults

    # 1. xdg-open with explicit env so DISPLAY/WAYLAND_DISPLAY are passed through
    xdg = shutil.which("xdg-open")
    if xdg:
        try:
            proc = subprocess.Popen(
                [xdg, url], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            proc.wait(timeout=3)
            if proc.returncode == 0:
                return
        except Exception:
            pass

    # 2. Qt native handler
    try:
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        if QDesktopServices.openUrl(QUrl(url)):
            return
    except Exception:
        pass

    # 3. Common browser binaries directly
    for browser in ("firefox", "chromium", "chromium-browser",
                    "google-chrome", "brave-browser"):
        b = shutil.which(browser)
        if b:
            try:
                subprocess.Popen(
                    [b, url], env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                continue

    # 4. Python webbrowser as last resort
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


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
    return round(100 - 100 / (1 + ag / al), 2) if al > 0 else (100.0 if ag > 0 else 50.0)

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
    return round((rsi_vals[-1] - lo) / (hi - lo) * 100, 2) if (hi - lo) > 0 else 50.0

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

    # ADR — Average Daily Range over last 10 candles (as % of price)
    # Measures how much the coin typically moves per candle
    # Low ADR = flat/choppy coin not worth trading
    adr_pct = 0.0
    if len(candles) >= 5 and closes[-1] > 0:
        recent = candles[-10:]
        ranges = [(c["high"] - c["low"]) / c["close"] * 100
                  for c in recent if c["close"] > 0]
        adr_pct = round(statistics.mean(ranges), 2) if ranges else 0.0

    signal, sig_clr, long_sc, short_sc = score_signal(
        rsi, mh, closes[-1], bbu, bbl, bbm, pattern, change_24h, stoch_rsi,
        vol_ratio=vol_ratio, macd_rising=macd_rising, bb_width_pct=bb_width_pct)

    # ── PRE-BREAKOUT detection ──────────────────────────
    # Fires when: BB squeeze + volume building + RSI recovering + price at support
    pre_breakout = False
    if signal in ("NEUTRAL", "BUY") and bbu and bbl and closes[-1] > 0:
        bb_pct_pos = (closes[-1] - bbl) / (bbu - bbl) * 100 if (bbu - bbl) > 0 else 50
        pre_breakout = (
            bb_width_pct < 5.0 and          # BB squeeze — bands very tight
            vol_ratio >= 1.5 and             # volume building (1.5x average)
            35 <= rsi <= 55 and              # RSI recovering, not overbought
            bb_pct_pos < 25                  # price near lower band
        )
        if pre_breakout:
            signal  = "PRE-BREAKOUT"
            sig_clr = "orange"

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
        "vol_spike":    vol_ratio >= 2.0,   # True if volume > 2x average
        "adr_pct":      adr_pct,             # avg candle range % — low = flat coin
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
#  TRADING CONFIG
# ─────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────
#  BINANCE TRADER  — signed REST calls, order placement
# ─────────────────────────────────────────────────────────
class BinanceTrader:
    """
    Handles all authenticated Binance API calls.
    Uses HMAC-SHA256 signing (standard key type).
    Every public method returns (success: bool, data: dict | str).
    Never raises — all exceptions are caught and returned as errors.
    """

    MAX_RETRIES = 3
    TIMEOUT     = 10  # seconds per request

    # ── Signing ──────────────────────────────────────────
    @staticmethod
    def _sign(params: dict, secret: str) -> str:
        import hmac, hashlib, urllib.parse
        query = urllib.parse.urlencode(params)
        return hmac.new(
            secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _signed_request(self, method: str, path: str,
                        params: dict | None = None) -> tuple[bool, dict]:
        """
        Send a signed request with retry + exponential backoff.
        Returns (True, response_dict) or (False, {"error": "..."}).
        """
        import time, urllib.parse

        key    = TRADING_CFG["api_key"].strip()
        secret = TRADING_CFG["api_secret"].strip()
        if not key or not secret:
            return False, {"error": "API key / secret not configured"}

        p = dict(params or {})

        last_err = ""
        for attempt in range(self.MAX_RETRIES):
            try:
                p["timestamp"] = int(time.time() * 1000)
                p["recvWindow"] = 5000
                p["signature"] = self._sign(p, secret)

                url     = trading_base() + path
                headers = {"X-MBX-APIKEY": key}

                if method == "GET":
                    resp = requests.get(url, params=p,
                                        headers=headers, timeout=self.TIMEOUT)
                elif method == "POST":
                    resp = requests.post(url, data=p,
                                         headers=headers, timeout=self.TIMEOUT)
                elif method == "DELETE":
                    resp = requests.delete(url, params=p,
                                           headers=headers, timeout=self.TIMEOUT)
                else:
                    return False, {"error": f"Unknown method {method}"}

                data = resp.json()

                if resp.status_code == 200:
                    return True, data

                # Binance error — no point retrying 400 errors
                code = data.get("code", 0)
                msg  = data.get("msg", str(data))
                if resp.status_code == 400 or resp.status_code == 401:
                    return False, {"error": f"[{code}] {msg}"}

                last_err = f"HTTP {resp.status_code}: {msg}"

            except requests.exceptions.Timeout:
                last_err = f"Timeout (attempt {attempt + 1})"
            except requests.exceptions.ConnectionError:
                last_err = f"Connection error (attempt {attempt + 1})"
            except Exception as e:
                last_err = str(e)

            if attempt < self.MAX_RETRIES - 1:
                import time as _t
                _t.sleep(0.5 * (attempt + 1))  # 0.5s, 1.0s backoff

        return False, {"error": last_err}

    # ── Public methods ───────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        """
        Ping Binance and fetch account info to verify keys work.
        Returns (True, "Connected — X USDT available") or (False, error).
        """
        ok, data = self._signed_request("GET", "/api/v3/account")
        if not ok:
            return False, data.get("error", "Unknown error")
        balances = {b["asset"]: float(b["free"])
                    for b in data.get("balances", [])
                    if float(b["free"]) > 0}
        usdt = balances.get("USDT", 0)
        env  = "TESTNET" if TRADING_CFG["testnet"] else "LIVE"
        return True, f"✓ Connected ({env}) — {usdt:,.2f} USDT available"

    def get_usdt_balance(self) -> tuple[bool, float]:
        """Returns (True, usdt_balance) or (False, 0.0)."""
        ok, data = self._signed_request("GET", "/api/v3/account")
        if not ok:
            return False, 0.0
        for b in data.get("balances", []):
            if b["asset"] == "USDT":
                return True, float(b["free"])
        return True, 0.0

    def get_asset_balance(self, asset: str) -> tuple[bool, float]:
        """Returns (True, free_balance) for any asset, or (False, 0.0)."""
        ok, data = self._signed_request("GET", "/api/v3/account")
        if not ok:
            return False, 0.0
        for b in data.get("balances", []):
            if b["asset"] == asset:
                return True, float(b["free"])
        return True, 0.0

    def get_symbol_info(self, symbol: str) -> tuple[bool, dict]:
        """
        Fetch LOT_SIZE and PRICE_FILTER for a symbol.
        Returns (True, {stepSize, minQty, tickSize, minNotional})
        """
        try:
            resp = requests.get(
                trading_base() + "/api/v3/exchangeInfo",
                params={"symbol": symbol},
                timeout=self.TIMEOUT
            )
            data = resp.json()
            filters = {}
            for sym in data.get("symbols", []):
                if sym["symbol"] == symbol:
                    for f in sym.get("filters", []):
                        if f["filterType"] == "LOT_SIZE":
                            filters["stepSize"] = float(f["stepSize"])
                            filters["minQty"]   = float(f["minQty"])
                        elif f["filterType"] == "PRICE_FILTER":
                            filters["tickSize"] = float(f["tickSize"])
                        elif f["filterType"] == "MIN_NOTIONAL":
                            filters["minNotional"] = float(f.get("minNotional", 10))
                        elif f["filterType"] == "NOTIONAL":
                            filters["minNotional"] = float(f.get("minNotional", 10))
                    return True, filters
            return False, {"error": f"Symbol {symbol} not found"}
        except Exception as e:
            return False, {"error": str(e)}

    def round_step(self, qty: float, step: float) -> float:
        """Round quantity down to nearest step size."""
        import math
        if step <= 0:
            return qty
        precision = max(0, -int(math.floor(math.log10(step))))
        return round(math.floor(qty / step) * step, precision)

    def round_tick(self, price: float, tick: float) -> float:
        """Round price to nearest tick size."""
        import math
        if tick <= 0:
            return price
        precision = max(0, -int(math.floor(math.log10(tick))))
        return round(round(price / tick) * tick, precision)

    def place_market_buy(self, symbol: str,
                         usdt_amount: float) -> tuple[bool, dict]:
        """
        Place a MARKET BUY order for `usdt_amount` USDT worth of `symbol`.
        Returns (True, order_dict) or (False, {"error": ...}).
        """
        # Get symbol filters
        ok, info = self.get_symbol_info(symbol)
        if not ok:
            return False, info

        step = info.get("stepSize", 0.00001)
        min_notional = info.get("minNotional", 10.0)

        # Get current price to calculate quantity
        try:
            pr = requests.get(
                trading_base() + "/api/v3/ticker/price",
                params={"symbol": symbol}, timeout=self.TIMEOUT
            ).json()
            price = float(pr["price"])
        except Exception as e:
            return False, {"error": f"Price fetch failed: {e}"}

        raw_qty = usdt_amount / price
        qty     = self.round_step(raw_qty, step)

        if qty * price < min_notional:
            return False, {"error": f"Order too small — minimum {min_notional} USDT notional"}

        return self._signed_request("POST", "/api/v3/order", {
            "symbol":   symbol,
            "side":     "BUY",
            "type":     "MARKET",
            "quantity": f"{qty:.8f}",
        })

    def place_oco_sell(self, symbol: str, quantity: float,
                       tp_price: float, sl_price: float,
                       sl_limit_price: float) -> tuple[bool, dict]:
        """
        Place an OCO sell order (TP limit + SL stop-limit).
        sl_limit_price should be slightly below sl_price (e.g. 0.1% lower).
        Returns (True, order_dict) or (False, {"error": ...}).
        """
        ok, info = self.get_symbol_info(symbol)
        if not ok:
            return False, info

        tick = info.get("tickSize", 0.00001)
        step = info.get("stepSize", 0.00001)

        qty      = self.round_step(quantity, step)
        tp_p     = self.round_tick(tp_price, tick)
        sl_p     = self.round_tick(sl_price, tick)
        sl_lim_p = self.round_tick(sl_limit_price, tick)

        return self._signed_request("POST", "/api/v3/order/oco", {
            "symbol":             symbol,
            "side":               "SELL",
            "quantity":           f"{qty:.8f}",
            "price":              f"{tp_p:.8f}",       # TP limit price
            "stopPrice":          f"{sl_p:.8f}",       # SL trigger
            "stopLimitPrice":     f"{sl_lim_p:.8f}",   # SL limit
            "stopLimitTimeInForce": "GTC",
        })

    def place_market_sell(self, symbol: str,
                          quantity: float) -> tuple[bool, dict]:
        """
        Place a MARKET SELL for exact quantity.
        Returns (True, order_dict) or (False, {"error": ...}).
        """
        ok, info = self.get_symbol_info(symbol)
        if not ok:
            return False, info

        step = info.get("stepSize", 0.00001)
        qty  = self.round_step(quantity, step)

        return self._signed_request("POST", "/api/v3/order", {
            "symbol":   symbol,
            "side":     "SELL",
            "type":     "MARKET",
            "quantity": f"{qty:.8f}",
        })

    def cancel_order(self, symbol: str,
                     order_id: int) -> tuple[bool, dict]:
        """Cancel a single order by ID."""
        return self._signed_request("DELETE", "/api/v3/order", {
            "symbol":  symbol,
            "orderId": order_id,
        })

    def cancel_oco(self, symbol: str,
                   order_list_id: int) -> tuple[bool, dict]:
        """Cancel an OCO order list by orderListId."""
        return self._signed_request("DELETE", "/api/v3/orderList", {
            "symbol":      symbol,
            "orderListId": order_list_id,
        })

    def get_open_orders(self, symbol: str) -> tuple[bool, list]:
        """Get all open orders for a symbol."""
        ok, data = self._signed_request("GET", "/api/v3/openOrders",
                                        {"symbol": symbol})
        if not ok:
            return False, []
        return True, data


# single shared instance
_trader = BinanceTrader()


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
                if   sig == "PRE-BREAKOUT": tier = 0
                elif sig == "STRONG BUY":  tier = 1
                elif sig == "STRONG SELL": tier = 2
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
    "max_rsi":          70,         # only alert if RSI <= this
    "max_bb_pct":       80,         # only alert if BB% <= this (0=oversold, 100=overbought)
    "require_vol_spike": False,     # only alert if volume spike detected
    "min_adr_pct":      0.5,        # minimum avg candle range % — skip flat coins
    "block_downtrend":  True,       # Fix 1 — block alerts when pattern shows Downtrend
    "min_vol_ratio":    0.8,        # Fix 2 — minimum volume ratio vs average
    "spike_cooldown":   True,       # Fix 3 — skip coin if spiked >15% in last 3 hours
    "spike_pct":        15.0,       # Fix 3 — spike threshold %
    "require_macd_rising": False,   # Fix 4 — only alert if MACD is rising
    "coin_cooldown":       True,    # Fix 5 — per-coin alert cooldown
    "coin_cooldown_mins":  30,      # minutes before same coin can alert again
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
        "PRE-BREAKOUT": [
            (660, 0.12), (0, 0.04), (880, 0.12), (0, 0.04), (1100, 0.20),
        ],
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



SAFETY_CFG = {
    "signal_persistence":       True,
    "btc_trend_check":          True,
    "btc_drop_pct":             2.0,
    # Fix 1 — BTC drop cooldown
    "btc_drop_cooldown_mins":   60,
    "btc_recovery_pct":         1.5,
    # Fix 2 — 1h trend freshness
    "trend_1h_freshness":       True,
    "trend_1h_stale_pct":       1.5,
    # Fix 3 — per-symbol recovery gate
    "symbol_recovery_gate":     True,
    "symbol_recovery_pct":      1.0,
    "symbol_recovery_expiry_mins": 30,
    "max_open_trades":          True,
    "max_open_trades_count":    3,
    "daily_loss_limit":         True,
    "daily_loss_amount":        100.0,
    "coin_trend_check":         True,
    "coin_drop_pct":            5.0,
}
_daily_loss_tracker      = {"date": "", "loss": 0.0}
_spike_cooldown_tracker  = {}  # symbol -> timestamp of spike detection

import time as _time
_btc_drop_state = {
    "active":       False,
    "trigger_time": 0.0,
    "drop_low":     float("inf"),
}
_symbol_block_state = {}

def _get_symbol_block(symbol: str) -> dict:
    if symbol not in _symbol_block_state:
        _symbol_block_state[symbol] = {"blocked": False, "block_time": 0.0, "block_price": 0.0}
    return _symbol_block_state[symbol]
_coin_alert_tracker      = {}  # symbol -> timestamp of last alert fired


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
            import requests as _req
            r2 = _req.get(CFG["base_url"] + "/api/v3/ticker/24hr",
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
                recovered = ((btc_price - low) / low * 100) if low > 0 else 0
                if elapsed >= cooldown_secs or recovered >= recovery_pct:
                    _btc_drop_state["active"] = False
            if not _btc_drop_state["active"] and btc_chg < -SAFETY_CFG["btc_drop_pct"]:
                _btc_drop_state["active"]       = True
                _btc_drop_state["trigger_time"] = now_ts
                _btc_drop_state["drop_low"]     = btc_price
            if _btc_drop_state["active"] and is_long:
                elapsed_min = (now_ts - _btc_drop_state["trigger_time"]) / 60
                low         = _btc_drop_state["drop_low"]
                recovered   = ((btc_price - low) / low * 100) if low > 0 else 0
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
        pct_from_1h = None
        try:
            import requests as _req
            kl = _req.get(CFG["base_url"] + "/api/v3/klines",
                          params={"symbol": sym, "interval": "1h", "limit": 2},
                          timeout=5).json()
            if kl and len(kl) >= 1:
                open_1h = float(kl[-1][1])
                if open_1h > 0:
                    pct_from_1h = (price - open_1h) / open_1h * 100
        except Exception:
            pass
        if pct_from_1h is not None and pct_from_1h <= -stale_threshold:
            return False, (
                f"trend_1h='up' is stale — {sym} is {pct_from_1h:.1f}% below its 1h open "
                f"(threshold: -{stale_threshold}%)"
            )

    # Fix 3 — Per-symbol recovery gate
    if is_long and SAFETY_CFG.get("symbol_recovery_gate", True):
        sb = _get_symbol_block(sym)
        if sb["blocked"]:
            expiry_secs   = SAFETY_CFG.get("symbol_recovery_expiry_mins", 30) * 60
            recovery_need = SAFETY_CFG.get("symbol_recovery_pct", 1.0)
            elapsed       = now_ts - sb["block_time"]
            cur_price     = r.get("price", 0)
            block_price   = sb["block_price"]
            recovered_pct = ((cur_price - block_price) / block_price * 100) if block_price > 0 else 0
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
        if chg_24h < -SAFETY_CFG["coin_drop_pct"]:
            return False, f"{sym} down {chg_24h:.1f}% in 24h — downtrend"

    # Layer 3 — Max open trades
    if SAFETY_CFG["max_open_trades"]:
        open_count = sum(1 for t in trades if t.get("status") == "OPEN")
        if open_count >= SAFETY_CFG["max_open_trades_count"]:
            return False, f"Max open trades reached ({open_count}/{SAFETY_CFG['max_open_trades_count']})"

    # Layer 3 — Daily loss limit
    if SAFETY_CFG["daily_loss_limit"]:
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y-%m-%d")
        if _daily_loss_tracker["date"] != today:
            _daily_loss_tracker["date"] = today
            _daily_loss_tracker["loss"] = 0.0
        if _daily_loss_tracker["loss"] >= SAFETY_CFG["daily_loss_amount"]:
            return False, f"Daily loss limit reached (${_daily_loss_tracker['loss']:.2f})"

    return True, ""


def safety_mark_symbol_blocked(symbol: str, price: float):
    """Call when safety_blocked=True fires — starts the per-symbol recovery gate."""
    sb = _get_symbol_block(symbol)
    sb["blocked"]     = True
    sb["block_time"]  = _time.time()
    sb["block_price"] = price

def record_trade_loss(pnl: float):
    """Call after a trade closes at a loss to update daily tracker."""
    if pnl < 0:
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y-%m-%d")
        if _daily_loss_tracker["date"] != today:
            _daily_loss_tracker["date"] = today
            _daily_loss_tracker["loss"] = 0.0
        _daily_loss_tracker["loss"] += abs(pnl)


# ─────────────────────────────────────────────────────────────
#  SIGNAL AUDIT LOGGER
#  Logs every scan result to CSV for post-analysis
# ─────────────────────────────────────────────────────────────
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
                    (not ALERT_CFG.get("block_downtrend") or "Downtrend" not in r.get("pattern", "")) and
                    (not ALERT_CFG.get("require_macd_rising") or r.get("macd_rising", False)) and
                    (not ALERT_CFG.get("require_vol_spike") or r.get("vol_spike", False))
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


# ─────────────────────────────────────────────────────────────
#  OUTCOME TRACKER
#  After an alert fires, schedule price checks at 30m, 1h, 4h.
#  Goes back to the CSV and fills in actual price outcomes.
# ─────────────────────────────────────────────────────────────

class OutcomeTracker:
    """
    Tracks price outcomes for alerted signals.
    Queues price checks at 30min, 1h, 4h after each alert.
    Updates the signal log CSV in-place with results.
    """
    def __init__(self):
        self._queue = []   # list of (check_time, symbol, entry_price, log_path, timestamp, col)
        self._lock  = threading.Lock()
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def schedule(self, symbol: str, entry_price: float, alert_timestamp: str, log_path: str):
        """Schedule outcome checks for a fired alert."""
        from datetime import datetime as _dt, timedelta as _td
        now = _dt.now()
        with self._lock:
            for minutes, col in [(30, "price_30m"), (60, "price_1h"), (240, "price_4h")]:
                check_at = now + _td(minutes=minutes)
                self._queue.append({
                    "check_at":        check_at,
                    "symbol":          symbol,
                    "entry_price":     entry_price,
                    "log_path":        log_path,
                    "alert_timestamp": alert_timestamp,
                    "price_col":       col,
                    "pct_col":         col.replace("price_", "pct_"),
                })

    def _fetch_price(self, symbol: str) -> float:
        """Fetch current price from Binance REST."""
        try:
            import requests as _req
            r = _req.get(
                CFG["base_url"] + "/api/v3/ticker/price",
                params={"symbol": symbol}, timeout=5
            ).json()
            return float(r.get("price", 0))
        except Exception:
            return 0.0

    def _update_csv(self, log_path: str, alert_timestamp: str, symbol: str,
                    price_col: str, pct_col: str, price: float, pct: float):
        """Find the matching row in CSV and update outcome columns."""
        import csv, tempfile, os
        if not os.path.exists(log_path):
            return
        try:
            rows = []
            with open(log_path, newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                for row in reader:
                    if (row["timestamp"] == alert_timestamp and
                            row["symbol"] == symbol and
                            row["alert_fired"] == "True"):
                        row[price_col] = round(price, 8)
                        row[pct_col]   = round(pct, 2)
                        # Set outcome once all 3 are filled
                        if row.get("pct_1h") not in ("", None):
                            p = float(row["pct_1h"])
                            if   p >= 3.0:  row["outcome"] = "WIN"
                            elif p <= -2.0: row["outcome"] = "LOSS"
                            else:           row["outcome"] = "FLAT"
                    rows.append(row)
            # Write back atomically
            tmp = log_path + ".tmp"
            with open(tmp, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(tmp, log_path)
        except Exception:
            pass

    def _run(self):
        from datetime import datetime as _dt
        while self._running:
            now = _dt.now()
            due = []
            with self._lock:
                remaining = []
                for item in self._queue:
                    if now >= item["check_at"]:
                        due.append(item)
                    else:
                        remaining.append(item)
                self._queue = remaining

            for item in due:
                price = self._fetch_price(item["symbol"])
                if price > 0 and item["entry_price"] > 0:
                    pct = (price - item["entry_price"]) / item["entry_price"] * 100
                    self._update_csv(
                        item["log_path"],
                        item["alert_timestamp"],
                        item["symbol"],
                        item["price_col"],
                        item["pct_col"],
                        price, pct
                    )

            time.sleep(30)  # check queue every 30 seconds

_outcome_tracker = OutcomeTracker()

class AlertEngine(QObject):
    """
    Background alert engine.
    - Auto-scans on a timer
    - Compares new results against last scan
    - Fires desktop / sound / telegram for NEW signals only
    """
    new_alert   = pyqtSignal(dict)
    scan_done   = pyqtSignal(list)
    scan_started = pyqtSignal()      # emitted when background scan begins

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
        self.scan_started.emit()     # notify UI scan is beginning
        self._scanner.start_scan()
        # Wait for scan to finish
        while self._scanner.scanning:
            time.sleep(0.2)
        results = self._scanner.get_results()
        if results:
            self._check_alerts(results)
            self.scan_done.emit(results)

    def _check_alerts(self, results):
        now = datetime.now()

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

        sig_order = {"PRE-BREAKOUT": 0, "STRONG BUY": 1, "STRONG SELL": 2, "BUY": 3, "SELL": 4, "NEUTRAL": 5}
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
            # Fix 3 — spike cooldown check
            _now_ts = datetime.now()
            _spike_ok = True
            if ALERT_CFG.get("spike_cooldown") and "BUY" in sig:
                _spike_threshold = ALERT_CFG.get("spike_pct", 15.0)
                _last_spike = _spike_cooldown_tracker.get(sym)
                if _last_spike and (_now_ts - _last_spike).seconds < 7200:
                    _spike_ok = False  # still in 2hr cooldown
                # Detect new spike — if coin up >threshold% track it
                if r.get("change", 0) >= _spike_threshold:
                    _spike_cooldown_tracker[sym] = _now_ts

            # Fix 5 — per-coin cooldown check
            _cooldown_ok = True
            if ALERT_CFG.get("coin_cooldown"):
                _cooldown_mins = ALERT_CFG.get("coin_cooldown_mins", 30)
                _last_alert = _coin_alert_tracker.get(sym)
                if _last_alert and (_now_ts - _last_alert).seconds < _cooldown_mins * 60:
                    _cooldown_ok = False

            passes = (level <= min_level and
                      pot >= ALERT_CFG["min_potential"] and
                      exp >= ALERT_CFG["min_exp_move"] and
                      r.get("rsi", 50) <= ALERT_CFG["max_rsi"] and
                      r.get("bb_pct", 50) <= ALERT_CFG["max_bb_pct"] and
                      r.get("adr_pct", 0) >= ALERT_CFG["min_adr_pct"] and
                      r.get("vol_ratio", 0) >= ALERT_CFG.get("min_vol_ratio", 0) and
                      (not ALERT_CFG.get("block_downtrend") or "Downtrend" not in r.get("pattern", "")) and
                      (not ALERT_CFG.get("require_macd_rising") or r.get("macd_rising", False)) and
                      (not ALERT_CFG["require_vol_spike"] or r.get("vol_spike", False)) and
                      _spike_ok and _cooldown_ok)

            if is_new and passes:
                _coin_alert_tracker[sym] = _now_ts  # record last alert time
                # Schedule outcome tracking — check price at 30m, 1h, 4h
                _outcome_tracker.schedule(
                    symbol=sym,
                    entry_price=r.get("price", 0),
                    alert_timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
                    log_path=_get_signal_log_path()
                )
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

FONT_SIZE    = 13   # default — user can change in Config tab
BROWSER_PATH = ""   # empty = use system default; set via Config tab

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
    padding: 4px 8px;
    font-size: {fs_l}px;
    font-weight: 700;
    border-radius: 6px;
    min-width: 0px;
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
QLabel#versionLabel {{
    color: {DARK};
    background: {ACCENT};
    font-size: {fs_x}px;
    font-weight: 800;
    font-family: {MONO_CSS};
    border-radius: 4px;
    padding: 1px 7px;
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
class TooltipHeaderView(QHeaderView):
    """
    QHeaderView subclass that shows per-column tooltips on hover.
    Tooltip appears after the standard Qt delay (~700ms) and disappears on mouse move.
    """
    def __init__(self, orientation, tooltips: dict, parent=None):
        """
        tooltips: dict mapping column index -> tooltip string
        """
        super().__init__(orientation, parent)
        self._tooltips = tooltips

    def event(self, e):
        from PyQt6.QtWidgets import QToolTip
        if e.type() == e.Type.ToolTip:
            pos   = e.pos()
            index = self.logicalIndexAt(pos)
            tip   = self._tooltips.get(index, "")
            if tip: QToolTip.showText(e.globalPos(), tip, self)
            else:   QToolTip.hideText()
            return True
        return super().event(e)


class SignalBadge(QLabel):
    COLORS = {
        "PRE-BREAKOUT":("#ff9900", "#2a1800",    "#ffaa33"),
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

        sr_grp = QGroupBox("SUPPORT / RESISTANCE")
        sr_lay = QHBoxLayout(sr_grp)
        sr_lay.addWidget(StatCard("Support",    f"${r['support']:.6f}", GREEN))
        sr_lay.addWidget(StatCard("Resistance", f"${r['resist']:.6f}",  RED))
        sr_lay.addWidget(StatCard("Vol 24h",    f"${r['volume_24h']/1e6:.1f}M", ACCENT))
        self.lay.addWidget(sr_grp)

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

        candles = r.get("candles", [])
        if candles:
            chart_grp = QGroupBox("PRICE  (last 50 candles)")
            chart_lay = QVBoxLayout(chart_grp)
            chart_lay.addWidget(PriceChart(candles))
            self.lay.addWidget(chart_grp)

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



# ─────────────────────────────────────────────────────────────
#  WEBSOCKET PRICE FEED
#  Subscribes to Binance miniTicker stream for real-time prices.
#  Falls back to REST polling if websocket-client not available.
# ─────────────────────────────────────────────────────────────

class BinanceWebSocketPrices(QObject):
    """
    Maintains a persistent WebSocket connection to Binance.
    Emits price_update(symbol, price) signal on every tick.
    Auto-reconnects on disconnect.
    """
    price_update = pyqtSignal(str, float)   # symbol, price
    connected    = pyqtSignal()
    disconnected = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._subscribed  = set()   # symbols currently subscribed
        self._ws          = None
        self._thread      = None
        self._running     = False
        self._reconnect_delay = 3   # seconds before reconnect

    def _ws_url(self, trade_syms=None):
        """
        Build WebSocket URL:
        - Open trade symbols: individual ticker streams (updates on every tick)
        - Scanner symbols: all-market miniTicker (every 3s, sufficient for scanning)
        """
        trade_syms = trade_syms or set()
        base_testnet = "wss://stream.testnet.binance.vision/stream?streams="
        base_live    = "wss://stream.binance.com:9443/stream?streams="
        base = base_testnet if TRADING_CFG["testnet"] else base_live

        streams = []
        # Per-symbol miniTicker for open trades — fires on every trade
        for sym in trade_syms:
            streams.append(f"{sym.lower()}@miniTicker")
        # All-market snapshot for scanner — covers everything else
        streams.append("!miniTicker@arr@3000ms")

        return base + "/".join(streams)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def subscribe(self, symbols: set):
        """Update subscriptions. Reconnects if trade symbols changed (need per-symbol streams)."""
        new_syms = {s.upper() for s in symbols}
        if new_syms == self._subscribed:
            return
        old_trade = {s for s in self._subscribed if not s.startswith("!")}
        new_trade = {s for s in new_syms if not s.startswith("!")}
        self._subscribed = new_syms
        # Reconnect only if trade symbols changed — need new per-symbol streams
        if old_trade != new_trade and self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _run(self):
        while self._running:
            try:
                # Build trade symbol list (only open trade symbols, not scanner symbols)
                trade_syms = {s for s in self._subscribed}
                url = self._ws_url(trade_syms)

                # Use a local reference to self for closure
                _self = self

                def on_message(ws, msg):
                    try:
                        data = json.loads(msg)
                        tickers = data.get("data", data)
                        if isinstance(tickers, list):
                            # All-market miniTicker array
                            for d in tickers:
                                sym   = d.get("s", "")
                                price = float(d.get("c", 0) or 0)
                                if price > 0 and (_self._subscribed and sym in _self._subscribed):
                                    _self.price_update.emit(sym, price)
                        elif isinstance(tickers, dict):
                            # Individual symbol stream — emit regardless of filter
                            sym   = tickers.get("s", "")
                            price = float(tickers.get("c", 0) or 0)
                            if sym and price > 0:
                                _self.price_update.emit(sym, price)
                    except Exception:
                        pass

                def on_open(ws):
                    _self.connected.emit()

                def on_close(ws, code, msg):
                    _self.disconnected.emit()

                def on_error(ws, err):
                    pass

                self._ws = _websocket.WebSocketApp(
                    url,
                    on_message=on_message,
                    on_open=on_open,
                    on_close=on_close,
                    on_error=on_error,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=8)
            except Exception:
                pass

            if self._running:
                time.sleep(self._reconnect_delay)

class CryptoScannerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Crypto Scalper Scanner {APP_VERSION} — Binance")

        # App icon — look next to the script file
        # App icon — search in several locations
        import os as _os, sys as _sys
        _icon_candidates = [
            _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "app_icon.png"),
            _os.path.join(_os.getcwd(), "app_icon.png"),
            _os.path.join(_os.path.dirname(_os.path.abspath(_sys.argv[0])), "app_icon.png"),
        ]
        for _icon_path in _icon_candidates:
            if _os.path.exists(_icon_path):
                _icon = QIcon(_icon_path)
                QApplication.instance().setWindowIcon(_icon)
                self.setWindowIcon(_icon)
                break
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
        self._alert_engine.scan_started.connect(self._on_alert_scan_started)

        # WebSocket price feed
        if _WS_AVAILABLE:
            self._ws_feed = BinanceWebSocketPrices()
            self._ws_feed.price_update.connect(self._on_ws_price, Qt.ConnectionType.QueuedConnection)
            self._ws_feed.connected.connect(self._on_ws_connected, Qt.ConnectionType.QueuedConnection)
            self._ws_feed.disconnected.connect(self._on_ws_disconnected, Qt.ConnectionType.QueuedConnection)
        else:
            self._ws_feed = None
        self._alert_engine.scan_started.connect(self._on_alert_scan_started)
        self._build_ui()
        self._setup_timer()
        self._restore_settings()  # after UI is built
        # Start trade price monitor immediately — runs always, not just on Trades tab
        self._trades_refresh_timer.start()
        self._alert_engine.start()
        _outcome_tracker.start()
        # Subscribe open trade symbols immediately so prices start flowing
        if self._ws_feed:
            open_syms = {t["symbol"] for t in self._trades if t["status"] == "OPEN"}
            if open_syms:
                self._ws_feed.subscribe(open_syms)
        # WebSocket starts after first scan completes (needs symbol list first)
        # _on_alert_scan_done will start it
        # Refresh balance display on startup
        QTimer.singleShot(1000, self._refresh_balance_display)
        # Show scanning status immediately so user knows it's working
        self.statusBar().showMessage("Starting scan…")
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("⏳")

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        topbar = QFrame()
        topbar.setStyleSheet(f"background:{PANEL}; border-bottom:1px solid {BORDER};")
        topbar.setFixedHeight(64)
        tlay = QHBoxLayout(topbar)
        tlay.setContentsMargins(20, 0, 20, 0)
        tlay.setSpacing(12)
        tlay.setSizeConstraint(QHBoxLayout.SizeConstraint.SetNoConstraint)

        # LEFT — title, version, subtitle with stretch=0 (never grow/shrink)
        title = QLabel("◈ CRYPTO SCALPER")
        title.setObjectName("titleLabel")

        ver = QLabel(APP_VERSION)
        ver.setObjectName("versionLabel")
        ver.setToolTip("Application version")

        sub = QLabel("Binance Spot  ·  Price < $1  ·  Vol > $1M  ·  5m")
        sub.setObjectName("subtitleLabel")
        sub.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        tlay.addWidget(title, 0)
        tlay.addWidget(ver, 0)
        tlay.addSpacing(14)
        tlay.addWidget(sub, 0)

        # Stretch pushes everything right
        tlay.addStretch(1)

        # Balance — fixed width, centred text, never resizes
        self._balance_lbl = QLabel("💰 —")
        self._balance_lbl.setFixedWidth(185)
        self._balance_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self._balance_lbl.setStyleSheet(
            f"color:{ACCENT}; font-family:{MONO_CSS}; font-size:11px; font-weight:700;")
        self._balance_lbl.setToolTip("USDT balance — click to refresh")
        self._balance_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._balance_lbl.mousePressEvent = lambda e: self._refresh_balance_display()
        tlay.addWidget(self._balance_lbl, 0)

        # Progress bar — only visible during scan
        # Keep progress as hidden (used internally, never shown)
        self.progress = QProgressBar()
        self.progress.setVisible(False)

        # Cols reset
        reset_col_btn = QPushButton("⇔ Cols")
        reset_col_btn.setFixedHeight(30)
        reset_col_btn.setMinimumWidth(75)
        reset_col_btn.setToolTip("Reset column widths to auto-proportional")
        reset_col_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; font-size:11px; padding:0 8px;")
        reset_col_btn.clicked.connect(self._reset_column_widths)
        tlay.addWidget(reset_col_btn, 0)

        # Scan button
        self.scan_btn = QPushButton("⚡")
        self.scan_btn.setObjectName("scanBtn")
        self.scan_btn.setFixedHeight(30)
        self.scan_btn.setMinimumWidth(75)
        self.scan_btn.setToolTip("Scan now")
        self.scan_btn.clicked.connect(self._start_scan)
        tlay.addWidget(self.scan_btn, 0)

        # status_lbl — kept for compatibility but hidden from top bar
        # Scan progress goes to statusBar() at bottom only
        self.status_lbl = QLabel()
        self.status_lbl.setVisible(False)

        # Filter chips
        self.lbl_filter = QLabel()
        self._update_filter_label()

        root.addWidget(topbar)

        self._live_banner = QLabel(
            "🔴  LIVE TRADING MODE — real money at risk  🔴")
        self._live_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._live_banner.setStyleSheet(
            f"background:#5a0000; color:#ff6666; font-weight:900; "
            f"font-size:12px; padding:4px; border-bottom:1px solid {RED};")
        self._live_banner.setVisible(not TRADING_CFG["testnet"])
        root.addWidget(self._live_banner)

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

        self.statusBar().showMessage("Ready")
        # Scan status dot in status bar
        self._scan_dot = QLabel("⬤")
        self._scan_dot.setStyleSheet("color: #00cc66; font-size:11px; padding:0 4px;")
        self._scan_dot.setToolTip("Scanner idle")
        self.statusBar().addPermanentWidget(self._scan_dot)

        # WebSocket status indicator
        if _WS_AVAILABLE:
            self._ws_status_lbl = QLabel("⚡ WS")
            self._ws_status_lbl.setStyleSheet(
                f"color:{DIM}; font-size:10px; font-weight:700; padding:0 6px;")
            self._ws_status_lbl.setToolTip("WebSocket — waiting for connection")
            self.statusBar().addPermanentWidget(self._ws_status_lbl)

        ver_lbl = QLabel(f"  {APP_VERSION}  ")
        ver_lbl.setStyleSheet(
            f"color:{ACCENT}; font-family:{MONO_CSS}; font-weight:700; "
            f"font-size:11px; padding:0 6px;"
        )
        self.statusBar().addPermanentWidget(ver_lbl)

    def _build_table(self):
        cols = ["#", "Symbol", "Price", "24h%", "RSI", "StRSI",
                "MACD", "BB%", "Vol 24h", "Signal", "Pot%", "Exp%", "L/S", "Pattern", "Chart",
                "AGE", "CONF", "1H", ""]

        COL_TIPS = {
            0:  "Rank — sorted by potential score after each scan",
            1:  "Trading pair (always USDT quoted)",
            2:  "Last traded price in USDT",
            3:  "24-hour price change %\n>+8% adds to short score, <-10% adds to long score",
            4:  "RSI (14-period Relative Strength Index)\n"
                "<30 = oversold → long bias  |  >70 = overbought → short bias\n"
                "Scores: <25=+5, <30=+4, <35=+3, <40=+2 (long)\n"
                "        >75=+5, >70=+4, >65=+3, >60=+2 (short)",
            5:  "Stochastic RSI (0–100)\n"
                "<20 = strongly oversold (+2 long)\n"
                ">80 = strongly overbought (+2 short)",
            6:  "MACD histogram value\n"
                "Positive + rising = bullish momentum (+3 long)\n"
                "Negative + falling = bearish momentum (+3 short)",
            7:  "Bollinger Band position (0–100%)\n"
                "0% = price at lower band (buy zone, +3 long)\n"
                "100% = price at upper band (sell zone, +3 short)\n"
                "Score halved if band width > 12% (wide/noisy bands)",
            8:  "24-hour trading volume in USDT",
            9:  "Signal verdict from the confluence scoring system\n"
                "STRONG BUY:  long ≥ 6 and margin ≥ 3\n"
                "BUY:         long ≥ 3 and margin ≥ 2\n"
                "NEUTRAL:     neither side wins clearly\n"
                "SELL:        short ≥ 3 and margin ≥ 2\n"
                "STRONG SELL: short ≥ 6 and margin ≥ 3",
            10: "Potential score (0–100) — composite urgency metric\n"
                "Signal strength: up to 30pts\n"
                "Volume ratio:    up to 25pts\n"
                "BB proximity:    up to 20pts\n"
                "RSI extremity:   up to 15pts\n"
                "StochRSI:        up to 10pts",
            11: "Expected move % — estimated near-term price range\n"
                "Based on ATR (14-period average true range)\n"
                "Multiplied by 1.4 for STRONG signals, 1.1 for BUY/SELL",
            12: "Long score / Short score\n"
                "Raw indicator confluence points for each direction\n"
                "Higher long score = stronger buy case\n"
                "Example: 7/2 = convincing long, 4/3 = weak edge",
            13: "Last detected candlestick pattern\n"
                "Hammer / Bull Engulf = bullish (+2 long, -1 short)\n"
                "Shooting Star / Bear Engulf = bearish (+2 short, -1 long)\n"
                "Squeeze = BB inside Keltner — breakout imminent (+1)\n"
                "Doji = indecision\n"
                "— = no pattern detected",
            14: "Sparkline — mini price chart of last 50 closes",
            15: "Signal age (mm:ss) — time since this signal first appeared\n"
                "Green < 30s (very fresh)\n"
                "Yellow < 5min\n"
                "Red > 5min (stale — treat with caution)",
            16: "Confirmation count — consecutive scans with the same signal\n"
                "Higher = more reliable. 5+ scans = fully confirmed",
            17: "1-hour trend direction\n"
                "↑ Up  = 1H close above 1H EMA (aligns with long)\n"
                "↓ Down = 1H close below 1H EMA (aligns with short)\n"
                "→ Flat = no clear trend",
        }

        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)

        # Install custom header with per-column tooltips
        tip_header = TooltipHeaderView(Qt.Orientation.Horizontal, COL_TIPS, t)
        tip_header.setStretchLastSection(False)
        tip_header.setSectionsClickable(True)
        tip_header.sectionClicked.connect(self._on_header_clicked)
        t.setHorizontalHeader(tip_header)

        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(False)
        t.setSortingEnabled(False)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(True)

        hdr = t.horizontalHeader()
        for i in range(len(cols) - 1):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(18, QHeaderView.ResizeMode.Stretch)
        t.setColumnHidden(18, True)   # hidden spacer — absorbs leftover width invisibly

        t.itemDoubleClicked.connect(self._on_row_double_click)
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
    def _build_trades_tab(self):
        self._load_trades()
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel("Right-click any coin in the Scanner tab to open a BUY trade.")
        hint.setStyleSheet(f"color:{DIM}; font-size:11px; padding:4px 0;")
        root.addWidget(hint)

        self.tr_summary = QLabel("No trades yet")
        self.tr_summary.setStyleSheet(f"color:{DIM}; font-size:11px; font-weight:700; padding:2px 0;")
        root.addWidget(self.tr_summary)

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

        self.tr_table = QTableWidget(0, 11)
        self.tr_table.setHorizontalHeaderLabels([
            "Opened", "Symbol", "Side", "Entry $", "Qty", "SL $", "TP $", "Live $", "Exit $", "P&L", "Status"
        ])
        self.tr_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tr_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(10, QHeaderView.ResizeMode.ResizeToContents)
        self.tr_table.setColumnWidth(7, 130)   # Live $
        self.tr_table.setColumnWidth(8, 130)   # Exit $
        self.tr_table.setAlternatingRowColors(False)
        self.tr_table.setSortingEnabled(False)
        self.tr_table.setShowGrid(True)
        self.tr_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tr_table.customContextMenuRequested.connect(self._trades_context_menu)
        root.addWidget(self.tr_table)

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

        del_btn = QPushButton("✕  Delete Selected")
        del_btn.setFixedHeight(30)
        del_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; padding:0 14px;"
        )
        del_btn.clicked.connect(self._delete_trade)

        remove_won_btn = QPushButton("🗑  Remove Closed")
        remove_won_btn.setFixedHeight(30)
        remove_won_btn.setToolTip("Remove all closed trades (WIN and LOSS) from history")
        remove_won_btn.setStyleSheet(
            f"background:{CARD}; color:#f0c040; border:1px solid #f0c040; "
            f"border-radius:4px; padding:0 14px;"
        )
        remove_won_btn.clicked.connect(self._remove_won_trades)

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
        btn_row.addWidget(remove_won_btn)
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
        sym = r["symbol"].replace("_USDT", "").replace("USDT", "")
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

        # Always show trading mode so user always knows context before acting
        api_key_set = bool(TRADING_CFG["api_key"] or
                           (hasattr(self, 'cfg_api_key') and self.cfg_api_key.text().strip()))
        if api_key_set:
            env = "🧪 TESTNET" if TRADING_CFG["testnet"] else "🔴 LIVE"
            mode_txt = f"  {env}  —  order will execute on Binance"
        else:
            mode_txt = "  📋 Journal only  —  no API key"
        mode_act = menu.addAction(mode_txt)
        mode_act.setEnabled(False)
        # Style the mode line to stand out
        mode_font = mode_act.font()
        mode_font.setItalic(True)
        mode_act.setFont(mode_font)

        menu.addSeparator()

        long_act  = menu.addAction(f"📈  BUY  {sym}")
        short_act = menu.addAction(f"📉  SELL  {sym}  (margin — coming soon)")
        short_act.setEnabled(False)
        menu.addSeparator()

        detail_act  = menu.addAction("🔍  View Details")
        binance_act = menu.addAction(f"🌐  Open {sym} on Binance")
        tv_act      = menu.addAction(f"📈  Open {sym} on TradingView")

        # Highlight recommended direction
        if sig == "PRE-BREAKOUT":
            long_act.setText(f"📈  BUY  {sym}  ← ⚡ PRE-BREAKOUT")
        elif "BUY" in sig:
            long_act.setText(f"📈  BUY  {sym}  ← {sig}")
        elif "SELL" in sig:
            short_act.setText(f"📉  SELL  {sym}  ← {sig}  (coming soon)")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == long_act:
            self._record_trade(r, "LONG")
        elif action == short_act:
            self._record_trade(r, "SHORT")
        elif action == detail_act:
            self._show_detail_popup(r)
        elif action == binance_act:
            sym_url = sym.replace("_", "")
            open_url(f"https://www.binance.com/en/trade/{sym_url}USDT?type=spot&interval=5m")
        elif action == tv_act:
            sym_url = sym.replace("_", "")
            open_url(f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym_url}USDT&interval=5")

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

        title_act = menu.addAction(f"  {'BUY' if side == 'LONG' else 'SELL'} {sym}")
        title_act.setEnabled(False)
        menu.addSeparator()

        close_act = edit_act = None
        if status == "OPEN":
            # Find current price from last scan results
            cur = (self._live_prices.get(trade["symbol"]) or
                   next((r["price"] for r in self._results if r["symbol"] == trade["symbol"]), None))
            label = f"✓  Close at current price  (${cur:.6f})" if cur else "✓  Close at price..."
            close_act = menu.addAction(label)
            edit_act  = menu.addAction("✎  Edit entry / SL / TP")
            menu.addSeparator()

        del_act = menu.addAction("✕  Delete")

        binance_act2 = menu.addAction(f"🌐  Open {sym} on Binance")
        tv_act2      = menu.addAction(f"📈  Open {sym} on TradingView")

        action = menu.exec(self.tr_table.viewport().mapToGlobal(pos))
        if action == close_act:
            cur = (self._live_prices.get(trade["symbol"]) or
                   next((r["price"] for r in self._results if r["symbol"] == trade["symbol"]), None))
            self._close_trade_dialog(tid=tid, prefill_price=cur)
        elif action == edit_act:
            self._edit_trade_dialog(tid=tid)
        elif action == del_act:
            self._trades = [t for t in self._trades if t["id"] != tid]
            self._save_trades()
            self._refresh_trades_table()
        elif action == binance_act2:
            sym_url = trade["symbol"].replace("_USDT","").replace("USDT","").replace("_","")
            open_url(f"https://www.binance.com/en/trade/{sym_url}USDT?type=spot&interval=5m")
        elif action == tv_act2:
            sym_url = trade["symbol"].replace("_USDT","").replace("USDT","").replace("_","")
            open_url(f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym_url}USDT&interval=5")

    # ── Context menu on ALERTS history table ────────────────
    def _alerts_context_menu(self, pos):
        row = self.alert_log_table.rowAt(pos.y())
        if row < 0:
            return
        time_item = self.alert_log_table.item(row, 0)
        if time_item is None:
            return
        data = time_item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        sym    = data.get("symbol", "")  # stored without USDT (e.g. "KAT")
        sig    = data.get("signal", "")
        price  = data.get("price", 0)
        if not sym:
            return

        sym_full = sym.replace("_", "") + "USDT"   # e.g. "KATUSDT"
        sym_display = sym.replace("USDT", "")       # clean display label

        # Build a result dict compatible with _record_trade (needs full symbol)
        r = {"symbol": sym_full, "signal": sig, "price": price,
             "signal_conf": 2, "trend_1h": "flat"}

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{CARD}; border:1px solid {BORDER}; color:{WHITE}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 20px; border-radius:3px; }}"
            f"QMenu::item:selected {{ background:{ACCENT}; color:{DARK}; }}"
            f"QMenu::separator {{ height:1px; background:{BORDER}; margin:4px 8px; }}"
        )

        title_act = menu.addAction(f"  {sig}  {sym_display}")
        title_act.setEnabled(False)
        menu.addSeparator()

        long_act  = menu.addAction(f"📈  BUY  {sym_display}")
        short_act = menu.addAction(f"📉  SELL  {sym_display}  (margin — coming soon)")
        short_act.setEnabled(False)
        if "BUY" in sig:
            long_act.setText(f"📈  BUY  {sym_display}  ← {sig}")
        elif "SELL" in sig:
            short_act.setText(f"📉  SELL  {sym_display}  ← {sig}  (coming soon)")
        menu.addSeparator()

        binance_act = menu.addAction(f"🌐  Open {sym_display} on Binance")
        tv_act      = menu.addAction(f"📈  Open {sym_display} on TradingView")

        action = menu.exec(self.alert_log_table.viewport().mapToGlobal(pos))
        if action == long_act:
            self._record_trade(r, "LONG")
        elif action == binance_act:
            sym_url = sym.replace("_", "") + "USDT"
            open_url(f"https://www.binance.com/en/trade/{sym_url}?type=spot&interval=5m")
        elif action == tv_act:
            sym_url = sym.replace("_", "") + "USDT"
            open_url(f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym_url}&interval=5")

    # ── Record trade from scanner right-click ───────────────
    def _record_trade(self, r, side):
        """Open trade dialog — fetches live USDT balance, places real order if API configured."""
        sym   = r["symbol"].replace("_USDT", "").replace("USDT", "")
        price = r["price"]
        sl_pct = CFG["sl_pct"] / 100
        tp_pct = CFG["tp_pct"] / 100

        if side == "LONG":
            suggested_sl = round(price * (1 - sl_pct), 8)
            suggested_tp = round(price * (1 + tp_pct), 8)
        else:
            suggested_sl = round(price * (1 + sl_pct), 8)
            suggested_tp = round(price * (1 - tp_pct), 8)

        api_ready = bool(TRADING_CFG["api_key"] and TRADING_CFG["api_secret"])

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Open {'BUY' if side == 'LONG' else 'SELL'} — {sym}")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(f"background:{DARK2}; color:{WHITE};")
        vlay = QVBoxLayout(dlg)
        vlay.setSpacing(10)

        accent = GREEN if side == "LONG" else RED
        icon   = "📈" if side == "LONG" else "📉"

        # Mode banner
        if api_ready:
            env  = "TESTNET" if TRADING_CFG["testnet"] else "LIVE"
            col  = GREEN if TRADING_CFG["testnet"] else RED
            mode_lbl = QLabel(f"{'🧪' if TRADING_CFG['testnet'] else '🔴'}  {env} — order will be placed on Binance")
            mode_lbl.setStyleSheet(
                f"background:{'#003a1a' if TRADING_CFG['testnet'] else '#3a0000'}; "
                f"color:{col}; font-size:11px; font-weight:700; padding:6px; border-radius:4px;")
        else:
            mode_lbl = QLabel("📋  Journal only — no API keys configured")
            mode_lbl.setStyleSheet(
                f"background:#1a1a2e; color:{DIM}; font-size:11px; padding:6px; border-radius:4px;")
        vlay.addWidget(mode_lbl)

        header = QLabel(f"{icon}  <b>{side}</b>  {sym}/USDT  —  ${price:.8f}")
        header.setStyleSheet(f"color:{accent}; font-size:13px; padding:4px 0;")
        vlay.addWidget(header)

        # Balance row — only shown when API ready
        balance_row = QHBoxLayout()
        balance_lbl = QLabel("USDT Balance:")
        balance_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        balance_val = QLabel("Fetching…" if api_ready else "—")
        balance_val.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:700;")
        balance_row.addWidget(balance_lbl)
        balance_row.addWidget(balance_val)
        balance_row.addStretch()
        vlay.addLayout(balance_row)

        # % selector buttons
        pct_row = QHBoxLayout()
        pct_lbl = QLabel("Use:")
        pct_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        pct_row.addWidget(pct_lbl)
        _avail_usdt = [0.0]  # mutable container for closure

        usdt_spin = QDoubleSpinBox()
        usdt_spin.setRange(0, 9999999)
        usdt_spin.setDecimals(2)
        usdt_spin.setValue(0)
        usdt_spin.setEnabled(api_ready)

        for pct in (25, 50, 75, 100):
            btn = QPushButton(f"{pct}%")
            btn.setFixedHeight(26)
            btn.setFixedWidth(48)
            btn.setStyleSheet(
                f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
                f"border-radius:4px; font-size:11px;")
            btn.setEnabled(api_ready)
            def _set_pct(p=pct):
                usdt_spin.setValue(round(_avail_usdt[0] * p / 100, 2))
            btn.clicked.connect(_set_pct)
            pct_row.addWidget(btn)
        pct_row.addStretch()
        vlay.addLayout(pct_row)

        # Main grid
        grid = QGridLayout(); grid.setSpacing(8)
        lbl_s = f"color:{DIM}; font-size:11px;"

        entry_spin = QDoubleSpinBox()
        entry_spin.setRange(0.0000001,999999); entry_spin.setDecimals(8); entry_spin.setValue(price)

        qty_spin = QDoubleSpinBox(); qty_spin.setRange(0, 999999999)
        qty_spin.setDecimals(6); qty_spin.setValue(0)
        qty_spin.setEnabled(not api_ready)  # auto-calculated from USDT when API ready

        sl_spin = QDoubleSpinBox()
        sl_spin.setRange(0.0000001,999999); sl_spin.setDecimals(8); sl_spin.setValue(suggested_sl)

        sl_pct_spin = QDoubleSpinBox(); sl_pct_spin.setRange(0.01, 50)
        sl_pct_spin.setDecimals(2); sl_pct_spin.setSuffix("%")
        sl_pct_spin.setValue(CFG["sl_pct"])
        sl_pct_spin.setFixedWidth(80)
        sl_pct_spin.setToolTip("Stop Loss as % from entry")

        tp_spin = QDoubleSpinBox()
        tp_spin.setRange(0.0000001,999999); tp_spin.setDecimals(8); tp_spin.setValue(suggested_tp)

        tp_pct_spin = QDoubleSpinBox(); tp_pct_spin.setRange(0.01, 100)
        tp_pct_spin.setDecimals(2); tp_pct_spin.setSuffix("%")
        tp_pct_spin.setValue(CFG["tp_pct"])
        tp_pct_spin.setFixedWidth(80)
        tp_pct_spin.setToolTip("Take Profit as % from entry")

        # Sync: price ↔ % (block signals to avoid recursion)
        _syncing = [False]

        def _sl_price_changed():
            if _syncing[0]: return
            e = entry_spin.value()
            s = sl_spin.value()
            if e > 0 and s > 0:
                _syncing[0] = True
                pct = abs(e - s) / e * 100
                sl_pct_spin.setValue(round(pct, 2))
                _syncing[0] = False

        def _sl_pct_changed():
            if _syncing[0]: return
            e = entry_spin.value()
            p = sl_pct_spin.value()
            if e > 0:
                _syncing[0] = True
                new_sl = round(e * (1 - p/100) if side == "LONG" else e * (1 + p/100), 8)
                sl_spin.setValue(new_sl)
                _syncing[0] = False

        def _tp_price_changed():
            if _syncing[0]: return
            e = entry_spin.value()
            t = tp_spin.value()
            if e > 0 and t > 0:
                _syncing[0] = True
                pct = abs(t - e) / e * 100
                tp_pct_spin.setValue(round(pct, 2))
                _syncing[0] = False

        def _tp_pct_changed():
            if _syncing[0]: return
            e = entry_spin.value()
            p = tp_pct_spin.value()
            if e > 0:
                _syncing[0] = True
                new_tp = round(e * (1 + p/100) if side == "LONG" else e * (1 - p/100), 8)
                tp_spin.setValue(new_tp)
                _syncing[0] = False

        sl_spin.valueChanged.connect(_sl_price_changed)
        sl_pct_spin.valueChanged.connect(_sl_pct_changed)
        tp_spin.valueChanged.connect(_tp_price_changed)
        tp_pct_spin.valueChanged.connect(_tp_pct_changed)

        # Also recalc SL/TP when entry price changes
        def _entry_changed():
            _sl_pct_changed()
            _tp_pct_changed()
            _update_hint()
        entry_spin.valueChanged.connect(_entry_changed)

        note_edit = QLineEdit(); note_edit.setPlaceholderText("Optional note…")

        # Select all on focus/click — makes editing any field instant
        def _spin_select_all(spin):
            spin.lineEdit().focusInEvent = lambda e: (
                QLineEdit.focusInEvent(spin.lineEdit(), e),
                QTimer.singleShot(0, spin.selectAll))
            spin.lineEdit().mouseReleaseEvent = lambda e: spin.selectAll()
        for _sp in (usdt_spin, entry_spin, sl_spin, tp_spin, sl_pct_spin, tp_pct_spin):
            _spin_select_all(_sp)

        # USDT amount label for journal mode
        usdt_journal = QDoubleSpinBox()
        usdt_journal.setRange(0, 9999999); usdt_journal.setDecimals(2)
        usdt_journal.setPrefix("$"); usdt_journal.setValue(0)
        usdt_journal.setVisible(not api_ready)

        pnl_hint = QLabel()
        pnl_hint.setStyleSheet(f"color:{DIM}; font-size:11px;")

        def _update_hint():
            e = entry_spin.value()
            if api_ready:
                u = usdt_spin.value()
                q = u / e if e > 0 else 0
                qty_spin.setValue(round(q, 6))
            else:
                q = qty_spin.value()
                u = q * e
            if q > 0 and e > 0:
                sl_risk = abs(sl_spin.value() - e) * q
                tp_gain = abs(tp_spin.value() - e) * q
                rr = tp_gain / sl_risk if sl_risk > 0 else 0
                pnl_hint.setText(
                    f"Cost: ${u:.2f} USDT  |  SL risk: -${sl_risk:.4f}  |  "
                    f"TP gain: +${tp_gain:.4f}  |  R/R: {rr:.2f}x")
            else:
                pnl_hint.setText("Enter amount to see cost and risk")

        for w in (qty_spin, sl_spin, tp_spin, usdt_spin, sl_pct_spin, tp_pct_spin):
            w.valueChanged.connect(_update_hint)

        rows_cfg = [
            ("Entry price", entry_spin),
        ]
        if api_ready:
            rows_cfg.append(("USDT amount", usdt_spin))
        else:
            rows_cfg.append(("USDT amount", usdt_journal))
            rows_cfg.append(("Qty (coins)", qty_spin))

        for i, (lbl, widget) in enumerate(rows_cfg):
            l = QLabel(lbl); l.setStyleSheet(lbl_s)
            grid.addWidget(l, i, 0)
            grid.addWidget(widget, i, 1, 1, 2)

        # SL row — price + % side by side
        row_sl = len(rows_cfg)
        sl_lbl = QLabel("Stop Loss"); sl_lbl.setStyleSheet(lbl_s)
        grid.addWidget(sl_lbl, row_sl, 0)
        grid.addWidget(sl_spin, row_sl, 1)
        grid.addWidget(sl_pct_spin, row_sl, 2)

        # TP row — price + % side by side
        row_tp = row_sl + 1
        tp_lbl = QLabel("Take Profit"); tp_lbl.setStyleSheet(lbl_s)
        grid.addWidget(tp_lbl, row_tp, 0)
        grid.addWidget(tp_spin, row_tp, 1)
        grid.addWidget(tp_pct_spin, row_tp, 2)

        # Note row
        row_note = row_tp + 1
        note_lbl = QLabel("Note"); note_lbl.setStyleSheet(lbl_s)
        grid.addWidget(note_lbl, row_note, 0)
        grid.addWidget(note_edit, row_note, 1, 1, 2)

        grid.setColumnStretch(1, 1)
        vlay.addLayout(grid)
        vlay.addWidget(pnl_hint)

        # OCO note
        if api_ready and TRADING_CFG["oco_enabled"]:
            oco_note = QLabel("🔒  OCO stop-loss will be placed on Binance after buy")
            oco_note.setStyleSheet(f"color:{YELLOW}; font-size:10px; padding:2px 0;")
            vlay.addWidget(oco_note)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton(f"{icon}  Confirm {'BUY' if side == 'LONG' else 'SELL'}")
        ok_btn.setStyleSheet(
            f"background:{'#002a1a' if side=='LONG' else '#2a0010'}; color:{accent}; "
            f"border:1px solid {accent}; border-radius:4px; font-weight:700; padding:4px 16px;")
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px; padding:4px 12px;")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(ok_btn); btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)

        # Status label for order progress
        order_status = QLabel("")
        order_status.setStyleSheet(f"color:{DIM}; font-size:11px; padding:2px 0;")
        order_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vlay.addWidget(order_status)

        # Fetch balance in background
        if api_ready:
            class _BalFetch(QThread):
                done = pyqtSignal(bool, float)
                def run(self_):
                    ok, bal = _trader.get_usdt_balance()
                    self_.done.emit(ok, bal)
            def _on_bal(ok, bal):
                if ok:
                    _avail_usdt[0] = bal
                    balance_val.setText(f"${bal:,.2f} USDT")
                    usdt_spin.setMaximum(bal)
                    usdt_spin.setValue(round(bal, 2))
                    _update_hint()
                else:
                    balance_val.setText("fetch failed")
                    balance_val.setStyleSheet(f"color:{RED}; font-size:11px; font-weight:700;")
            self._bal_thread = _BalFetch()
            self._bal_thread.done.connect(_on_bal)
            self._bal_thread.start()

        _update_hint()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        entry_price = entry_spin.value()
        sl_price    = sl_spin.value()
        tp_price    = tp_spin.value()
        note        = note_edit.text().strip()

        if api_ready:
            usdt_amount = usdt_spin.value()
            if usdt_amount <= 0:
                QMessageBox.warning(self, "Invalid Amount", "USDT amount must be greater than 0.")
                return

            # Pre-check: verify symbol exists on this exchange before showing progress
            symbol = r["symbol"]
            prog_lbl_text = QLabel("Checking symbol…")
            prog_lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            prog_lbl_text.setStyleSheet(f"color:{ACCENT}; font-size:12px;")

            # Trade safety checks
            safety_ok, safety_reason = check_trade_safety(r, self._trades)
            if not safety_ok:
                reply = QMessageBox.warning(
                    self, "Trade Safety Check Failed",
                    f"Safety filter blocked this trade:\n\n{safety_reason}\n\n"
                    f"Override and trade anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            sym_ok, sym_info = _trader.get_symbol_info(symbol)
            if not sym_ok:
                env = "testnet" if TRADING_CFG["testnet"] else "live Binance"
                reply = QMessageBox.warning(
                    self, "Symbol Not on Testnet",
                    f"{symbol} is not available on {env}.\n\n"
                    f"The scanner uses live Binance data but the testnet has fewer coins.\n\n"
                    f"Would you like to record this as a journal trade instead\n"
                    f"(no real order placed)?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                # Fall through to journal mode by disabling api_ready
                api_ready = False

            # Show progress dialog
            prog = QDialog(self)
            prog.setWindowTitle("Placing Order…")
            prog.setModal(True)
            prog.setFixedSize(320, 100)
            prog.setStyleSheet(f"background:{DARK2}; color:{WHITE};")
            prog_lay = QVBoxLayout(prog)
            prog_lbl = QLabel("Placing market BUY on Binance…")
            prog_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            prog_lbl.setStyleSheet(f"color:{ACCENT}; font-size:12px;")
            prog_lay.addWidget(prog_lbl)
            prog.show()
            QApplication.processEvents()

            # Place market BUY
            ok, order = _trader.place_market_buy(symbol, usdt_amount)

            if not ok:
                prog.close()
                QMessageBox.critical(
                    self, "Order Failed",
                    f"Market BUY failed:\n\n{order.get('error', str(order))}\n\n"
                    f"Trade was NOT recorded."
                )
                return

            # Extract fill details from order response
            fills      = order.get("fills", [])
            filled_qty = float(order.get("executedQty", 0))
            if fills:
                avg_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / filled_qty
            else:
                avg_price = entry_price
            order_id   = order.get("orderId")

            # Recalculate SL/TP based on actual fill price
            if side == "LONG":
                sl_price = round(avg_price * (1 - sl_pct), 8)
                tp_price = round(avg_price * (1 + tp_pct), 8)
            else:
                sl_price = round(avg_price * (1 + sl_pct), 8)
                tp_price = round(avg_price * (1 - tp_pct), 8)

            oco_order_id = None

            # Place OCO if enabled
            if TRADING_CFG["oco_enabled"] and side == "LONG":
                prog_lbl.setText("Placing OCO stop-loss…")
                QApplication.processEvents()
                sl_limit = round(sl_price * 0.999, 8)  # limit 0.1% below stop
                oco_ok, oco_data = _trader.place_oco_sell(
                    symbol, filled_qty, tp_price, sl_price, sl_limit)
                if oco_ok:
                    oco_order_id = oco_data.get("orderListId")
                else:
                    # OCO failed — warn but don't abort (trade is already filled)
                    QMessageBox.warning(
                        self, "OCO Warning",
                        f"BUY was filled but OCO stop-loss failed:\n"
                        f"{oco_data.get('error', str(oco_data))}\n\n"
                        f"Trade is recorded. Monitor manually or set OCO manually on Binance."
                    )

            prog.close()

            # Round stored qty to step size — must exactly match what Binance holds
            ok_info, sym_info = _trader.get_symbol_info(symbol)
            step = sym_info.get("stepSize", 0.00000001) if ok_info else 0.00000001
            stored_qty = _trader.round_step(filled_qty, step)

            trade = {
                "id":           int(datetime.now().timestamp() * 1000),
                "time":         datetime.now().strftime("%m-%d %H:%M"),
                "symbol":       symbol,
                "side":         side,
                "entry":        round(avg_price, 8),
                "qty":          stored_qty,
                "sl":           sl_price,
                "tp":           tp_price,
                "note":         note,
                "exit":         None, "pnl": None, "pnl_pct": None,
                "status":       "OPEN",
                "binance_order_id": order_id,
                "binance_oco_id":   oco_order_id,
                "live":         not TRADING_CFG["testnet"],
            }
            status_msg = (
                f"✓ BUY filled: {sym} {filled_qty:.6f} @ ${avg_price:.6f}"
                + (f"  |  OCO set" if oco_order_id else "")
            )

        else:
            # Journal-only mode
            qty_val = usdt_journal.value() / entry_price if usdt_journal.value() > 0 else qty_spin.value()
            trade = {
                "id":     int(datetime.now().timestamp() * 1000),
                "time":   datetime.now().strftime("%m-%d %H:%M"),
                "symbol": r["symbol"],
                "side":   side,
                "entry":  entry_price,
                "qty":    round(qty_val, 8),
                "sl":     sl_price,
                "tp":     tp_price,
                "note":   note,
                "exit":   None, "pnl": None, "pnl_pct": None,
                "status": "OPEN",
                "binance_order_id": None,
                "binance_oco_id":   None,
                "live":   False,
            }
            status_msg = f"Opened {side} {sym} @ ${entry_price:.6f} (journal only)"

        self._trades.insert(0, trade)
        self._log_trade_event("OPEN", trade)
        self._save_trades()
        self._refresh_trades_table()

        # Switch to Trades tab
        tabs = self.centralWidget().findChild(QTabWidget)
        if tabs:
            for i in range(tabs.count()):
                if "Trade" in tabs.tabText(i):
                    tabs.setCurrentIndex(i)
                    break
        self._show_status(status_msg)

    # ── Close trade dialog ───────────────────────────────────
    def _close_trade_dialog(self, checked=False, tid=None, prefill_price=None):
        # If called from button (no tid), get selected row
        if tid is None:
            row = self.tr_table.currentRow()
            if row < 0:
                self._show_status("Select a trade row first")
                return
            item = self.tr_table.item(row, 0)
            if item is None: return
            tid = item.data(Qt.ItemDataRole.UserRole)

        trade = next((t for t in self._trades if t["id"] == tid), None)
        if trade is None or trade["status"] != "OPEN":
            self._show_status("Trade is already closed")
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
        exit_spin.setRange(0.0000001,999999); exit_spin.setDecimals(8)
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

        api_ready   = bool(TRADING_CFG["api_key"] and TRADING_CFG["api_secret"])
        order_id    = trade.get("binance_order_id")
        oco_id      = trade.get("binance_oco_id")
        symbol_full = trade["symbol"]

        if api_ready and order_id is not None:
            # Cancel OCO first
            if oco_id is not None:
                c_ok, c_data = _trader.cancel_oco(symbol_full, oco_id)
                if not c_ok:
                    self.statusBar().showMessage(
                        f"OCO cancel note: {c_data.get('error','')[:60]}")

            # Round qty to step size so it exactly matches Binance holdings
            _, s_info = _trader.get_symbol_info(symbol_full)
            sell_qty = _trader.round_step(qty, s_info.get("stepSize", 0.00000001))

            # Place market SELL — if insufficient balance, retry with actual balance
            s_ok, s_data = _trader.place_market_sell(symbol_full, sell_qty)
            if not s_ok and "-2010" in str(s_data):
                # Balance mismatch — fetch real balance and retry
                asset = symbol_full.replace("USDT", "")
                bal_ok, actual_bal = _trader.get_asset_balance(asset)
                if bal_ok and actual_bal > 0:
                    _, s_info = _trader.get_symbol_info(symbol_full)
                    sell_qty = _trader.round_step(actual_bal, s_info.get("stepSize", 1))
                    s_ok, s_data = _trader.place_market_sell(symbol_full, sell_qty)
                    if s_ok:
                        qty = sell_qty  # update qty for P&L calc
            if not s_ok:
                QMessageBox.critical(
                    self, "Sell Failed",
                    f"Market SELL failed:\n\n{s_data.get('error', str(s_data))}\n\n"
                    f"Trade was NOT closed. Check Binance manually.")
                return

            fills    = s_data.get("fills", [])
            exec_qty = float(s_data.get("executedQty", qty))
            if fills and exec_qty > 0:
                ep = sum(float(f["price"]) * float(f["qty"]) for f in fills) / exec_qty
            if side == "LONG":
                pnl = (ep - entry) * exec_qty
                pct = (ep - entry) / entry * 100
            else:
                pnl = (entry - ep) * exec_qty
                pct = (entry - ep) / entry * 100
            record_trade_loss(pnl)  # update daily loss tracker

        trade["exit"]         = round(ep, 8)
        trade["pnl"]          = round(pnl, 8)
        trade["pnl_pct"]      = round(pct, 4)
        trade["status"]       = "WIN" if pnl >= 0 else "LOSS"
        trade["closed"]       = datetime.now().strftime("%m-%d %H:%M")
        trade["close_reason"] = "MANUAL"
        self._log_trade_event("CLOSE", trade)
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
                self._show_status("Select a trade row first")
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
        # Subscribe new symbol to WebSocket
        if self._ws_feed:
            syms = {t["symbol"] for t in self._trades if t["status"] == "OPEN"}
            self._ws_feed.subscribe(syms)
        self._show_status(f"Trade updated: {side} {sym}")

    # ── Delete selected trade ────────────────────────────────
    def _delete_trade(self):
        rows = self.tr_table.selectionModel().selectedRows()
        if not rows:
            self._show_status("Select one or more trade rows first")
            return
        tids = set()
        for idx in rows:
            item = self.tr_table.item(idx.row(), 0)
            if item:
                tids.add(item.data(Qt.ItemDataRole.UserRole))
        if not tids:
            return
        reply = QMessageBox.question(
            self, "Delete Trades",
            f"Delete {len(tids)} trade(s)? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._trades = [t for t in self._trades if t["id"] not in tids]
        self._save_trades()
        self._refresh_trades_table()

    def _remove_won_trades(self):
        closed = [t for t in self._trades if t["status"] in ("WIN", "LOSS")]
        if not closed:
            self._show_status("No closed trades to remove")
            return
        wins   = sum(1 for t in closed if t["status"] == "WIN")
        losses = sum(1 for t in closed if t["status"] == "LOSS")
        reply = QMessageBox.question(
            self, "Remove Closed Trades",
            f"Remove all {len(closed)} closed trade(s) from history?\n"
            f"({wins} wins, {losses} losses)\n\n"
            f"Open trades will not be affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        closed_ids = {t["id"] for t in closed}
        self._trades = [t for t in self._trades if t["id"] not in closed_ids]
        self._save_trades()
        self._refresh_trades_table()
        self._show_status(f"Removed {len(closed_ids)} closed trade(s)")

    # ── Refresh trades table ─────────────────────────────────
    def _refresh_trades_table(self):
        try:
            self._do_refresh_trades_table()
        except Exception as e:
            import traceback; traceback.print_exc()

    def _do_refresh_trades_table(self):
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
            side_label = "BUY" if side == "LONG" else "SELL"

            if trade.get("pnl") is not None:
                pnl     = trade["pnl"]
                pct     = trade.get("pnl_pct", 0) or 0
                sign    = "+" if pct >= 0 else ""
                if pnl == 0 and pct != 0:
                    # Journal-only trade with no qty — show % only
                    pnl_str = f"{sign}{pct:.2f}%  (no qty)"
                else:
                    pnl_str = f"{sign}{pnl:.4f} USDT  ({sign}{pct:.2f}%)"
                pnl_col = GREEN if pct >= 0 else RED
            else:
                # Live unrealised P&L for open trades
                sym_full2 = trade["symbol"]
                cur = (self._live_prices.get(sym_full2) or
                       next((res["price"] for res in self._results if res["symbol"] == sym_full2), None))
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
                    if qty > 0:
                        pnl_str = f"{sign}{upnl:.4f} ({sign}{upct:.2f}%)"
                    else:
                        pnl_str = f"{sign}{upct:.2f}%"
                    pnl_col = GREEN if upct >= 0 else RED
                else:
                    pnl_str = "⏳ fetching…"
                    pnl_col = DIM

            status_col = ACCENT if status == "OPEN" else (GREEN if (trade.get("pnl") or 0) >= 0 else RED)
            # Exit price — only show when trade is closed
            if trade.get("exit") and status != "OPEN":
                exit_str = f"${trade['exit']:.8f}"
            else:
                exit_str = "—"

            # Live price — shown for open trades
            sym_full = trade["symbol"]
            live_price = (self._live_prices.get(sym_full) or
                         next((res["price"] for res in self._results if res["symbol"] == sym_full), None))
            if status == "OPEN":
                live_str = f"${live_price:.8f}" if live_price else "⏳"
                live_col = ACCENT
            else:
                live_str = "—"
                live_col = DIM

            self.tr_table.setItem(r, 0,  cell(trade["time"],              DIM,       align=left))
            self.tr_table.setItem(r, 1,  cell(trade["symbol"].replace("USDT",""), ACCENT, bold=True, align=left))
            self.tr_table.setItem(r, 2,  cell(side_label,                 side_col,  bold=True, align=center))
            self.tr_table.setItem(r, 3,  cell(f"${trade['entry']:.8f}",   WHITE))
            self.tr_table.setItem(r, 4,  cell(f"{trade['qty']}",          WHITE))
            self.tr_table.setItem(r, 5,  cell(f"${trade['sl']:.8f}" if trade.get("sl") else "—", DIM))
            self.tr_table.setItem(r, 6,  cell(f"${trade['tp']:.8f}" if trade.get("tp") else "—", DIM))
            self.tr_table.setItem(r, 7,  cell(live_str,                   live_col, bold=True))
            self.tr_table.setItem(r, 8,  cell(exit_str,                   WHITE))
            self.tr_table.setItem(r, 9,  cell(pnl_str,                    pnl_col, bold=True))
            self.tr_table.setItem(r, 10, cell(status,                     status_col, bold=True, align=center))

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

    TRADES_FILE = os.path.join(APP_LOGS_DIR, "trades.json")
    TRADE_LOG   = os.path.join(APP_LOGS_DIR, "trade_log.txt")

    def _save_trades(self):
        try:
            # Atomic write: write to temp file then rename to avoid corruption
            tmp = self.TRADES_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._trades, f, indent=2)
            os.replace(tmp, self.TRADES_FILE)
        except Exception as e:
            self._show_status(f"Trade save error: {e}")

    def _log_trade_event(self, event: str, trade: dict):
        """Append a timestamped line to the audit log file."""
        try:
            ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            env = "TESTNET" if TRADING_CFG["testnet"] else "LIVE"
            sym = trade.get("symbol", "")
            sid = trade.get("side", "")
            line = (f"{ts}  [{env}]  {event:12s}  {sid:5s}  {sym:12s}  "
                    f"entry={trade.get('entry','')}  qty={trade.get('qty','')}  "
                    f"sl={trade.get('sl','')}  tp={trade.get('tp','')}  "
                    f"exit={trade.get('exit','')}  pnl={trade.get('pnl','')}  "
                    f"status={trade.get('status','')}  "
                    f"binance_order={trade.get('binance_order_id','')}  "
                    f"oco={trade.get('binance_oco_id','')}\n")
            with open(self.TRADE_LOG, "a") as f:
                f.write(line)
        except Exception:
            pass

    def _load_trades(self):
        try:
            if os.path.exists(self.TRADES_FILE):
                with open(self.TRADES_FILE, "r") as f:
                    data = json.load(f)
                # Validate it's a list of dicts
                if isinstance(data, list):
                    self._trades = [t for t in data if isinstance(t, dict)]
                else:
                    self._trades = []
            else:
                self._trades = []
        except Exception as e:
            print(f"[WARN] Could not load trades: {e} — starting fresh")
            self._trades = []

    # ─────────────────────────────────────────────────────────
    #  TAB SWITCH — live price fetch when Trades tab opened
    # ─────────────────────────────────────────────────────────
    def _on_tab_changed(self, index):
        tab_text = self._tabs_widget.tabText(index)
        if tab_text.startswith("💰"):
            # Switched to Trades tab — trigger immediate refresh
            self._fetch_open_trade_prices()

    def _fetch_open_trade_prices(self):
        """REST polling for open trade prices — always runs as safety net."""
        open_trades = [t for t in self._trades if t["status"] == "OPEN"]
        if not open_trades:
            return
        if getattr(self, '_trade_price_fetch_running', False):
            return
        # Ensure WS is subscribed to trade symbols
        if self._ws_feed:
            syms = {t["symbol"] for t in open_trades}
            self._ws_feed.subscribe(syms | self._ws_feed._subscribed)

        open_syms = list({t["symbol"] for t in open_trades})
        self._trade_price_fetch_running = True

        def _worker():
            try:
                # One lightweight call per open symbol
                base = CFG["base_url"]
                for sym in open_syms:
                    try:
                        resp = requests.get(
                            f"{base}/api/v3/ticker/price",
                            params={"symbol": sym},
                            timeout=5
                        )
                        d = resp.json()
                        if isinstance(d, dict) and "price" in d:
                            self._live_prices[sym] = float(d["price"])
                    except Exception:
                        pass
                # Mirror into scan results
                for r in self._results:
                    if r["symbol"] in self._live_prices:
                        r["price"] = self._live_prices[r["symbol"]]
            except Exception:
                pass

        def _done():
            self._trade_price_fetch_running = False
            self._check_sltp_hits(self._results)
            self._refresh_trades_table()

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
        """Called after every scan.
        Wrapped in try/except — a crash here must never stop the app."""
        try:
            self._check_sltp_hits_inner(results)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._show_status(f"⚠ SL/TP check error: {str(e)[:60]}")

    def _check_sltp_hits_inner(self, results):
        """Called after every scan. Auto-closes open trades whose SL or TP has been crossed.
        TP hit: app places market SELL + cancels OCO.
        SL hit: OCO handles it on Binance — app just records it.
        """
        price_map = {r["symbol"]: r["price"] for r in results}
        price_map.update(getattr(self, "_live_prices", {}))
        hits = []

        for trade in self._trades:
            if trade["status"] != "OPEN":
                continue
            sym   = trade["symbol"]
            price = price_map.get(sym)
            if price is None:
                continue

            side     = trade["side"]
            sl       = trade.get("sl")
            tp       = trade.get("tp")
            hit_type = None

            if side == "LONG":
                if sl and price <= sl:   hit_type = "SL"
                elif tp and price >= tp: hit_type = "TP"
            else:
                if sl and price >= sl:   hit_type = "SL"
                elif tp and price <= tp: hit_type = "TP"

            if hit_type:
                hits.append((trade, hit_type, price))

        for trade, hit_type, price in hits:
            side        = trade["side"]
            entry       = trade["entry"]
            qty         = trade.get("qty", 0)
            symbol_full = trade["symbol"]
            sym_short   = symbol_full.replace("USDT", "")
            oco_id      = trade.get("binance_oco_id")
            order_id    = trade.get("binance_order_id")
            api_ready   = bool(TRADING_CFG["api_key"] and TRADING_CFG["api_secret"])
            exit_price  = price

            if hit_type == "TP" and api_ready and order_id is not None:
                # Cancel OCO then place market SELL
                if oco_id is not None:
                    _trader.cancel_oco(symbol_full, oco_id)

                # Round to step size so qty exactly matches Binance holdings
                _, s_info = _trader.get_symbol_info(symbol_full)
                sell_qty = _trader.round_step(qty, s_info.get("stepSize", 0.00000001))
                s_ok, s_data = _trader.place_market_sell(symbol_full, sell_qty)
                if not s_ok and "-2010" in str(s_data):
                    # Retry with actual balance
                    asset = symbol_full.replace("USDT", "")
                    _, actual_bal = _trader.get_asset_balance(asset)
                    if actual_bal > 0:
                        _, s_info = _trader.get_symbol_info(symbol_full)
                        sell_qty = _trader.round_step(actual_bal, s_info.get("stepSize", 1))
                        s_ok, s_data = _trader.place_market_sell(symbol_full, sell_qty)
                if s_ok:
                    fills    = s_data.get("fills", [])
                    exec_qty = float(s_data.get("executedQty", qty))
                    if fills and exec_qty > 0:
                        exit_price = sum(
                            float(f["price"]) * float(f["qty"]) for f in fills
                        ) / exec_qty
                    qty = exec_qty
                else:
                    err = s_data.get("error", str(s_data))[:80]
                    self.statusBar().showMessage(
                        f"⚠ TP SELL FAILED for {sym_short}: {err}")
                    continue  # don't close in journal if sell failed

            elif hit_type == "SL" and api_ready and oco_id is not None:
                # OCO already handled this on Binance — just record in journal
                pass

            # P&L
            if side == "LONG":
                pnl = (exit_price - entry) * qty
                pct = (exit_price - entry) / entry * 100
            else:
                pnl = (entry - exit_price) * qty
                pct = (entry - exit_price) / entry * 100

            trade["exit"]         = round(exit_price, 8)
            trade["pnl"]          = round(pnl, 8)
            trade["pnl_pct"]      = round(pct, 4)
            trade["status"]       = "WIN" if pnl >= 0 else "LOSS"
            trade["closed"]       = datetime.now().strftime("%m-%d %H:%M")
            trade["close_reason"] = hit_type
            self._log_trade_event(f"AUTO_{hit_type}", trade)

            sign = "+" if pnl >= 0 else ""
            msg  = (f"{'🎯' if hit_type=='TP' else '🛑'}  {hit_type} HIT  {side} {sym_short}  "
                    f"@ ${exit_price:.6f}  P&L: {sign}{pnl:.4f} USDT ({sign}{pct:.2f}%)")

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
        fpath = os.path.join(APP_LOGS_DIR, fname)
        os.makedirs(APP_LOGS_DIR, exist_ok=True)
        try:
            with open(fpath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "id","time","symbol","side","entry","qty","sl","tp",
                    "exit","pnl","pnl_pct","status","closed","close_reason","note"
                ])
                writer.writeheader()
                for t in self._trades:
                    writer.writerow({k: t.get(k,"") for k in writer.fieldnames})
            self._show_status(f"Trades exported → {fpath}")
        except Exception as e:
            self._show_status(f"CSV export error: {e}")

    def _build_alerts_tab(self):
        # Two sub-tabs: Settings | History
        outer = QWidget()
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        self._alerts_sub_tabs = QTabWidget()
        self._alerts_sub_tabs.setDocumentMode(False)
        self._alerts_sub_tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {BORDER};
                border-radius: 8px;
                background: {DARK};
                top: -1px;
            }}
            QTabBar {{
                alignment: left;
            }}
            QTabBar::tab {{
                font-family: {MONO_CSS}; font-size: 11px; font-weight: 700;
                padding: 7px 20px;
                color: {DIM};
                background: {CARD};
                border: 1px solid {BORDER};
                border-bottom: none;
                border-radius: 6px 6px 0 0;
                margin-right: 3px;
                min-width: 110px;
            }}
            QTabBar::tab:selected {{
                color: {ACCENT};
                background: {DARK};
                border-bottom: 1px solid {DARK};
            }}
            QTabBar::tab:hover:!selected {{
                color: {WHITE};
                background: {CARD};
            }}
        """)
        outer_lay.addWidget(self._alerts_sub_tabs)

        # ── SUB-TAB 1: SETTINGS ──────────────────────────────
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        auto_grp = QGroupBox("AUTO-SCAN & TRIGGER")
        aglay = QGridLayout(auto_grp)
        aglay.setSpacing(6)
        aglay.setColumnMinimumWidth(0, 160)
        aglay.setColumnStretch(0, 0)
        aglay.setColumnStretch(1, 1)

        self.al_enabled = QCheckBox("Enable auto-scan alerts")
        self.al_enabled.setChecked(ALERT_CFG["enabled"])
        self.al_enabled.setStyleSheet(f"color:{WHITE};")

        self.al_interval = QSpinBox()
        self.al_interval.setFixedWidth(160)
        self.al_interval.setRange(30, 3600)
        self.al_interval.setValue(ALERT_CFG["interval_sec"])
        self.al_interval.setSuffix("s")

        self.al_min_signal = QComboBox()
        self.al_min_signal.setFixedWidth(160)
        for s in ["PRE-BREAKOUT", "BUY", "STRONG BUY"]:
            self.al_min_signal.addItem(s)
        self.al_min_signal.setCurrentText(ALERT_CFG["min_signal"])

        self.al_min_pot = QSpinBox()
        self.al_min_pot.setFixedWidth(160)
        self.al_min_pot.setRange(0, 100)
        self.al_min_pot.setValue(ALERT_CFG["min_potential"])
        self.al_min_pot.setSuffix("%")

        self.al_min_exp = QDoubleSpinBox()
        self.al_min_exp.setFixedWidth(160)
        self.al_min_exp.setRange(0, 50)
        self.al_min_exp.setValue(ALERT_CFG["min_exp_move"])
        self.al_min_exp.setSuffix("%")

        self.al_max_rsi = QSpinBox()
        self.al_max_rsi.setFixedWidth(160)
        self.al_max_rsi.setRange(1, 100)
        self.al_max_rsi.setValue(ALERT_CFG["max_rsi"])
        self.al_max_rsi.setToolTip("Only alert if RSI is below this value")

        self.al_max_bb = QSpinBox()
        self.al_max_bb.setFixedWidth(160)
        self.al_max_bb.setRange(0, 200)
        self.al_max_bb.setValue(ALERT_CFG["max_bb_pct"])
        self.al_max_bb.setSuffix("%")
        self.al_max_bb.setToolTip("Only alert if BB% is below this")

        self.al_vol_spike = QCheckBox("Require volume spike")
        self.al_vol_spike.setChecked(ALERT_CFG["require_vol_spike"])
        self.al_vol_spike.setStyleSheet(f"color:{WHITE};")
        self.al_vol_spike.setToolTip("Only alert if unusual volume detected")

        # Fix 1 — block downtrend
        self.al_block_downtrend = QCheckBox("Block Downtrend pattern")
        self.al_block_downtrend.setChecked(ALERT_CFG.get("block_downtrend", True))
        self.al_block_downtrend.setStyleSheet(f"color:{WHITE};")
        self.al_block_downtrend.setToolTip("Skip alerts when candlestick pattern shows Downtrend ↓")

        # Fix 2 — min vol ratio
        self.al_min_vol_ratio = QDoubleSpinBox()
        self.al_min_vol_ratio.setFixedWidth(160)
        self.al_min_vol_ratio.setRange(0, 5)
        self.al_min_vol_ratio.setDecimals(1)
        self.al_min_vol_ratio.setValue(ALERT_CFG.get("min_vol_ratio", 0.8))
        self.al_min_vol_ratio.setSuffix("x avg vol")
        self.al_min_vol_ratio.setToolTip(
            "Minimum volume ratio vs average\n"
            "ROBO alerts had 0.3x-0.7x — dying volume after dump\n"
            "0.8x = only alert if volume is near normal or above"
        )

        # Fix 3 — spike cooldown
        self.al_spike_cooldown = QCheckBox("Post-spike cooldown  >")
        self.al_spike_cooldown.setChecked(ALERT_CFG.get("spike_cooldown", True))
        self.al_spike_cooldown.setStyleSheet(f"color:{WHITE};")
        self.al_spike_cooldown_pct = QDoubleSpinBox()
        self.al_spike_cooldown_pct.setFixedWidth(100)
        self.al_spike_cooldown_pct.setRange(5, 50)
        self.al_spike_cooldown_pct.setDecimals(0)
        self.al_spike_cooldown_pct.setValue(ALERT_CFG.get("spike_pct", 15.0))
        self.al_spike_cooldown_pct.setSuffix("% spike → 2hr block")
        self.al_spike_cooldown_pct.setEnabled(ALERT_CFG.get("spike_cooldown", True))
        self.al_spike_cooldown.toggled.connect(self.al_spike_cooldown_pct.setEnabled)
        self.al_spike_cooldown.setToolTip(
            "If coin spiked more than this % in last 3h → block alerts for 2 hours\n"
            "Prevents chasing dump-after-pump signals like ROBO"
        )

        # Fix 4 — MACD rising
        self.al_require_macd = QCheckBox("Require MACD rising")

        # Fix 5 — per-coin cooldown
        self.al_coin_cooldown = QCheckBox("Per-coin cooldown")
        self.al_coin_cooldown.setChecked(ALERT_CFG.get("coin_cooldown", True))
        self.al_coin_cooldown.setStyleSheet(f"color:{WHITE};")
        self.al_coin_cooldown_mins = QSpinBox()
        self.al_coin_cooldown_mins.setFixedWidth(100)
        self.al_coin_cooldown_mins.setRange(5, 240)
        self.al_coin_cooldown_mins.setValue(ALERT_CFG.get("coin_cooldown_mins", 30))
        self.al_coin_cooldown_mins.setSuffix(" min cooldown")
        self.al_coin_cooldown_mins.setEnabled(ALERT_CFG.get("coin_cooldown", True))
        self.al_coin_cooldown.toggled.connect(self.al_coin_cooldown_mins.setEnabled)
        self.al_coin_cooldown.setToolTip(
            "Once a coin alerts, block it for this many minutes\n"
            "Prevents ROBO-style spam (82 alerts from 1 coin today)\n"
            "30 min = each coin can alert at most twice per hour"
        )
        self.al_require_macd.setChecked(ALERT_CFG.get("require_macd_rising", False))
        self.al_require_macd.setStyleSheet(f"color:{WHITE};")
        self.al_require_macd.setToolTip(
            "Only alert if MACD histogram is rising (bullish momentum building)\n"
            "Stricter — eliminates signals with fading momentum"
        )

        self.al_min_adr = QDoubleSpinBox()
        self.al_min_adr.setFixedWidth(160)
        self.al_min_adr.setRange(0, 20)
        self.al_min_adr.setDecimals(1)
        self.al_min_adr.setValue(ALERT_CFG.get("min_adr_pct", 0.5))
        self.al_min_adr.setSuffix("%")
        self.al_min_adr.setToolTip(
            "Skip coins with avg candle range below this\n"
            "NIGHT ~1% → set 2%+ to exclude flat coins\n"
            "Higher = only coins with real price movement"
        )

        rows = [
            ("Scan interval",   self.al_interval),
            ("Minimum signal",  self.al_min_signal),
            ("Min Potential %", self.al_min_pot),
            ("Min Exp Move %",  self.al_min_exp),
            ("Max RSI",         self.al_max_rsi),
            ("Max BB%",         self.al_max_bb),
            ("Min ADR %",       self.al_min_adr),
            ("Min Vol Ratio",   self.al_min_vol_ratio),
        ]
        aglay.addWidget(self.al_enabled, 0, 0, 1, 2)
        for i, (lbl_text, widget) in enumerate(rows, 1):
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(f"color:{DIM};")
            aglay.addWidget(lbl, i, 0)
            aglay.addWidget(widget, i, 1, Qt.AlignmentFlag.AlignLeft)
        row_offset = len(rows) + 1
        aglay.addWidget(self.al_vol_spike,          row_offset,     0, 1, 2)
        aglay.addWidget(self.al_block_downtrend,    row_offset + 1, 0, 1, 2)
        aglay.addWidget(self.al_require_macd,       row_offset + 2, 0, 1, 2)
        # Spike cooldown row
        spike_row = QHBoxLayout()
        spike_row.addWidget(self.al_spike_cooldown)
        spike_row.addWidget(self.al_spike_cooldown_pct)
        spike_row.addStretch()
        spike_w = QWidget(); spike_w.setLayout(spike_row)
        aglay.addWidget(spike_w,                    row_offset + 3, 0, 1, 2)
        # Per-coin cooldown row
        cooldown_row = QHBoxLayout()
        cooldown_row.addWidget(self.al_coin_cooldown)
        cooldown_row.addWidget(self.al_coin_cooldown_mins)
        cooldown_row.addStretch()
        cooldown_w = QWidget(); cooldown_w.setLayout(cooldown_row)
        aglay.addWidget(cooldown_w,                 row_offset + 4, 0, 1, 2)

        # Top row: auto-scan left, notification channels right
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(auto_grp, 1)

        ch_grp = QGroupBox("NOTIFICATION CHANNELS")
        chlay = QVBoxLayout(ch_grp)
        chlay.setSpacing(4)
        chlay.setContentsMargins(12, 8, 12, 8)

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

        top_row.addWidget(ch_grp, 1)
        lay.addLayout(top_row)

        btn_row = QHBoxLayout()
        apply_btn = QPushButton("✓  Apply Alert Settings")
        apply_btn.clicked.connect(self._apply_alert_config)
        test_btn = QPushButton("🔔  Test Alerts Now")
        test_btn.clicked.connect(self._test_alert)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(test_btn)
        lay.addLayout(btn_row)

        lay.addStretch()
        settings_scroll.setWidget(w)
        # ── SUB-TAB 1: ALERTS / HISTORY ──────────────────────
        history_w = QWidget()
        history_lay = QVBoxLayout(history_w)
        history_lay.setContentsMargins(16, 12, 16, 12)
        history_lay.setSpacing(8)

        hist_header = QHBoxLayout()
        hist_title = QLabel("ALERT HISTORY")
        hist_title.setStyleSheet(f"color:{ACCENT}; font-weight:800; font-size:13px; font-family:{MONO_CSS};")
        hist_header.addWidget(hist_title)
        hist_header.addStretch()
        hist_clear2 = QPushButton("🗑  Clear Log")
        hist_clear2.setFixedHeight(28)
        hist_clear2.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; font-size:11px; padding:0 10px;")
        hist_clear2.clicked.connect(self._clear_alert_log)
        hist_header.addWidget(hist_clear2)
        history_lay.addLayout(hist_header)

        self.al_history_stats = QLabel("No alerts yet — waiting for signals")
        self.al_history_stats.setStyleSheet(f"color:{DIM}; font-size:11px;")
        history_lay.addWidget(self.al_history_stats)

        # Table — same look as Trades tab
        self.alert_log_table = QTableWidget(0, 5)
        self.alert_log_table.setHorizontalHeaderLabels(
            ["TIME", "SYMBOL", "SIGNAL", "DETAILS", "PRICE"]
        )
        self.alert_log_table.verticalHeader().setVisible(False)
        self.alert_log_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.alert_log_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alert_log_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.alert_log_table.setAlternatingRowColors(False)
        self.alert_log_table.setSortingEnabled(False)
        self.alert_log_table.setShowGrid(True)
        self.alert_log_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.alert_log_table.customContextMenuRequested.connect(self._alerts_context_menu)
        al_hdr = self.alert_log_table.horizontalHeader()
        al_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        al_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        al_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        al_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        al_hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        al_hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        history_lay.addWidget(self.alert_log_table)

        # Keep legacy scroll-based layout as no-ops for backward compat
        self.alert_log_widget2 = QWidget()
        self.alert_log_layout2 = QVBoxLayout(self.alert_log_widget2)
        self.alert_log_widget = self.alert_log_widget2
        self.alert_log_layout = self.alert_log_layout2

        self._alerts_sub_tabs.addTab(history_w, "📋  Alerts  (0)")
        self._alerts_sub_tabs.addTab(settings_scroll, "⚙  Settings")

        return outer

    def _apply_alert_config(self):
        ALERT_CFG["enabled"]          = self.al_enabled.isChecked()
        self._refresh_alert_toggle()
        ALERT_CFG["interval_sec"]     = self.al_interval.value()
        ALERT_CFG["min_signal"]       = self.al_min_signal.currentText()
        ALERT_CFG["min_potential"]    = self.al_min_pot.value()
        ALERT_CFG["min_exp_move"]     = self.al_min_exp.value()
        ALERT_CFG["max_rsi"]          = self.al_max_rsi.value()
        ALERT_CFG["max_bb_pct"]       = self.al_max_bb.value()
        ALERT_CFG["require_vol_spike"]    = self.al_vol_spike.isChecked()
        ALERT_CFG["min_adr_pct"]          = self.al_min_adr.value()
        ALERT_CFG["block_downtrend"]      = self.al_block_downtrend.isChecked()
        ALERT_CFG["min_vol_ratio"]        = self.al_min_vol_ratio.value()
        ALERT_CFG["spike_cooldown"]       = self.al_spike_cooldown.isChecked()
        ALERT_CFG["spike_pct"]            = self.al_spike_cooldown_pct.value()
        ALERT_CFG["require_macd_rising"]  = self.al_require_macd.isChecked()
        ALERT_CFG["coin_cooldown"]        = self.al_coin_cooldown.isChecked()
        ALERT_CFG["coin_cooldown_mins"]   = self.al_coin_cooldown_mins.value()
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
        self._show_status("Test alert fired — check sound / desktop / Telegram / WhatsApp queue")

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

    def _update_history_tab_badge(self):
        """Update Alerts tab title with alert count."""
        count = self.alert_log_table.rowCount() if hasattr(self, 'alert_log_table') else 0
        if hasattr(self, '_alerts_sub_tabs'):
            self._alerts_sub_tabs.setTabText(0, f"📋  Alerts  ({count})")
        if hasattr(self, 'al_history_stats'):
            if count > 0:
                self.al_history_stats.setText(f"{count} alert{'s' if count != 1 else ''} — latest at top")
            else:
                self.al_history_stats.setText("No alerts yet — waiting for signals")

    def _add_alert_row(self, alert, flash=True):
        """Insert a single alert row into the history table."""
        if not hasattr(self, 'alert_log_table'):
            return
        sig = alert.get("signal", "")
        col = GREEN if "BUY" in sig else (RED if "SELL" in sig else "#ff9900")

        try:
            detail_text = (
                f"RSI {float(alert.get('rsi',0)):.0f}  ·  "
                f"Exp {float(alert.get('exp',0)):.1f}%  ·  "
                f"Pot {alert.get('pot',0)}%  ·  "
                f"Vol {float(alert.get('vol',0)):.1f}x  ·  "
                f"{alert.get('pattern','')}"
            )
        except Exception:
            detail_text = str(alert.get("pattern", ""))
        try:
            price_text = f"${float(alert.get('price', 0)):.5f}"
        except Exception:
            price_text = str(alert.get("price", ""))

        tbl = self.alert_log_table
        tbl.insertRow(0)  # insert at top — newest first

        def cell(text, color=None, bold=False, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter):
            item = QTableWidgetItem(str(text))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if color:
                item.setForeground(QColor(color))
            if bold:
                f = item.font(); f.setBold(True); item.setFont(f)
            item.setTextAlignment(align)
            return item

        tbl.setItem(0, 0, cell(alert.get("time", ""), DIM))
        # Store full symbol (with USDT) and signal in UserRole for context menu
        time_item = tbl.item(0, 0)
        time_item.setData(Qt.ItemDataRole.UserRole, {
            "symbol": alert.get("symbol", ""),
            "signal": sig,
            "price":  alert.get("price", 0),
        })
        tbl.setItem(0, 1, cell(alert.get("symbol", "").replace("USDT",""), ACCENT, bold=True))
        tbl.setItem(0, 2, cell(sig, col, bold=True))
        tbl.setItem(0, 3, cell(detail_text, DIM))
        tbl.setItem(0, 4, cell(price_text, WHITE,
                               align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))

        # Limit to 20 rows
        while tbl.rowCount() > 20:
            tbl.removeRow(tbl.rowCount() - 1)

        if flash and hasattr(self, '_alerts_sub_tabs'):
            self._alerts_sub_tabs.setCurrentIndex(0)

    def _on_new_alert(self, alert):
        """Called on main thread via signal — add to log + trigger all visual alerts."""
        self._alert_log.append(alert)
        # Keep in-memory list to max 20
        if len(self._alert_log) > 20:
            self._alert_log = self._alert_log[-20:]

        sig = alert["signal"]
        sym = alert["symbol"]

        self._flash_window(sig)
        self._start_title_flash(sig, sym)
        self._update_status_alert(sig, sym)
        if "STRONG" in sig:
            self._show_strong_popup(alert)

        self._add_alert_row(alert, flash=True)
        self._update_history_tab_badge()

    def _on_ws_connected(self):
        """WebSocket connected — update status bar indicator."""
        if hasattr(self, '_ws_status_lbl'):
            self._ws_status_lbl.setText("⚡ WS")
            self._ws_status_lbl.setStyleSheet(
                f"color:#00cc66; font-size:10px; font-weight:700; padding:0 6px;")
            self._ws_status_lbl.setToolTip("WebSocket connected — live price feed active")

    def _on_ws_disconnected(self):
        """WebSocket disconnected — update status bar indicator."""
        if hasattr(self, '_ws_status_lbl'):
            self._ws_status_lbl.setText("⚡ WS")
            self._ws_status_lbl.setStyleSheet(
                f"color:{YELLOW}; font-size:10px; font-weight:700; padding:0 6px;")
            self._ws_status_lbl.setToolTip("WebSocket disconnected — reconnecting…")

    def _on_ws_price(self, symbol: str, price: float):
        """Called on every WebSocket price tick — updates live prices and checks TP/SL."""
        self._live_prices[symbol] = price
        # Mirror into scan results
        for r in self._results:
            if r["symbol"] == symbol:
                r["price"] = price
        # Refresh trades display (throttled — only if Trades tab visible or trade open)
        if not getattr(self, "_ws_refresh_pending", False):
            self._ws_refresh_pending = True
            QTimer.singleShot(100, self._ws_flush)

    def _ws_flush(self):
        """Flush accumulated WebSocket updates to UI — max 2x per second."""
        self._ws_refresh_pending = False
        self._check_sltp_hits(self._results)
        self._refresh_trades_table()

    def _on_alert_scan_started(self):
        """Background scan just started — update button to show scanning state."""
        if self._worker is None or not self._worker.isRunning():
            self.scan_btn.setEnabled(False)
            self.scan_btn.setText("⏳")
            self._set_dot_scanning()

    def _on_alert_scan_done(self, results):
        """Background alert scan completed — update table silently if no manual scan running."""
        if self._worker is None or not self._worker.isRunning():
            self._results = results
            self._refresh_display()
            self._populate_picks(results)
            self._check_sltp_hits(results)
            self._refresh_trades_table()
            self._refresh_balance_display()
            n = len(results)
            self.statusBar().showMessage(
                f"Auto-scan: {n} coins  [{datetime.now().strftime('%H:%M:%S')}]")
            self.scan_btn.setEnabled(True)
            self.scan_btn.setText("⚡")
            self._set_dot_idle(n)
            # Log all signals for audit
            threading.Thread(
                target=log_scan_results,
                args=(results,),
                kwargs={"trades": self._trades},
                daemon=True
            ).start()
            # Update log size label
            QTimer.singleShot(2000, self._update_signal_log_size)
            # Start/update WebSocket with scanned symbols
            if self._ws_feed:
                syms = {r["symbol"] for r in results}
                syms |= {t["symbol"] for t in self._trades if t["status"] == "OPEN"}
                self._ws_feed.subscribe(syms)
                if not self._ws_feed._running:
                    self._ws_feed.start()

    def _clear_alert_log(self):
        self._alert_log.clear()
        if hasattr(self, 'alert_log_table'):
            self.alert_log_table.setRowCount(0)
        self._update_history_tab_badge()
        if hasattr(self, 'al_history_stats'):
            self.al_history_stats.setText("No alerts yet — waiting for signals")

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
        # Wrap everything in a scroll area so content never clips on small windows
        outer = QWidget()
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)

        def _cfg_grid(parent):
            """Grid with fixed label column + stretching widget column."""
            g = QGridLayout(parent)
            g.setSpacing(12)
            g.setColumnMinimumWidth(0, 160)
            g.setColumnStretch(0, 0)
            g.setColumnStretch(1, 1)
            return g

        # Filter settings
        filter_grp = QGroupBox("SCAN FILTERS")
        flay = _cfg_grid(filter_grp)

        self.cfg_max_price = QDoubleSpinBox()
        self.cfg_max_price.setRange(0.01,100); self.cfg_max_price.setDecimals(2)
        self.cfg_max_price.setPrefix("$"); self.cfg_max_price.setValue(CFG["max_price"]); self.cfg_max_price.setFixedWidth(160)

        self.cfg_min_vol = QDoubleSpinBox()
        self.cfg_min_vol.setRange(100000,1e9); self.cfg_min_vol.setDecimals(0)
        self.cfg_min_vol.setPrefix("$"); self.cfg_min_vol.setSingleStep(100000)
        self.cfg_min_vol.setValue(CFG["min_volume_usdt"]); self.cfg_min_vol.setFixedWidth(160)

        self.cfg_interval = QComboBox()
        self.cfg_interval.setFixedWidth(160)
        for iv in ["1m","3m","5m","15m","30m","1h"]:
            self.cfg_interval.addItem(iv)
        self.cfg_interval.setCurrentText(CFG["interval"])

        self.cfg_top_n = QSpinBox()
        self.cfg_top_n.setRange(5,100); self.cfg_top_n.setValue(CFG["top_n"]); self.cfg_top_n.setFixedWidth(160)

        self.cfg_picks_n = QSpinBox()
        self.cfg_picks_n.setRange(1,20); self.cfg_picks_n.setValue(CFG["picks_n"]); self.cfg_picks_n.setFixedWidth(160)
        self.cfg_picks_n.setToolTip("Max cards shown per section in Top Picks tab")

        self.cfg_candles = QSpinBox()
        self.cfg_candles.setRange(20,200); self.cfg_candles.setValue(CFG["candle_limit"]); self.cfg_candles.setFixedWidth(160)

        rows = [
            ("Max Price ($)",       self.cfg_max_price),
            ("Min Volume (USDT)",   self.cfg_min_vol),
            ("Interval",            self.cfg_interval),
            ("Top N coins",         self.cfg_top_n),
            ("Top Picks to show",   self.cfg_picks_n),
            ("Candles to fetch",    self.cfg_candles),
        ]
        for i, (lbl, widget) in enumerate(rows):
            l = QLabel(lbl); l.setStyleSheet(f"color:{DIM};")
            flay.addWidget(l, i, 0)
            flay.addWidget(widget, i, 1, Qt.AlignmentFlag.AlignLeft)

        # ── 2-column grid layout ─────────────────────────
        grid2 = QGridLayout()
        grid2.setSpacing(12)
        grid2.setColumnStretch(0, 1)
        grid2.setColumnStretch(1, 1)
        grid2.addWidget(filter_grp,  0, 0)

        # Risk settings
        risk_grp = QGroupBox("RISK MANAGEMENT")
        rlay = _cfg_grid(risk_grp)

        self.cfg_sl  = QDoubleSpinBox(); self.cfg_sl.setRange(0.5, 20); self.cfg_sl.setValue(CFG["sl_pct"]); self.cfg_sl.setSuffix("%"); self.cfg_sl.setFixedWidth(160)
        self.cfg_tp  = QDoubleSpinBox(); self.cfg_tp.setRange(0.5, 50); self.cfg_tp.setValue(CFG["tp_pct"]); self.cfg_tp.setSuffix("%"); self.cfg_tp.setFixedWidth(160)
        self.cfg_tp2 = QDoubleSpinBox(); self.cfg_tp2.setRange(1.0, 100); self.cfg_tp2.setValue(CFG["tp2_pct"]); self.cfg_tp2.setSuffix("%"); self.cfg_tp2.setFixedWidth(160)

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
            rlay.addWidget(widget, i, 1, Qt.AlignmentFlag.AlignLeft)

        rr_lbl_title = QLabel("R/R Ratio"); rr_lbl_title.setStyleSheet(f"color:{DIM};")
        rlay.addWidget(rr_lbl_title, len(risk_rows), 0)
        rlay.addWidget(self.rr_lbl, len(risk_rows), 1)
        grid2.addWidget(risk_grp,   0, 1)

        # ── TRADE SAFETY ─────────────────────────────────
        safety_grp = QGroupBox("TRADE SAFETY")
        slay = _cfg_grid(safety_grp)

        def _safety_row(row, label, cfg_key, spin_widget=None, spin_cfg_key=None):
            """Add a checkbox row with optional spinbox."""
            cb = QCheckBox(label)
            cb.setChecked(SAFETY_CFG[cfg_key])
            cb.setStyleSheet(f"color:{WHITE};")
            slay.addWidget(cb, row, 0, 1, 2 if spin_widget is None else 1)
            if spin_widget is not None:
                spin_widget.setEnabled(SAFETY_CFG[cfg_key])
                spin_widget.setFixedWidth(100)
                slay.addWidget(spin_widget, row, 1, Qt.AlignmentFlag.AlignLeft)
                cb.toggled.connect(spin_widget.setEnabled)
            return cb

        self.sf_persistence  = QCheckBox("Signal must hold 2+ consecutive scans")
        self.sf_persistence.setChecked(SAFETY_CFG["signal_persistence"])
        self.sf_persistence.setStyleSheet(f"color:{WHITE};")
        self.sf_persistence.setToolTip("Eliminates false signals that appear for only one scan")

        self.sf_btc_check    = QCheckBox("Skip if BTC dropping >")
        self.sf_btc_check.setChecked(SAFETY_CFG["btc_trend_check"])
        self.sf_btc_check.setStyleSheet(f"color:{WHITE};")
        self.sf_btc_drop     = QDoubleSpinBox()
        self.sf_btc_drop.setRange(0.5, 10); self.sf_btc_drop.setValue(SAFETY_CFG["btc_drop_pct"])
        self.sf_btc_drop.setSuffix("%"); self.sf_btc_drop.setFixedWidth(100)
        self.sf_btc_drop.setEnabled(SAFETY_CFG["btc_trend_check"])
        self.sf_btc_check.toggled.connect(self.sf_btc_drop.setEnabled)

        self.sf_coin_check   = QCheckBox("Skip if coin down >")
        self.sf_coin_check.setChecked(SAFETY_CFG["coin_trend_check"])
        self.sf_coin_check.setStyleSheet(f"color:{WHITE};")
        self.sf_coin_drop    = QDoubleSpinBox()
        self.sf_coin_drop.setRange(1, 20); self.sf_coin_drop.setValue(SAFETY_CFG["coin_drop_pct"])
        self.sf_coin_drop.setSuffix("% in 24h"); self.sf_coin_drop.setFixedWidth(120)
        self.sf_coin_drop.setEnabled(SAFETY_CFG["coin_trend_check"])
        self.sf_coin_check.toggled.connect(self.sf_coin_drop.setEnabled)

        self.sf_max_trades   = QCheckBox("Max open trades")
        self.sf_max_trades.setChecked(SAFETY_CFG["max_open_trades"])
        self.sf_max_trades.setStyleSheet(f"color:{WHITE};")
        self.sf_max_trades_n = QSpinBox()
        self.sf_max_trades_n.setRange(1, 20); self.sf_max_trades_n.setValue(SAFETY_CFG["max_open_trades_count"])
        self.sf_max_trades_n.setFixedWidth(100)
        self.sf_max_trades_n.setEnabled(SAFETY_CFG["max_open_trades"])
        self.sf_max_trades.toggled.connect(self.sf_max_trades_n.setEnabled)

        self.sf_daily_loss   = QCheckBox("Daily loss limit  $")
        self.sf_daily_loss.setChecked(SAFETY_CFG["daily_loss_limit"])
        self.sf_daily_loss.setStyleSheet(f"color:{WHITE};")
        self.sf_daily_loss_n = QDoubleSpinBox()
        self.sf_daily_loss_n.setRange(10, 10000); self.sf_daily_loss_n.setValue(SAFETY_CFG["daily_loss_amount"])
        self.sf_daily_loss_n.setPrefix("$"); self.sf_daily_loss_n.setFixedWidth(100)
        self.sf_daily_loss_n.setEnabled(SAFETY_CFG["daily_loss_limit"])
        self.sf_daily_loss.toggled.connect(self.sf_daily_loss_n.setEnabled)

        # Daily loss tracker reset button
        self.sf_reset_btn = QPushButton("Reset Daily Loss")
        self.sf_reset_btn.setFixedWidth(140)
        self.sf_reset_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; padding:2px 8px; font-size:11px;")
        def _reset_daily():
            _daily_loss_tracker["loss"] = 0.0
            _daily_loss_tracker["date"] = ""
            self.statusBar().showMessage("Daily loss counter reset")
        self.sf_reset_btn.clicked.connect(_reset_daily)

        slay.addWidget(self.sf_persistence,  0, 0, 1, 2)
        slay.addWidget(self.sf_btc_check,    1, 0)
        slay.addWidget(self.sf_btc_drop,     1, 1, Qt.AlignmentFlag.AlignLeft)

        # Fix 1 — BTC drop cooldown
        self.sf_btc_cooldown_check = QCheckBox("  BTC drop cooldown")
        self.sf_btc_cooldown_check.setChecked(True)
        self.sf_btc_cooldown_check.setStyleSheet(f"color:{WHITE};")
        self.sf_btc_cooldown_check.setToolTip(
            "After BTC drop triggers, block new LONGs for this many minutes.\n"
            "Lifts early if BTC recovers the % set to the right."
        )
        self.sf_btc_cooldown_mins = QSpinBox()
        self.sf_btc_cooldown_mins.setRange(5, 240)
        self.sf_btc_cooldown_mins.setValue(SAFETY_CFG.get("btc_drop_cooldown_mins", 60))
        self.sf_btc_cooldown_mins.setSuffix(" min block")
        self.sf_btc_cooldown_mins.setFixedWidth(120)
        self.sf_btc_recovery = QDoubleSpinBox()
        self.sf_btc_recovery.setRange(0.5, 10.0)
        self.sf_btc_recovery.setValue(SAFETY_CFG.get("btc_recovery_pct", 1.5))
        self.sf_btc_recovery.setSuffix("% BTC recovery to lift early")
        self.sf_btc_recovery.setFixedWidth(210)
        slay.addWidget(self.sf_btc_cooldown_check, 2, 0)
        slay.addWidget(self.sf_btc_cooldown_mins,  2, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_btc_recovery,       2, 2, Qt.AlignmentFlag.AlignLeft)

        # Fix 2 — 1h trend freshness
        self.sf_trend_freshness = QCheckBox("Override stale trend_1h='up' if price fell >")
        self.sf_trend_freshness.setChecked(SAFETY_CFG.get("trend_1h_freshness", True))
        self.sf_trend_freshness.setStyleSheet(f"color:{WHITE};")
        self.sf_trend_freshness.setToolTip(
            "If the coin has already dropped this much from its 1h candle open,\n"
            "treat trend_1h='up' as stale and block the LONG."
        )
        self.sf_trend_stale = QDoubleSpinBox()
        self.sf_trend_stale.setRange(0.5, 10.0)
        self.sf_trend_stale.setValue(SAFETY_CFG.get("trend_1h_stale_pct", 1.5))
        self.sf_trend_stale.setSuffix("% below 1h open")
        self.sf_trend_stale.setFixedWidth(160)
        self.sf_trend_stale.setEnabled(SAFETY_CFG.get("trend_1h_freshness", True))
        self.sf_trend_freshness.toggled.connect(self.sf_trend_stale.setEnabled)
        slay.addWidget(self.sf_trend_freshness, 3, 0)
        slay.addWidget(self.sf_trend_stale,     3, 1, Qt.AlignmentFlag.AlignLeft)

        # Fix 3 — Per-symbol recovery gate
        self.sf_sym_recovery = QCheckBox("Per-symbol recovery gate after safety block")
        self.sf_sym_recovery.setChecked(SAFETY_CFG.get("symbol_recovery_gate", True))
        self.sf_sym_recovery.setStyleSheet(f"color:{WHITE};")
        self.sf_sym_recovery.setToolTip(
            "After a safety block fires for a coin, require its price to bounce\n"
            "by this % before a new LONG is allowed."
        )
        self.sf_sym_recovery_pct = QDoubleSpinBox()
        self.sf_sym_recovery_pct.setRange(0.2, 10.0)
        self.sf_sym_recovery_pct.setValue(SAFETY_CFG.get("symbol_recovery_pct", 1.0))
        self.sf_sym_recovery_pct.setSuffix("% bounce required")
        self.sf_sym_recovery_pct.setFixedWidth(160)
        self.sf_sym_recovery_pct.setEnabled(SAFETY_CFG.get("symbol_recovery_gate", True))
        self.sf_sym_expiry = QSpinBox()
        self.sf_sym_expiry.setRange(5, 120)
        self.sf_sym_expiry.setValue(SAFETY_CFG.get("symbol_recovery_expiry_mins", 30))
        self.sf_sym_expiry.setSuffix(" min max lock")
        self.sf_sym_expiry.setFixedWidth(130)
        self.sf_sym_expiry.setEnabled(SAFETY_CFG.get("symbol_recovery_gate", True))
        self.sf_sym_recovery.toggled.connect(self.sf_sym_recovery_pct.setEnabled)
        self.sf_sym_recovery.toggled.connect(self.sf_sym_expiry.setEnabled)
        slay.addWidget(self.sf_sym_recovery,     4, 0)
        slay.addWidget(self.sf_sym_recovery_pct, 4, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_sym_expiry,       4, 2, Qt.AlignmentFlag.AlignLeft)

        slay.addWidget(self.sf_coin_check,   5, 0)
        slay.addWidget(self.sf_coin_drop,    5, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_max_trades,   6, 0)
        slay.addWidget(self.sf_max_trades_n, 6, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_daily_loss,   7, 0)
        slay.addWidget(self.sf_daily_loss_n, 7, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_reset_btn,    8, 0, 1, 2)

        grid2.addWidget(safety_grp, 1, 0)

        ui_grp = QGroupBox("UI APPEARANCE")
        ulay   = _cfg_grid(ui_grp)

        self.cfg_font_size = QSpinBox()
        self.cfg_font_size.setRange(8, 20)
        self.cfg_font_size.setValue(FONT_SIZE)
        self.cfg_font_size.setSuffix(" px")
        self.cfg_font_size.setFixedWidth(160)
        self.cfg_font_size.setToolTip("Base font size — all text scales proportionally")

        fs_lbl = QLabel("Font Size")
        fs_lbl.setStyleSheet(f"color:{DIM};")
        fs_hint = QLabel("Resize the window to test layout at any font size")
        fs_hint.setStyleSheet(f"color:{DIM}; font-size:10px;")

        ulay.addWidget(fs_lbl,            0, 0)
        ulay.addWidget(self.cfg_font_size, 0, 1, Qt.AlignmentFlag.AlignLeft)
        ulay.addWidget(fs_hint,            1, 0, 1, 2)
        grid2.addWidget(ui_grp,     1, 1)

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
        grid2.addWidget(alert_grp,  2, 0)

        export_grp = QGroupBox("EXPORT SCAN RESULTS")
        elay = QVBoxLayout(export_grp)
        elay.setSpacing(8)

        export_btn = QPushButton("↓  Export Last Scan to JSON")
        export_btn.setFixedHeight(34)
        export_btn.setStyleSheet(
            f"background:{CARD}; color:{ACCENT}; border:1px solid {ACCENT}; "
            f"border-radius:4px; font-weight:700; padding:0 14px;"
        )
        export_btn.clicked.connect(self._export)
        self.cfg_export_lbl = QLabel("No scan yet")
        self.cfg_export_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        exp_row = QHBoxLayout()
        exp_row.addWidget(export_btn)
        exp_row.addSpacing(12)
        exp_row.addWidget(self.cfg_export_lbl)
        exp_row.addStretch()
        elay.addLayout(exp_row)

        # Signal audit log
        signal_log_btn = QPushButton("📋  Open Signal Log")
        signal_log_btn.setFixedHeight(30)
        signal_log_btn.setStyleSheet(
            f"background:{CARD}; color:{YELLOW}; border:1px solid {YELLOW}; "
            f"border-radius:4px; font-size:11px; padding:0 12px;")
        signal_log_btn.setToolTip(f"CSV audit log of all scan results: {SIGNAL_LOG_PATH}")
        signal_log_btn.clicked.connect(self._open_signal_log)

        outcome_btn = QPushButton("📊  Outcome Analysis")
        outcome_btn.setFixedHeight(30)
        outcome_btn.setStyleSheet(
            f"background:{CARD}; color:#00cc99; border:1px solid #00cc99; "
            f"border-radius:4px; font-size:11px; padding:0 12px;")
        outcome_btn.setToolTip("Analyse alert outcomes — WIN/LOSS/FLAT rates from signal log")
        outcome_btn.clicked.connect(self._show_outcome_analysis)

        self._signal_log_size_lbl = QLabel()
        self._signal_log_size_lbl.setStyleSheet(f"color:{DIM}; font-size:10px;")
        self._update_signal_log_size()

        clear_log_btn = QPushButton("🗑  Clear")
        clear_log_btn.setFixedHeight(30)
        clear_log_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; font-size:11px; padding:0 10px;")
        clear_log_btn.clicked.connect(self._clear_signal_log)

        log_row = QHBoxLayout()
        log_row.addWidget(signal_log_btn)
        log_row.addWidget(outcome_btn)
        log_row.addWidget(clear_log_btn)
        log_row.addWidget(self._signal_log_size_lbl)
        log_row.addStretch()
        elay.addLayout(log_row)

        grid2.addWidget(export_grp, 2, 1)
        lay.addLayout(grid2)

        api_grp = QGroupBox("BINANCE API  —  TRADING")
        api_grp.setStyleSheet(
            f"QGroupBox {{ border:1px solid {YELLOW}; border-radius:6px; "
            f"margin-top:8px; color:{YELLOW}; font-weight:700; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 4px; }}"
        )
        aplay = QVBoxLayout(api_grp)
        aplay.setSpacing(8)

        # Testnet / Live toggle row
        mode_row = QHBoxLayout()
        self.cfg_testnet = QPushButton("🧪  TESTNET MODE  (safe)")
        self.cfg_testnet.setCheckable(True)
        self.cfg_testnet.setChecked(TRADING_CFG["testnet"])
        self.cfg_testnet.setFixedHeight(32)
        self._refresh_trading_mode_btn()
        self.cfg_testnet.clicked.connect(self._on_trading_mode_toggle)
        mode_row.addWidget(self.cfg_testnet)
        mode_row.addStretch()
        aplay.addLayout(mode_row)

        # Warning label — shown only in live mode
        self.cfg_live_warning = QLabel(
            "⚠  LIVE MODE — real money at risk. "
            "API key must have TRADE permission only. NO withdrawal permission.")
        self.cfg_live_warning.setStyleSheet(
            f"color:{RED}; font-size:11px; font-weight:700; padding:4px 0;")
        self.cfg_live_warning.setWordWrap(True)
        self.cfg_live_warning.setVisible(not TRADING_CFG["testnet"])
        aplay.addWidget(self.cfg_live_warning)

        # Key fields grid
        key_grid = QGridLayout()
        key_grid.setSpacing(8)
        key_grid.setColumnMinimumWidth(0, 100)
        key_grid.setColumnStretch(1, 1)

        key_lbl = QLabel("API Key:")
        key_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        self.cfg_api_key = QLineEdit()
        self.cfg_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.cfg_api_key.setPlaceholderText("Paste your Binance API key here")
        self.cfg_api_key.setText(TRADING_CFG["api_key"])

        sec_lbl = QLabel("Secret:")
        sec_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        self.cfg_api_secret = QLineEdit()
        self.cfg_api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.cfg_api_secret.setPlaceholderText("Paste your Binance API secret here")
        self.cfg_api_secret.setText(TRADING_CFG["api_secret"])

        # Show/hide toggle
        show_btn = QPushButton("👁")
        show_btn.setFixedSize(28, 28)
        show_btn.setCheckable(True)
        show_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px;")
        show_btn.setToolTip("Show / hide keys")
        def _toggle_echo(checked):
            mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            self.cfg_api_key.setEchoMode(mode)
            self.cfg_api_secret.setEchoMode(mode)
        show_btn.toggled.connect(_toggle_echo)

        key_grid.addWidget(key_lbl,              0, 0)
        key_grid.addWidget(self.cfg_api_key,     0, 1)
        key_grid.addWidget(show_btn,             0, 2, 2, 1)
        key_grid.addWidget(sec_lbl,              1, 0)
        key_grid.addWidget(self.cfg_api_secret,  1, 1)
        aplay.addLayout(key_grid)

        # OCO toggle
        oco_row = QHBoxLayout()
        self.cfg_oco = QCheckBox("Place OCO stop-loss on Binance after each buy")
        self.cfg_oco.setChecked(TRADING_CFG["oco_enabled"])
        self.cfg_oco.setStyleSheet(f"color:{DIM}; font-size:11px;")
        self.cfg_oco.setToolTip(
            "OCO = One-Cancels-the-Other order\n"
            "Places a stop-loss directly on Binance — protects you even if the app is closed.\n"
            "Disable to use in-app monitoring only.")
        oco_row.addWidget(self.cfg_oco)
        oco_row.addStretch()
        aplay.addLayout(oco_row)

        # Test connection button + result label
        conn_row = QHBoxLayout()
        self.cfg_conn_btn = QPushButton("🔌  Test Connection")
        self.cfg_conn_btn.setFixedHeight(30)
        self.cfg_conn_btn.setStyleSheet(
            f"background:{CARD}; color:{ACCENT}; border:1px solid {ACCENT}; "
            f"border-radius:4px; font-weight:700; padding:0 14px;")
        self.cfg_conn_btn.clicked.connect(self._test_api_connection)
        self.cfg_conn_lbl = QLabel("Not tested")
        self.cfg_conn_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        conn_row.addWidget(self.cfg_conn_btn)
        conn_row.addSpacing(10)
        conn_row.addWidget(self.cfg_conn_lbl)
        conn_row.addStretch()
        aplay.addLayout(conn_row)

        lay.addWidget(api_grp)

        browser_grp = QGroupBox("BROWSER")
        blay = QHBoxLayout(browser_grp)
        blay.setSpacing(8)

        browser_lbl = QLabel("Browser path:")
        browser_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")

        self.cfg_browser = QLineEdit()
        self.cfg_browser.setPlaceholderText(
            "Leave empty for system default  (e.g. /usr/bin/firefox  or  /usr/bin/brave)")
        self.cfg_browser.setText(BROWSER_PATH)
        self.cfg_browser.setToolTip(
            "Full path to browser binary. Leave empty to use xdg-open / system default.")

        browse_btn = QPushButton("…")
        browse_btn.setFixedWidth(32)
        browse_btn.setFixedHeight(28)
        browse_btn.setToolTip("Pick browser binary")
        browse_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px;")
        browse_btn.clicked.connect(self._pick_browser)

        test_browser_btn = QPushButton("🌐  Test")
        test_browser_btn.setFixedHeight(28)
        test_browser_btn.setStyleSheet(
            f"background:{CARD}; color:{ACCENT}; border:1px solid {ACCENT}; "
            f"border-radius:4px; padding:0 10px;")
        test_browser_btn.setToolTip("Open Binance in the configured browser")
        test_browser_btn.clicked.connect(
            lambda: open_url("https://www.binance.com"))

        blay.addWidget(browser_lbl)
        blay.addWidget(self.cfg_browser, 1)
        blay.addWidget(browse_btn)
        blay.addWidget(test_browser_btn)
        lay.addWidget(browser_grp)

        apply_btn = QPushButton("✓  Apply Settings")
        apply_btn.clicked.connect(self._apply_config)
        lay.addWidget(apply_btn)
        lay.addStretch()

        scroll.setWidget(w)
        outer_lay.addWidget(scroll)
        return outer

    def _pick_browser(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Browser Binary", "/usr/bin",
            "Executables (*)"
        )
        if path:
            self.cfg_browser.setText(path)

    def _refresh_balance_display(self):
        """Fetch USDT balance in background and update the top bar label."""
        if not TRADING_CFG["api_key"]:
            self._balance_lbl.setText("💰 —")
            return

        self._balance_lbl.setText("💰 …")

        class _BalFetch(QThread):
            done = pyqtSignal(bool, float)
            def run(self_):
                ok, bal = _trader.get_usdt_balance()
                self_.done.emit(ok, bal)

        def _on_done(ok, bal):
            if ok:
                env = "T" if TRADING_CFG["testnet"] else "L"
                self._balance_lbl.setText(f"💰 {bal:,.2f} USDT [{env}]")
                col = GREEN if TRADING_CFG["testnet"] else RED
                self._balance_lbl.setStyleSheet(
                    f"color:{col}; font-family:{MONO_CSS}; font-size:11px; "
                    f"font-weight:700; padding:0 8px;")
            else:
                self._balance_lbl.setText("💰 —")

        self._bal_fetch_thread = _BalFetch()
        self._bal_fetch_thread.done.connect(_on_done)
        self._bal_fetch_thread.start()

    def _refresh_trading_mode_btn(self):
        """Update the testnet/live toggle button appearance."""
        if not hasattr(self, 'cfg_testnet'):
            return
        if TRADING_CFG["testnet"]:
            self.cfg_testnet.setText("🧪  TESTNET MODE  (safe)")
            self.cfg_testnet.setStyleSheet(
                f"background:#003a1a; color:{GREEN}; border:1px solid {GREEN}; "
                f"border-radius:4px; font-size:12px; font-weight:700; padding:0 14px;")
        else:
            self.cfg_testnet.setText("🔴  LIVE MODE  — real money")
            self.cfg_testnet.setStyleSheet(
                f"background:#3a0000; color:{RED}; border:2px solid {RED}; "
                f"border-radius:4px; font-size:12px; font-weight:700; padding:0 14px;")

    def _on_trading_mode_toggle(self):
        is_live = not self.cfg_testnet.isChecked()
        if is_live:
            reply = QMessageBox.warning(
                self, "Switch to LIVE Trading",
                "⚠  You are switching to LIVE mode.\n\n"
                "Real money will be used for all trades.\n"
                "Make sure your API key has TRADE permission only — NO withdrawal.\n\n"
                "Are you sure?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.cfg_testnet.setChecked(True)
                return
        TRADING_CFG["testnet"] = self.cfg_testnet.isChecked()
        self._refresh_trading_mode_btn()
        self.cfg_live_warning.setVisible(not TRADING_CFG["testnet"])
        self._refresh_live_banner()
        mode = "TESTNET" if TRADING_CFG["testnet"] else "LIVE"
        self.statusBar().showMessage(f"Trading mode switched to {mode}")
        self.cfg_conn_lbl.setText("Not tested")

    def _refresh_live_banner(self):
        """Show/hide the red live mode banner in the main window title area."""
        is_live = not TRADING_CFG["testnet"]
        if hasattr(self, '_live_banner'):
            self._live_banner.setVisible(is_live)

    def _test_api_connection(self):
        """Test connection button — runs in a thread so UI doesn't freeze."""
        self.cfg_conn_btn.setEnabled(False)
        self.cfg_conn_lbl.setText("Testing…")
        self.cfg_conn_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")

        # Save current key values first
        TRADING_CFG["api_key"]    = self.cfg_api_key.text().strip()
        TRADING_CFG["api_secret"] = self.cfg_api_secret.text().strip()
        TRADING_CFG["oco_enabled"] = self.cfg_oco.isChecked()

        class _ConnTest(QThread):
            result = pyqtSignal(bool, str)
            def run(self_):
                ok, msg = _trader.test_connection()
                self_.result.emit(ok, msg)

        self._conn_thread = _ConnTest()
        def _on_result(ok, msg):
            self.cfg_conn_btn.setEnabled(True)
            if ok:
                self.cfg_conn_lbl.setText(msg)
                self.cfg_conn_lbl.setStyleSheet(
                    f"color:{GREEN}; font-size:11px; font-weight:700;")
            else:
                self.cfg_conn_lbl.setText(f"✗ {msg}")
                self.cfg_conn_lbl.setStyleSheet(
                    f"color:{RED}; font-size:11px; font-weight:700;")
        self._conn_thread.result.connect(_on_result)
        self._conn_thread.start()

    def _update_rr_label(self):
        rr = self.cfg_tp.value() / self.cfg_sl.value()
        col = GREEN if rr >= 1.5 else YELLOW if rr >= 1 else RED
        self.rr_lbl.setText(f"{rr:.2f}x")
        self.rr_lbl.setStyleSheet(f"color:{col}; font-family:{MONO_CSS}; font-weight:800; font-size:14px;")

    def _update_filter_label(self):
        pass

    def _apply_safety_config(self):
        """Read safety UI widgets into SAFETY_CFG."""
        SAFETY_CFG["signal_persistence"]          = self.sf_persistence.isChecked()
        SAFETY_CFG["btc_trend_check"]             = self.sf_btc_check.isChecked()
        SAFETY_CFG["btc_drop_pct"]                = self.sf_btc_drop.value()
        SAFETY_CFG["btc_drop_cooldown_mins"]      = self.sf_btc_cooldown_mins.value()
        SAFETY_CFG["btc_recovery_pct"]            = self.sf_btc_recovery.value()
        SAFETY_CFG["trend_1h_freshness"]          = self.sf_trend_freshness.isChecked()
        SAFETY_CFG["trend_1h_stale_pct"]          = self.sf_trend_stale.value()
        SAFETY_CFG["symbol_recovery_gate"]        = self.sf_sym_recovery.isChecked()
        SAFETY_CFG["symbol_recovery_pct"]         = self.sf_sym_recovery_pct.value()
        SAFETY_CFG["symbol_recovery_expiry_mins"] = self.sf_sym_expiry.value()
        SAFETY_CFG["coin_trend_check"]            = self.sf_coin_check.isChecked()
        SAFETY_CFG["coin_drop_pct"]               = self.sf_coin_drop.value()
        SAFETY_CFG["max_open_trades"]             = self.sf_max_trades.isChecked()
        SAFETY_CFG["max_open_trades_count"]       = self.sf_max_trades_n.value()
        SAFETY_CFG["daily_loss_limit"]            = self.sf_daily_loss.isChecked()
        SAFETY_CFG["daily_loss_amount"]           = self.sf_daily_loss_n.value()

    def _apply_config(self):
        global FONT_SIZE
        CFG["max_price"]       = self.cfg_max_price.value()
        CFG["min_volume_usdt"] = self.cfg_min_vol.value()
        CFG["interval"]        = self.cfg_interval.currentText()
        CFG["top_n"]           = self.cfg_top_n.value()
        CFG["picks_n"]         = self.cfg_picks_n.value()
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

        # Browser path
        global BROWSER_PATH
        BROWSER_PATH = self.cfg_browser.text().strip()
        self._settings.setValue("browserPath", BROWSER_PATH)

        # Trading config
        TRADING_CFG["api_key"]    = self.cfg_api_key.text().strip()
        TRADING_CFG["api_secret"] = self.cfg_api_secret.text().strip()
        TRADING_CFG["oco_enabled"] = self.cfg_oco.isChecked()
        self._settings.setValue("tradingApiKey",    TRADING_CFG["api_key"])
        self._settings.setValue("tradingApiSecret", TRADING_CFG["api_secret"])
        self._settings.setValue("tradingTestnet",   TRADING_CFG["testnet"])
        self._settings.setValue("tradingOco",       TRADING_CFG["oco_enabled"])

        # Persist scan/picks counts
        self._settings.setValue("topN",   CFG["top_n"])
        self._settings.setValue("picksN", CFG["picks_n"])

        self.statusBar().showMessage(
            f"Config applied — font {FONT_SIZE}px  |  press Scan to refresh"
        )

    def _restore_settings(self):
        global FONT_SIZE, BROWSER_PATH
        s = self._settings

        # Font size — restore first so stylesheet is correct before layout
        saved_fs = s.value("fontSize")
        if saved_fs is not None:
            FONT_SIZE = int(saved_fs)
            self.cfg_font_size.setValue(FONT_SIZE)
            QApplication.instance().setStyleSheet(make_stylesheet(FONT_SIZE))

        # Browser path
        saved_bp = s.value("browserPath")
        if saved_bp is not None:
            BROWSER_PATH = str(saved_bp)
            self.cfg_browser.setText(BROWSER_PATH)

        # Safety config
        for k in SAFETY_CFG:
            val = s.value(f"safety_{k}")
            if val is not None:
                if isinstance(SAFETY_CFG[k], bool):
                    SAFETY_CFG[k] = val in (True, "true", "True", "1")
                elif isinstance(SAFETY_CFG[k], float):
                    try: SAFETY_CFG[k] = float(val)
                    except (ValueError, TypeError): pass
                elif isinstance(SAFETY_CFG[k], int):
                    try: SAFETY_CFG[k] = int(val)
                    except (ValueError, TypeError): pass
        if hasattr(self, "sf_persistence"):
            self.sf_persistence.setChecked(SAFETY_CFG["signal_persistence"])
            self.sf_btc_check.setChecked(SAFETY_CFG["btc_trend_check"])
            self.sf_btc_drop.setValue(SAFETY_CFG["btc_drop_pct"])
            self.sf_btc_cooldown_mins.setValue(SAFETY_CFG.get("btc_drop_cooldown_mins", 60))
            self.sf_btc_recovery.setValue(SAFETY_CFG.get("btc_recovery_pct", 1.5))
            self.sf_trend_freshness.setChecked(SAFETY_CFG.get("trend_1h_freshness", True))
            self.sf_trend_stale.setValue(SAFETY_CFG.get("trend_1h_stale_pct", 1.5))
            self.sf_sym_recovery.setChecked(SAFETY_CFG.get("symbol_recovery_gate", True))
            self.sf_sym_recovery_pct.setValue(SAFETY_CFG.get("symbol_recovery_pct", 1.0))
            self.sf_sym_expiry.setValue(SAFETY_CFG.get("symbol_recovery_expiry_mins", 30))
            self.sf_coin_check.setChecked(SAFETY_CFG["coin_trend_check"])
            self.sf_coin_drop.setValue(SAFETY_CFG["coin_drop_pct"])
            self.sf_max_trades.setChecked(SAFETY_CFG["max_open_trades"])
            self.sf_max_trades_n.setValue(SAFETY_CFG["max_open_trades_count"])
            self.sf_daily_loss.setChecked(SAFETY_CFG["daily_loss_limit"])
            self.sf_daily_loss_n.setValue(SAFETY_CFG["daily_loss_amount"])

        # Restore saved alert history
        try:
            import json as _json
            saved_alerts = s.value("alertHistory")
            if saved_alerts:
                alerts = _json.loads(saved_alerts)
                for alert in alerts:
                    # Re-cast numeric fields
                    for k in ("rsi", "exp", "pot", "vol", "price"):
                        if k in alert:
                            try: alert[k] = float(alert[k])
                            except: pass
                    self._alert_log.append(alert)
                    self._add_alert_row(alert, flash=False)
                self._update_history_tab_badge()
        except Exception:
            pass

        # Trading config
        tk = s.value("tradingApiKey")
        ts = s.value("tradingApiSecret")
        tt = s.value("tradingTestnet")
        to = s.value("tradingOco")
        if tk is not None:
            TRADING_CFG["api_key"] = str(tk)
            self.cfg_api_key.setText(TRADING_CFG["api_key"])
        if ts is not None:
            TRADING_CFG["api_secret"] = str(ts)
            self.cfg_api_secret.setText(TRADING_CFG["api_secret"])
        if tt is not None:
            TRADING_CFG["testnet"] = tt in (True, "true", "True", "1")
            self.cfg_testnet.setChecked(TRADING_CFG["testnet"])
            self._refresh_trading_mode_btn()
            self.cfg_live_warning.setVisible(not TRADING_CFG["testnet"])
            self._refresh_live_banner()
        if to is not None:
            TRADING_CFG["oco_enabled"] = to in (True, "true", "True", "1")
            self.cfg_oco.setChecked(TRADING_CFG["oco_enabled"])

        # Final sync — always pull from widgets into TRADING_CFG
        # This guarantees TRADING_CFG is current even if QSettings keys differ
        try:
            TRADING_CFG["api_key"]    = self.cfg_api_key.text().strip()
            TRADING_CFG["api_secret"] = self.cfg_api_secret.text().strip()
        except Exception:
            pass

        # Restore all CFG spinbox values
        def _load(key, widget, cast, cfg_key=None):
            v = s.value(key)
            if v is not None:
                try:
                    val = cast(v)
                    widget.setValue(val) if hasattr(widget, 'setValue') else widget.setCurrentText(val)
                    if cfg_key:
                        CFG[cfg_key] = val
                except Exception:
                    pass

        _load("topN",     self.cfg_top_n,     int,   "top_n")
        _load("picksN",   self.cfg_picks_n,   int,   "picks_n")
        _load("maxPrice", self.cfg_max_price, float, "max_price")
        _load("minVol",   self.cfg_min_vol,   float, "min_volume_usdt")
        _load("candles",  self.cfg_candles,   int,   "candle_limit")
        _load("slPct",    self.cfg_sl,        float, "sl_pct")
        _load("tpPct",    self.cfg_tp,        float, "tp_pct")
        _load("tp2Pct",   self.cfg_tp2,       float, "tp2_pct")

        iv = s.value("interval")
        if iv is not None:
            self.cfg_interval.setCurrentText(str(iv))
            CFG["interval"] = str(iv)

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
            if hasattr(self, "al_max_rsi"):
                self.al_max_rsi.setValue(int(ALERT_CFG.get("max_rsi", 70)))
                self.al_max_bb.setValue(int(ALERT_CFG.get("max_bb_pct", 80)))
                self.al_vol_spike.setChecked(ALERT_CFG.get("require_vol_spike", False))
                self.al_min_adr.setValue(float(ALERT_CFG.get("min_adr_pct", 0.5)))
                self.al_block_downtrend.setChecked(ALERT_CFG.get("block_downtrend", True))
                self.al_min_vol_ratio.setValue(float(ALERT_CFG.get("min_vol_ratio", 0.8)))
                self.al_spike_cooldown.setChecked(ALERT_CFG.get("spike_cooldown", True))
                self.al_spike_cooldown_pct.setValue(float(ALERT_CFG.get("spike_pct", 15.0)))
                self.al_require_macd.setChecked(ALERT_CFG.get("require_macd_rising", False))
                self.al_coin_cooldown.setChecked(ALERT_CFG.get("coin_cooldown", True))
                self.al_coin_cooldown_mins.setValue(int(ALERT_CFG.get("coin_cooldown_mins", 30)))
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
        s.setValue("browserPath", BROWSER_PATH)
        # Trading config
        s.setValue("tradingApiKey",    TRADING_CFG["api_key"])
        s.setValue("tradingApiSecret", TRADING_CFG["api_secret"])
        s.setValue("tradingTestnet",   TRADING_CFG["testnet"])
        s.setValue("tradingOco",       TRADING_CFG["oco_enabled"])
        # Safety config
        try:
            self._apply_safety_config()
        except Exception:
            pass
        for k, v in SAFETY_CFG.items():
            s.setValue(f"safety_{k}", v)
        # Save all CFG spinbox values directly from widgets (no Apply click needed)
        try:
            s.setValue("topN",    self.cfg_top_n.value())
            s.setValue("picksN",  self.cfg_picks_n.value())
            s.setValue("maxPrice",  self.cfg_max_price.value())
            s.setValue("minVol",    self.cfg_min_vol.value())
            s.setValue("interval",  self.cfg_interval.currentText())
            s.setValue("candles",   self.cfg_candles.value())
            s.setValue("slPct",     self.cfg_sl.value())
            s.setValue("tpPct",     self.cfg_tp.value())
            s.setValue("tp2Pct",    self.cfg_tp2.value())
        except Exception:
            pass
        # Save last 20 alert log entries
        try:
            import json as _json
            alerts_to_save = self._alert_log[-20:]
            # Convert any non-serializable values
            safe_alerts = []
            for a in alerts_to_save:
                safe = {}
                for k, v in a.items():
                    safe[k] = str(v) if not isinstance(v, (str, int, float, bool)) else v
                safe_alerts.append(safe)
            s.setValue("alertHistory", _json.dumps(safe_alerts))
        except Exception:
            pass
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
        self._trades_refresh_timer.stop()
        self._dot_blink_timer.stop()
        _outcome_tracker.stop()
        if self._ws_feed:
            self._ws_feed.stop()
        self._save_settings()
        super().closeEvent(event)

    def _show_status(self, msg, timeout_ms=10000):
        """Show status bar message — auto-clears after timeout (default 10s)."""
        self.statusBar().showMessage(msg, timeout_ms)

    def _setup_timer(self):
        self._progress_timer = QTimer()
        self._progress_timer.timeout.connect(self._poll_progress)

        # Trades tab live price refresh — fires every 3s always
        self._trades_refresh_timer = QTimer()
        self._trades_refresh_timer.setInterval(3000)
        self._trades_refresh_timer.timeout.connect(self._fetch_open_trade_prices)

        # Blink timer for scanning dot
        self._dot_blink_state = True
        self._dot_blink_timer = QTimer()
        self._dot_blink_timer.setInterval(500)
        self._dot_blink_timer.timeout.connect(self._blink_dot)

    def _blink_dot(self):
        """Alternate dot color while scanning."""
        self._dot_blink_state = not self._dot_blink_state
        color = "#00aaff" if self._dot_blink_state else "#004488"
        self._scan_dot.setStyleSheet(f"color: {color}; font-size: 14px;")

    def _set_dot_scanning(self):
        self._dot_blink_timer.start()
        self._scan_dot.setToolTip("Scanning…")

    def _set_dot_idle(self, coin_count=None):
        self._dot_blink_timer.stop()
        self._scan_dot.setStyleSheet("color: #00cc66; font-size: 14px;")
        tip = f"Last scan: {coin_count} coins" if coin_count else "Scanner idle"
        self._scan_dot.setToolTip(tip)

    def _start_scan(self):
        if self._worker and self._worker.isRunning():
            return
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("⏳")
        self._set_dot_scanning()
        self.table.setRowCount(0)
        self.statusBar().showMessage("Fetching tickers...")

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
        self.statusBar().showMessage(status[:80])

    def _poll_progress(self):
        pass

    def _on_finished(self, results):
        self._results = results
        self._refresh_display()
        self._populate_picks(results)
        self._check_sltp_hits(results)     # auto-close SL/TP hit trades
        self._refresh_trades_table()       # update unrealised P&L
        self._refresh_balance_display()
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("⚡")
        self._set_dot_idle(len(results))
        # Log all signals for audit
        threading.Thread(
            target=log_scan_results,
            args=(results,),
            kwargs={"trades": self._trades},
            daemon=True
        ).start()
     
        n = len(results)
        self.statusBar().showMessage(f"Done — {n} coins  [{datetime.now().strftime('%H:%M:%S')}]")
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
        self.scan_btn.setText("⚡")
        self.progress.setVisible(False)
        self.statusBar().showMessage(f"Error: {msg[:60]}")
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
        9:  lambda r, i: {"PRE-BREAKOUT":0,"STRONG BUY":1,"BUY":2,"NEUTRAL":3,"SELL":4,"STRONG SELL":5,                  # Signal tier
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
        try:
            self._do_populate_table(results)
        except Exception as e:
            import traceback; traceback.print_exc()

    def _do_populate_table(self, results):
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

            sig_tier = {"PRE-BREAKOUT":0,"STRONG BUY":1,"BUY":2,"NEUTRAL":3,"SELL":4,"STRONG SELL":5}.get(sig, 3)
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

        n      = CFG.get("picks_n", 5)
        pre_bo = [r for r in results if r["signal"] == "PRE-BREAKOUT"][:n]
        buys   = [r for r in results if r["signal"] in ("STRONG BUY", "BUY")][:n]
        sells  = [r for r in results if r["signal"] in ("STRONG SELL", "SELL")][:n]

        for section_name, section_results, color, is_long in [
            ("⚡  PRE-BREAKOUT",    pre_bo, "#ff9900", True),
            ("🟢  LONG CANDIDATES", buys,   GREEN,     True),
            ("🔴  SHORT CANDIDATES",sells,  RED,       False),
        ]:
            if not section_results:
                continue
            lbl = QLabel(section_name)
            lbl.setStyleSheet(f"color:{color}; font-size:15px; font-weight:800; padding:8px 0 4px 0;")
            self.picks_lay.addWidget(lbl)

            for r in section_results:
                self.picks_lay.addWidget(self._build_pick_card(r, is_long))

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
                self_.setMinimumHeight(160)
                self_.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            def paintEvent(self_, event):
                p = QPainter(self_)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                W, H = self_.width(), self_.height()

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

                # 24h% right-aligned — row 1, measure first so row 2 knows safe right edge
                chg_col = GREEN if chg24 >= 0 else RED
                p.setFont(f_med)
                chg_w = p.fontMetrics().horizontalAdvance(chg_str) + 6
                chg_x = W - chg_w - 8
                p.setPen(QColor(chg_col))
                p.drawText(chg_x, ry, chg_w, ROW_H,
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, chg_str)

                # ════════════════════════════════════════════
                # ROW 2  — Score bar | RSI bar | BB bar | Age (right)
                # ════════════════════════════════════════════
                y2 = ry + ROW_H + 4
                bar_h = 6
                bar_r = 3

                # Score bar (0–10 scale)
                score_w = 90
                txt(L, y2, "Score", DIM, 8)
                bx = L + 38
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(bx, y2+2, score_w, bar_h, bar_r, bar_r)
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

                # Right side of Row 2: Age stacked above Conf — anchored to right edge
                # measure both
                f_sm = QFont(); f_sm.setPointSize(8); p.setFont(f_sm)
                fm_sm = p.fontMetrics()
                age_label  = f"Age: {age_str}"
                conf_label = f"Conf: {conf_bar}"
                aw = fm_sm.horizontalAdvance(age_label)
                cw = fm_sm.horizontalAdvance(conf_label)
                right_col_w = max(aw, cw) + 4
                rx2 = W - right_col_w - 8

                # Age on top line of row 2
                p.setPen(QColor(age_col))
                p.drawText(rx2, y2, right_col_w, 14,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, age_label)
                # Conf below age
                p.setPen(QColor(conf_col))
                p.drawText(rx2, y2 + 14, right_col_w, 14,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, conf_label)

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

                # 1H and vol right of pills — drawn BELOW pills in row 4, not overlapping
                # (removed from row 3)

                # ════════════════════════════════════════════
                # ROW 4  — MACD | StRSI | 1H trend | Vol bar
                # ════════════════════════════════════════════
                y4 = y3 + pill_h + 6
                macd_str  = f"MACD {'▲ Positive' if macd_h > 0 else '▼ Negative'}  ({macd_h:+.4f})"
                macd_col  = GREEN if macd_h > 0 else RED
                strsi_str = f"StRSI {strsi:.0f}"
                strsi_col = GREEN if strsi < 30 else (RED if strsi > 70 else YELLOW)

                txt(L, y4, macd_str,  macd_col,  9, bold=True)
                mx = L + txt_w(macd_str, 9, bold=True) + 14
                txt(mx, y4, strsi_str, strsi_col, 9, bold=True)

                # 1H trend | Vol bar — right side of row 4, no overlap
                # 1H trend label
                trend_label = f"1H {trend_sym}  Vol"
                tl_w = txt_w(trend_label, 9) + 4

                vol_bar_w = 50
                gap = 6
                # total right block width: trend_label + gap + vol_bar + gap + vol_value
                f_sm2 = QFont(); f_sm2.setPointSize(8); p.setFont(f_sm2)
                vol_val_str = f"{vol_r:.1f}x"
                vvw = p.fontMetrics().horizontalAdvance(vol_val_str) + 4

                right_block_x = W - tl_w - gap - vol_bar_w - gap - vvw - 8

                # 1H trend
                p.setFont(QFont())
                txt(right_block_x, y4, f"1H {trend_sym}", trend_col, 9)
                tl_actual = txt_w(f"1H {trend_sym}", 9) + 6
                txt(right_block_x + tl_actual, y4, "Vol", DIM, 9)
                vol_lbl_w = txt_w("Vol", 9) + 4

                vbx = right_block_x + tl_actual + vol_lbl_w
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(vbx, y4+3, vol_bar_w, bar_h, bar_r, bar_r)
                vol_fill = min(int((vol_r / 5) * vol_bar_w), vol_bar_w)
                vol_col = "#00ff88" if vol_r >= 2 else (YELLOW if vol_r >= 1.2 else DIM)
                p.setBrush(QBrush(QColor(vol_col)))
                p.drawRoundedRect(vbx, y4+3, max(vol_fill, 3), bar_h, bar_r, bar_r)
                txt(vbx + vol_bar_w + 3, y4, vol_val_str, vol_col, 8, bold=True)

            def sizeHint(self_):
                from PyQt6.QtCore import QSize
                return QSize(500, 160)

            def minimumSizeHint(self_):
                from PyQt6.QtCore import QSize
                return QSize(400, 160)

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

        detail_panel = DetailPanel()
        detail_panel.load(r)
        main_lay.addWidget(detail_panel)

        hint = QLabel("Right-click row in Scanner to open a trade  |  Click outside or Esc to close")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color:{DIM}; font-size:10px; padding:6px; background:transparent;")
        main_lay.addWidget(hint)

        mw = self.geometry()
        w  = max(700, int(mw.width()  * 0.55))
        h  = max(600, int(mw.height() * 0.80))
        dlg.setFixedSize(w, h)
        dlg.move(
            mw.x() + (mw.width()  - w) // 2,
            mw.y() + (mw.height() - h) // 2
        )

        QShortcut(QKeySequence("Escape"), dlg).activated.connect(dlg.accept)

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

    def _update_signal_log_size(self):
        """Update signal log size label — shows today's row count and total size."""
        if not hasattr(self, '_signal_log_size_lbl'):
            return
        import glob
        log_dir  = APP_LOGS_DIR
        log_path = _get_signal_log_path()
        try:
            # Today's rows
            today_rows = 0
            if os.path.exists(log_path):
                with open(log_path) as f:
                    today_rows = max(0, sum(1 for _ in f) - 1)
            # Total size of all daily files
            all_files = glob.glob(os.path.join(log_dir, "signal_log_*.csv"))
            total_kb  = sum(os.path.getsize(f) for f in all_files) // 1024
            n_files   = len(all_files)
            if n_files:
                self._signal_log_size_lbl.setText(
                    f"Today: {today_rows:,} rows  |  {n_files} files  {total_kb}KB total")
            else:
                self._signal_log_size_lbl.setText("No log yet")
        except Exception:
            self._signal_log_size_lbl.setText("No log yet")

    def _show_outcome_analysis(self):
        """Read all signal logs and show outcome statistics in a dialog."""
        import glob, csv as _csv
        log_dir   = APP_LOGS_DIR
        all_files = sorted(glob.glob(os.path.join(log_dir, "signal_log_*.csv")))

        if not all_files:
            self._show_status("No signal logs found — run scans first")
            return

        wins = losses = flats = pending = total_alerted = 0
        by_symbol = {}
        pct_moves = []

        for fpath in all_files:
            try:
                with open(fpath, newline="") as f:
                    for row in _csv.DictReader(f):
                        if row.get("alert_fired") != "True":
                            continue
                        total_alerted += 1
                        sym     = row.get("symbol", "").replace("USDT","")
                        outcome = row.get("outcome", "")
                        pct_1h  = row.get("pct_1h", "")

                        if sym not in by_symbol:
                            by_symbol[sym] = {"W": 0, "L": 0, "F": 0, "P": 0}

                        if outcome == "WIN":
                            wins += 1; by_symbol[sym]["W"] += 1
                        elif outcome == "LOSS":
                            losses += 1; by_symbol[sym]["L"] += 1
                        elif outcome == "FLAT":
                            flats += 1; by_symbol[sym]["F"] += 1
                        else:
                            pending += 1; by_symbol[sym]["P"] += 1

                        if pct_1h not in ("", None):
                            try: pct_moves.append(float(pct_1h))
                            except: pass
            except Exception:
                pass

        resolved = wins + losses + flats
        win_rate = wins / resolved * 100 if resolved > 0 else 0
        avg_move = sum(pct_moves) / len(pct_moves) if pct_moves else 0

        report_lines = [
            f"OUTCOME ANALYSIS  —  {len(all_files)} day(s) of data",
            "",
            f"Total alerted signals  : {total_alerted}",
            f"Resolved (1h outcome)  : {resolved}",
            f"Pending  (< 1h old)    : {pending}",
            "",
            f"WIN   (>= +3% in 1h)  : {wins}  ({win_rate:.1f}%)",
        ]
        if resolved > 0:
            report_lines += [
                f"LOSS  (<= -2% in 1h)  : {losses}  ({losses/resolved*100:.1f}%)",
                f"FLAT  (between)        : {flats}  ({flats/resolved*100:.1f}%)",
            ]
        report_lines += [
            f"Avg 1h price move      : {avg_move:+.2f}%",
            "",
            "-" * 45,
            "BY SYMBOL  (W / L / F / Pending):",
        ]
        for sym, c in sorted(by_symbol.items(),
                              key=lambda x: x[1]["W"], reverse=True):
            res = c["W"] + c["L"] + c["F"]
            wr  = c["W"] / res * 100 if res > 0 else 0
            report_lines.append(
                f"  {sym:10} W:{c['W']} L:{c['L']} F:{c['F']} P:{c['P']}  WR:{wr:.0f}%"
            )

        if resolved == 0:
            report_lines += [
                "",
                "No outcomes yet — outcome tracking needs",
                "at least 1 hour after alerts fire.",
                "Keep the app running and check back later.",
            ]

        dlg = QDialog(self)
        dlg.setWindowTitle("Outcome Analysis")
        dlg.setMinimumSize(520, 460)
        dlg.setStyleSheet(f"background:{DARK}; color:{WHITE};")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 16, 20, 16)

        title_lbl = QLabel("📊  Alert Outcome Analysis")
        title_lbl.setStyleSheet(
            f"color:{ACCENT}; font-size:16px; font-weight:800; margin-bottom:8px;")
        lay.addWidget(title_lbl)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("JetBrains Mono,DejaVu Sans Mono,Monospace", 11))
        txt.setStyleSheet(
            f"background:{CARD}; color:{WHITE}; border:1px solid {BORDER}; "
            f"border-radius:6px; padding:12px;")
        txt.setPlainText("\n".join(report_lines))
        lay.addWidget(txt)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        close_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; padding:6px 20px;")
        lay.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.exec()

    def _open_signal_log(self):
        """Open today's signal log CSV in default application."""
        log_path = _get_signal_log_path()
        if os.path.exists(log_path):
            open_url(f"file://{log_path}")
        else:
            self._show_status("No signal log yet — run a scan first")

    def _clear_signal_log(self):
        """Delete all signal log files."""
        import glob
        log_dir   = APP_LOGS_DIR
        all_files = glob.glob(os.path.join(log_dir, "signal_log_*.csv"))
        if not all_files:
            self._show_status("No signal logs to clear")
            return
        try:
            for f in all_files:
                os.remove(f)
            self._update_signal_log_size()
            self._show_status(f"Cleared {len(all_files)} signal log file(s)")
        except Exception as e:
            self._show_status(f"Could not clear logs: {e}")

    def _export(self):
        if not self._results:
            self.statusBar().showMessage("Nothing to export — run a scan first")
            return
        fname = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        fpath = os.path.join(APP_LOGS_DIR, fname)
        os.makedirs(APP_LOGS_DIR, exist_ok=True)
        clean = [{k: v for k, v in r.items() if k not in ("sig_clr","candles")}
                 for r in self._results]
        with open(fpath, "w") as f:
            def _json_serial(obj):
                if hasattr(obj, 'isoformat'):
                    return obj.isoformat()
                if hasattr(obj, '__str__'):
                    return str(obj)
                raise TypeError(f"Not serializable: {type(obj)}")
            json.dump(clean, f, indent=2, default=_json_serial)
        self._show_status(f"Exported → {fpath}")
        if hasattr(self, 'cfg_export_lbl'):
            self.cfg_export_lbl.setText(f"Saved: {fname}")
            self.cfg_export_lbl.setStyleSheet(f"color:{GREEN}; font-size:11px;")

# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Catch any unhandled exception and log it to file."""
    import traceback, datetime
    log_path = os.path.join(APP_LOGS_DIR, "crash.log")
    try:
        with open(log_path, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"CRASH: {datetime.datetime.now()}\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        print(f"[CRASH LOGGED] → {log_path}")
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    # Create config/logs directory on first launch
    os.makedirs(APP_LOGS_DIR, exist_ok=True)

    # Suppress Qt stderr noise about missing dbus portal and unknown CSS properties.
    # These are harmless Qt/desktop-environment warnings, not Python errors.
    os.environ.setdefault("QT_LOGGING_RULES",
        "qt.qpa.theme=false;qt.qpa.theme.gnome=false")

    sys.excepthook = _global_exception_handler
    app = QApplication(sys.argv)
    app.setApplicationName("Crypto Scalper Scanner")
    app.setStyleSheet(make_stylesheet(FONT_SIZE))

    # Set app icon early so it shows in taskbar, dock and alt-tab
    import os as _os
    for _p in [
        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "app_icon.png"),
        _os.path.join(_os.getcwd(), "app_icon.png"),
        _os.path.join(_os.path.dirname(_os.path.abspath(sys.argv[0])), "app_icon.png"),
    ]:
        if _os.path.exists(_p):
            app.setWindowIcon(QIcon(_p))
            break

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
