import json
import os
import subprocess
import threading
import time
from datetime import datetime

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

            # Only alert on NEW signals (wasn't a signal before, or upgraded),
            # OR when conf just reached 2 (signal persistence just satisfied).
            # Without this second condition, is_new flips to False on scan 2
            # — the exact scan where persistence clears — so the alert never fires.
            conf    = r.get("signal_conf", 1)
            is_new = (sig != "NEUTRAL" and
                      (prev == "NEUTRAL" or
                       sig_order.get(prev, 4) > level or
                       conf == 2))  # just satisfied persistence — fire now
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

            # v2.4.5 — crash cooldown: block BUY if a single candle crashed recently
            _crash_ok = True
            if ALERT_CFG.get("crash_cooldown", True) and "BUY" in sig:
                _crash_mins   = ALERT_CFG.get("crash_cooldown_mins", 60)
                _crash_thresh = ALERT_CFG.get("crash_pct", 8.0)
                _last_crash   = _crash_cooldown_tracker.get(sym)
                if _last_crash and (_now_ts - _last_crash).seconds < _crash_mins * 60:
                    _crash_ok = False  # still in crash cooldown
                # Detect crash candle — check if any of last 3 candles dropped > threshold
                candles = r.get("candles", [])
                if candles and len(candles) >= 2:
                    for _c in candles[-3:]:
                        if _c["open"] > 0:
                            _candle_drop = (_c["open"] - _c["close"]) / _c["open"] * 100
                            if _candle_drop >= _crash_thresh:
                                _crash_cooldown_tracker[sym] = _now_ts
                                _crash_ok = False
                                break

            # Fix 5 — per-coin cooldown check
            _cooldown_ok = True
            if ALERT_CFG.get("coin_cooldown"):
                _cooldown_mins = ALERT_CFG.get("coin_cooldown_mins", 30)
                _last_alert = _coin_alert_tracker.get(sym)
                if _last_alert and (_now_ts - _last_alert).seconds < _cooldown_mins * 60:
                    _cooldown_ok = False

            # 1h downtrend block: skip BUY signals when higher timeframe is bearish
            _1h_ok = True
            if ALERT_CFG.get("block_1h_downtrend", True) and "BUY" in sig:
                if r.get("trend_1h") == "down":
                    _1h_ok = False

            # Squeeze exemption (v2.6.1):
            # When BB is tightly squeezed (<3% width) on a STRONG BUY or PRE-BREAKOUT,
            # the expected-move formula underestimates the real move because ATR, BB width
            # and momentum are all suppressed by the coiling. KAT Mar 23 is the proof case:
            # exp_move was 2.5-2.9% for 60+ minutes of STRONG BUY before a +13% spike.
            # In a squeeze, low exp_move IS the setup — don't punish it.
            # Same logic applies to vol_ratio: pre-spike accumulation has thin volume.
            _bb_width = r.get("bb_width_pct", 99)
            _squeeze_exemption = (
                "BUY" in sig and
                _bb_width < ALERT_CFG.get("squeeze_exempt_bb_width", 3.0) and
                r.get("trend_1h") in ("up", "flat")
            )

            _exp_ok = (
                exp >= ALERT_CFG["min_exp_move"] or
                _squeeze_exemption
            )
            _vol_ok = (
                r.get("vol_ratio", 0) >= ALERT_CFG.get("min_vol_ratio", 0) or
                _squeeze_exemption
            )

            passes = (level <= min_level and
                      pot >= ALERT_CFG["min_potential"] and
                      _exp_ok and
                      r.get("rsi", 50) <= ALERT_CFG["max_rsi"] and
                      r.get("bb_pct", 50) <= ALERT_CFG["max_bb_pct"] and
                      r.get("adr_pct", 0) >= ALERT_CFG["min_adr_pct"] and
                      _vol_ok and
                      (not ALERT_CFG.get("block_downtrend") or not any(p in r.get("pattern", "") for p in ("Downtrend", "Rejection"))) and
                      (not ALERT_CFG.get("require_macd_rising") or r.get("macd_rising", False)) and
                      (not ALERT_CFG["require_vol_spike"] or r.get("vol_spike", False)) and
                      _spike_ok and _cooldown_ok and _1h_ok and _crash_ok)

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
