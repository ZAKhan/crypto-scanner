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
