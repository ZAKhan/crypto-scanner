import json
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

try:
    import websocket as _websocket
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

from cs.api import TRADING_CFG


class BinanceWebSocketPrices(QObject):
    """
    Maintains a persistent WebSocket connection to Binance.
    Emits price_update(symbol, price) signal on every tick.
    Auto-reconnects on disconnect.

    Two subscription tiers:
    - Trade symbols (_trade_syms): open positions — get a dedicated per-symbol
      miniTicker stream that fires on every trade execution (~real-time).
      Adding new trade symbols triggers a reconnect to build the new URL.
    - Alert symbols (_alert_syms): alert log entries — covered by the all-market
      !miniTicker@arr stream that fires every ~1s. No reconnect needed; prices
      arrive within 1s automatically for every symbol on Binance.
    """
    price_update = pyqtSignal(str, float)   # symbol, price
    connected    = pyqtSignal()
    disconnected = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._trade_syms  = set()   # open-position symbols → per-symbol streams
        self._alert_syms  = set()   # alert symbols → covered by all-market stream
        self._ws          = None
        self._thread      = None
        self._running     = False
        self._reconnect_delay = 3

    def _ws_url(self):
        """
        Build combined WebSocket URL.
        - Per-symbol @miniTicker for every open-trade symbol (fires on every fill).
        - !miniTicker@arr for the all-market snapshot (every ~1s, covers alert syms).
        """
        base_testnet = "wss://stream.testnet.binance.vision/stream?streams="
        base_live    = "wss://stream.binance.com:9443/stream?streams="
        base = base_testnet if TRADING_CFG["testnet"] else base_live

        streams = []
        for sym in sorted(self._trade_syms):
            streams.append(f"{sym.lower()}@miniTicker")
        streams.append("!miniTicker@arr@1000ms")   # 1 s cadence — sufficient for alert P&L

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
        """
        Subscribe open-trade symbols.
        Triggers a WS reconnect when the set changes so new per-symbol
        streams are included in the URL.
        """
        new_syms = {s.upper() for s in symbols}
        if new_syms == self._trade_syms:
            return
        self._trade_syms = new_syms
        self._reconnect()

    def subscribe_alert(self, symbol: str):
        """
        Subscribe an alert symbol.
        No reconnect required — the all-market stream already covers it.
        Prices will arrive within ~1s automatically.
        """
        self._alert_syms.add(symbol.upper())

    def _reconnect(self):
        """Close current WS so _run() rebuilds the URL and reconnects."""
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _run(self):
        while self._running:
            try:
                url = self._ws_url()
                _self = self

                def on_message(ws, msg):
                    try:
                        data = json.loads(msg)
                        tickers = data.get("data", data)
                        if isinstance(tickers, list):
                            # All-market miniTicker array — emit for ALL symbols.
                            # This is what feeds live P&L for alert rows.
                            for d in tickers:
                                sym   = d.get("s", "")
                                price = float(d.get("c", 0) or 0)
                                if sym and price > 0:
                                    _self.price_update.emit(sym, price)
                        elif isinstance(tickers, dict):
                            # Individual per-symbol stream for open trades
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
