import math
import requests

from cs.api import TRADING_CFG, trading_base


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
