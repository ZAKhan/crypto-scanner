import csv
import json
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta

import requests
from PyQt6.QtCore import QObject, pyqtSignal

from cs.config import CFG
from cs.logger import ALERT_CFG, _get_signal_log_path
from cs.safety import (
    _spike_cooldown_tracker,
    _crash_cooldown_tracker,
    _coin_alert_tracker,
)
from cs.scanner import Scanner
from cs.sounds import _SOUNDS, _play_wav


class OutcomeTracker:
    """
    Tracks price outcomes for alerted signals.
    Queues price checks at 30min, 1h, 4h after each alert.
    Updates the signal log CSV in-place with results.
    """
    def __init__(self):
        self._queue   = []
        self._lock    = threading.Lock()
        self._thread  = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def schedule(self, symbol: str, entry_price: float,
                 alert_timestamp: str, log_path: str):
        now = datetime.now()
        with self._lock:
            for minutes, col in [(30, "price_30m"), (60, "price_1h"), (240, "price_4h")]:
                self._queue.append({
                    "check_at":        now + timedelta(minutes=minutes),
                    "symbol":          symbol,
                    "entry_price":     entry_price,
                    "log_path":        log_path,
                    "alert_timestamp": alert_timestamp,
                    "price_col":       col,
                    "pct_col":         col.replace("price_", "pct_"),
                })

    def _fetch_price(self, symbol: str) -> float:
        try:
            r = requests.get(
                CFG["base_url"] + "/api/v3/ticker/price",
                params={"symbol": symbol}, timeout=5
            ).json()
            return float(r.get("price", 0))
        except Exception:
            return 0.0

    def _update_csv(self, log_path: str, alert_timestamp: str, symbol: str,
                    price_col: str, pct_col: str, price: float, pct: float):
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
                        if row.get("pct_1h") not in ("", None):
                            p = float(row["pct_1h"])
                            if   p >= 3.0:  row["outcome"] = "WIN"
                            elif p <= -2.0: row["outcome"] = "LOSS"
                            else:           row["outcome"] = "FLAT"
                    rows.append(row)
            tmp = log_path + ".tmp"
            with open(tmp, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(tmp, log_path)
        except Exception:
            pass

    def _run(self):
        while self._running:
            now = datetime.now()
            due = []
            with self._lock:
                remaining = [item for item in self._queue if now < item["check_at"]]
                due       = [item for item in self._queue if now >= item["check_at"]]
                self._queue = remaining

            for item in due:
                price = self._fetch_price(item["symbol"])
                if price > 0 and item["entry_price"] > 0:
                    pct = (price - item["entry_price"]) / item["entry_price"] * 100
                    self._update_csv(
                        item["log_path"], item["alert_timestamp"], item["symbol"],
                        item["price_col"], item["pct_col"], price, pct
                    )
            time.sleep(30)

_outcome_tracker = OutcomeTracker()


class AlertEngine(QObject):
    """
    Background alert engine.
    - Auto-scans on a timer
    - Compares new results against last scan
    - Fires desktop / sound / telegram for NEW signals only
    """
    new_alert    = pyqtSignal(dict)
    scan_done    = pyqtSignal(list)
    scan_started = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._scanner      = Scanner()
        self._last_signals = {}   # symbol -> signal string from last scan
        self._signal_age   = {}   # symbol -> datetime when current signal first appeared
        self._signal_conf  = {}   # symbol -> consecutive scan count holding same signal
        self._thread       = None
        self._running      = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def trigger_now(self):
        threading.Thread(target=self._run_scan, daemon=True).start()

    def _loop(self):
        while self._running:
            self._run_scan()
            for _ in range(ALERT_CFG["interval_sec"] * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _run_scan(self):
        if self._scanner.scanning:
            return
        self.scan_started.emit()
        self._scanner.start_scan()
        while self._scanner.scanning:
            time.sleep(0.2)
        results = self._scanner.get_results()
        if results:
            self._check_alerts(results)
            self.scan_done.emit(results)

    def _check_alerts(self, results):
        now = datetime.now()

        # ── Pre-populate crash/spike trackers from candle data ──────
        # These are in-memory dicts that reset on every app restart.
        # On the first scan after startup, backfill them so cooldowns
        # are respected even for coins that crashed before the app opened.
        _crash_thresh = ALERT_CFG.get("crash_pct", 8.0)
        _cum_thresh   = ALERT_CFG.get("crash_cumulative_pct", 6.0)
        _cum_candles  = ALERT_CFG.get("crash_cumulative_candles", 10)
        _spike_thresh = ALERT_CFG.get("spike_pct", 15.0)
        for r in results:
            sym     = r["symbol"]
            candles = r.get("candles", [])
            if not candles:
                continue
            # Only backfill if tracker is empty for this symbol
            # (don't overwrite a cooldown that was set this session)
            if sym not in _crash_cooldown_tracker:
                triggered = False
                # Single-candle crash in last 3 candles
                for _c in candles[-3:]:
                    if _c["open"] > 0 and (_c["open"] - _c["close"]) / _c["open"] * 100 >= _crash_thresh:
                        triggered = True
                        break
                # Cumulative drop from peak in last N candles
                if not triggered and len(candles) >= _cum_candles:
                    _w = candles[-_cum_candles:]
                    _peak = max(c["high"] for c in _w)
                    if _peak > 0 and (_peak - candles[-1]["close"]) / _peak * 100 >= _cum_thresh:
                        triggered = True
                if triggered:
                    _crash_cooldown_tracker[sym] = now
            if sym not in _spike_cooldown_tracker:
                if r.get("change", 0) >= _spike_thresh:
                    _spike_cooldown_tracker[sym] = now

        for r in results:
            sym  = r["symbol"]
            sig  = r["signal"]
            prev = self._last_signals.get(sym, "NEUTRAL")

            if sig == "NEUTRAL":
                self._signal_age.pop(sym, None)
                self._signal_conf[sym] = 0
            elif sig != prev:
                self._signal_age[sym]  = now
                self._signal_conf[sym] = 1
            else:
                if sym not in self._signal_age:
                    self._signal_age[sym] = now
                self._signal_conf[sym] = self._signal_conf.get(sym, 0) + 1

            r["signal_conf"] = self._signal_conf.get(sym, 1)
            r["signal_age"]  = self._signal_age.get(sym, now)

        if not ALERT_CFG["enabled"]:
            for r in results:
                self._last_signals[r["symbol"]] = r["signal"]
            return

        sig_order = {"PRE-BREAKOUT": 0, "STRONG BUY": 1, "STRONG SELL": 2,
                     "BUY": 3, "SELL": 4, "NEUTRAL": 5}
        min_level = sig_order.get(ALERT_CFG["min_signal"], 2)

        for r in results:
            sym   = r["symbol"]
            sig   = r["signal"]
            prev  = self._last_signals.get(sym, "NEUTRAL")
            level = sig_order.get(sig, 4)
            pot   = r.get("potential", 0)
            exp   = r.get("expected_move", 0)
            conf  = r.get("signal_conf", 1)

            is_new = (sig != "NEUTRAL" and
                      (prev == "NEUTRAL" or
                       sig_order.get(prev, 4) > level or
                       conf == 2))

            _now_ts = datetime.now()

            # Fix 3 — spike cooldown
            _spike_ok = True
            if ALERT_CFG.get("spike_cooldown") and "BUY" in sig:
                _last_spike = _spike_cooldown_tracker.get(sym)
                if _last_spike and (_now_ts - _last_spike).seconds < 7200:
                    _spike_ok = False
                if r.get("change", 0) >= ALERT_CFG.get("spike_pct", 15.0):
                    _spike_cooldown_tracker[sym] = _now_ts

            # Crash cooldown
            _crash_ok = True
            if ALERT_CFG.get("crash_cooldown", True) and "BUY" in sig:
                _crash_mins   = ALERT_CFG.get("crash_cooldown_mins", 60)
                _crash_thresh = ALERT_CFG.get("crash_pct", 8.0)
                _cum_thresh   = ALERT_CFG.get("crash_cumulative_pct", 6.0)
                _cum_candles  = ALERT_CFG.get("crash_cumulative_candles", 10)
                _last_crash   = _crash_cooldown_tracker.get(sym)
                if _last_crash and (_now_ts - _last_crash).total_seconds() < _crash_mins * 60:
                    _crash_ok = False
                candles = r.get("candles", [])
                if _crash_ok and candles and len(candles) >= 2:
                    # Single-candle crash: one candle dropped > crash_pct
                    for _c in candles[-3:]:
                        if _c["open"] > 0:
                            if (_c["open"] - _c["close"]) / _c["open"] * 100 >= _crash_thresh:
                                _crash_cooldown_tracker[sym] = _now_ts
                                _crash_ok = False
                                break
                if _crash_ok and candles and len(candles) >= _cum_candles:
                    # Cumulative crash: peak-to-trough drop over last N candles
                    # catches multi-candle dumps like STO's spread-out sell-off
                    _window     = candles[-_cum_candles:]
                    _peak_high  = max(c["high"] for c in _window)
                    _last_close = candles[-1]["close"]
                    if _peak_high > 0:
                        _cum_drop = (_peak_high - _last_close) / _peak_high * 100
                        if _cum_drop >= _cum_thresh:
                            _crash_cooldown_tracker[sym] = _now_ts
                            _crash_ok = False

            # Per-coin duplicate suppression — three-layer rule:
            # Layer 1 (time): base cooldown — hard block for first N minutes
            # Layer 2 (signal upgrade): allow early re-alert only if signal
            #          strengthened (e.g. BUY → STRONG BUY)
            # Layer 3 (price move): early re-alert also requires price to have
            #          moved >1.5% from the last alert price (avoids same-candle re-fires)
            _cooldown_ok = True
            if ALERT_CFG.get("coin_cooldown"):
                _last = _coin_alert_tracker.get(sym)
                if _last:
                    _cooldown_mins  = ALERT_CFG.get("coin_cooldown_mins", 60)
                    _elapsed        = (_now_ts - _last["time"]).total_seconds() / 60
                    _within_window  = _elapsed < _cooldown_mins

                    if _within_window:
                        # Within base cooldown — only pass through on upgrade + price move
                        sig_order_local = {"PRE-BREAKOUT": 0, "STRONG BUY": 1,
                                           "STRONG SELL": 2, "BUY": 3, "SELL": 4, "NEUTRAL": 5}
                        _prev_level = sig_order_local.get(_last["signal"], 5)
                        _cur_level  = sig_order_local.get(sig, 5)
                        _upgraded   = _cur_level < _prev_level   # lower index = stronger

                        _last_price  = _last["price"]
                        _cur_price   = r.get("price", 0)
                        _price_moved = (abs(_cur_price - _last_price) / _last_price * 100
                                        if _last_price > 0 else 0) >= 1.5

                        if not (_upgraded and _price_moved):
                            _cooldown_ok = False

            # 1h downtrend block
            _1h_ok = not (ALERT_CFG.get("block_1h_downtrend", True) and
                          "BUY" in sig and r.get("trend_1h") == "down")

            # Squeeze exemption — ONLY bypasses min_exp_move, NOT vol_ratio.
            # A true squeeze has suppressed ATR so exp_move is artificially
            # low — that's valid to exempt. But zero volume is zero volume.
            _bb_width = r.get("bb_width_pct", 99)
            _squeeze_exemption = (
                "BUY" in sig and
                _bb_width < ALERT_CFG.get("squeeze_exempt_bb_width", 2.0) and
                r.get("trend_1h") in ("up", "flat") and
                r.get("vol_ratio", 0) >= 1.0   # minimum floor even in squeeze
            )

            # Pattern quality gate
            _pattern = r.get("pattern", "")
            _pattern_ok = True
            if ALERT_CFG.get("block_doji", True) and "Doji" in _pattern:
                _pattern_ok = False
            if ALERT_CFG.get("block_neutral_pattern", True) and _pattern == "Neutral":
                _pattern_ok = False
            # Always block explicitly bearish patterns regardless of config
            if any(p in _pattern for p in ("Vol Spike ↓", "Shooting Star", "Bearish Engulf")):
                _pattern_ok = False

            passes = (
                level <= min_level and
                pot   >= ALERT_CFG["min_potential"] and
                (exp  >= ALERT_CFG["min_exp_move"] or _squeeze_exemption) and
                r.get("rsi", 50) <= ALERT_CFG["max_rsi"] and
                r.get("bb_pos", 50) <= ALERT_CFG["max_bb_pct"] and
                r.get("adr_pct", 0) >= ALERT_CFG["min_adr_pct"] and
                (r.get("vol_ratio", 0) >= ALERT_CFG.get("min_vol_ratio", 1.0) or
                 sig == "PRE-BREAKOUT") and  # PRE-BREAKOUT fires before volume arrives
                _pattern_ok and
                (not ALERT_CFG.get("block_downtrend") or
                 not any(p in _pattern for p in ("Downtrend", "Rejection"))) and
                (not ALERT_CFG.get("require_macd_rising") or r.get("macd_rising", False)) and
                (not ALERT_CFG["require_vol_spike"] or r.get("vol_spike", False)) and
                _spike_ok and _cooldown_ok and _1h_ok and _crash_ok
            )

            if is_new and passes:
                _coin_alert_tracker[sym] = {
                    "time":   _now_ts,
                    "price":  r.get("price", 0),
                    "signal": sig,
                }
                _outcome_tracker.schedule(
                    symbol=sym,
                    entry_price=r.get("price", 0),
                    alert_timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
                    log_path=_get_signal_log_path()
                )
                alert = {
                    "time":        now.strftime("%H:%M:%S"),
                    "symbol":      sym.replace("USDT", ""),
                    "signal":      sig,
                    "price":       r["price"],
                    "rsi":         r["rsi"],
                    "exp":         exp,
                    "pot":         pot,
                    "pattern":     r.get("pattern", "—"),
                    "vol":         r.get("vol_ratio", 0),
                    "macd_rising": r.get("macd_rising", False),
                }
                self.new_alert.emit(alert)
                self._fire(alert)

        for r in results:
            self._last_signals[r["symbol"]] = r["signal"]

    def _fire(self, a):
        sig = a["signal"]
        sym = a["symbol"]
        msg = (f"{sig}: {sym}  ${a['price']:.5f}\n"
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
        if "STRONG" in signal and "BUY" in signal:   key = "STRONG BUY"
        elif "STRONG" in signal:                      key = "STRONG SELL"
        elif "BUY" in signal:                         key = "BUY"
        else:                                         key = "SELL"
        path = _SOUNDS.get(key)
        if path and os.path.exists(path):
            _play_wav(path)

    def _desktop_notify(self, signal, symbol, body):
        try:
            is_buy    = "BUY" in signal
            is_strong = "STRONG" in signal
            title = f"🚀 {signal} — {symbol}" if is_buy else f"🔴 {signal} — {symbol}"
            subprocess.Popen(
                ["notify-send",
                 "-i", "dialog-information" if is_buy else "dialog-warning",
                 "-u", "critical" if is_strong else "normal",
                 "-t", "0" if is_strong else "12000",
                 title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            pass

    def _telegram(self, signal, symbol, a):
        try:
            emoji    = "🟢" if "BUY" in signal else "🔴"
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
        try:
            emoji   = "🟢" if "BUY" in signal else "🔴"
            strong  = "STRONG" in signal
            macd_ok = (a.get("macd_rising", False) and "BUY" in signal) or \
                      (not a.get("macd_rising", True) and "SELL" in signal)
            text = "\n".join([
                f"{emoji} *{'🚨 ' if strong else ''}{signal}* — {symbol}",
                f"━━━━━━━━━━━━━━━",
                f"💰 Price:    ${a['price']:.6f}",
                f"📊 RSI:      {a['rsi']:.1f}",
                f"📈 Exp Move: {a['exp']:.1f}%",
                f"⭐ Potential: {a['pot']}%",
                f"📦 Volume:   {a['vol']:.1f}x avg",
                f"🕯 Pattern:  {a['pattern']}",
                f"⚡ MACD:    {'Fresh ✅' if macd_ok else 'Stale ⚠️'}",
                f"🕐 {a['time']}  |  Binance Spot",
            ])

            queue_path = ALERT_CFG["picoclaw_queue"]
            os.makedirs(os.path.dirname(queue_path), exist_ok=True)
            queue = []
            if os.path.exists(queue_path):
                try:
                    with open(queue_path) as f:
                        q = json.load(f)
                    queue = q if isinstance(q, list) else []
                except Exception:
                    queue = []
            queue.append({"to": ALERT_CFG["wa_number"], "text": text,
                           "sent": False, "ts": datetime.now().isoformat()})
            queue = queue[-50:]
            with open(queue_path, "w") as f:
                json.dump(queue, f, indent=2)
        except Exception:
            pass
