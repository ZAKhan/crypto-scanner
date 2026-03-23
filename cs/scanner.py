import time
import threading
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal

from cs.config import CFG
from cs.api import fetch_all_tickers, fetch_klines, fetch_trend_1h
from cs.indicators import analyse


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
                    t1h  = fetch_trend_1h(sym)
                    data = analyse(sym, raw, coin["change"], trend_1h=t1h)
                    data["symbol"]     = sym
                    data["trend_1h"]   = t1h
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
