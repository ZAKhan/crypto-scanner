import hmac
import hashlib
import math
import time
import urllib.parse

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
    TIMEOUT     = 10

    @staticmethod
    def _sign(params: dict, secret: str) -> str:
        query = urllib.parse.urlencode(params)
        return hmac.new(
            secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _signed_request(self, method: str, path: str,
                        params: dict | None = None) -> tuple[bool, dict]:
        key    = TRADING_CFG["api_key"].strip()
        secret = TRADING_CFG["api_secret"].strip()
        if not key or not secret:
            return False, {"error": "API key / secret not configured"}

        p = dict(params or {})
        last_err = ""
        for attempt in range(self.MAX_RETRIES):
            try:
                p["timestamp"]  = int(time.time() * 1000)
                p["recvWindow"] = 5000
                p["signature"]  = self._sign(p, secret)
                url     = trading_base() + path
                headers = {"X-MBX-APIKEY": key}

                if method == "GET":
                    resp = requests.get(url, params=p, headers=headers, timeout=self.TIMEOUT)
                elif method == "POST":
                    resp = requests.post(url, data=p, headers=headers, timeout=self.TIMEOUT)
                elif method == "DELETE":
                    resp = requests.delete(url, params=p, headers=headers, timeout=self.TIMEOUT)
                else:
                    return False, {"error": f"Unknown method {method}"}

                data = resp.json()
                if resp.status_code == 200:
                    return True, data
                code = data.get("code", 0)
                msg  = data.get("msg", str(data))
                if resp.status_code in (400, 401):
                    return False, {"error": f"[{code}] {msg}"}
                last_err = f"HTTP {resp.status_code}: {msg}"

            except requests.exceptions.Timeout:
                last_err = f"Timeout (attempt {attempt + 1})"
            except requests.exceptions.ConnectionError:
                last_err = f"Connection error (attempt {attempt + 1})"
            except Exception as e:
                last_err = str(e)

            if attempt < self.MAX_RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))

        return False, {"error": last_err}

    # ── Account ──────────────────────────────────────────

    def get_balances(self) -> tuple[bool, dict]:
        """
        Fetch all balances in one API call.
        Returns (True, {asset: free_float, ...}) or (False, {}).
        Use this instead of get_usdt_balance / get_asset_balance to avoid
        making multiple /account round-trips.
        """
        ok, data = self._signed_request("GET", "/api/v3/account")
        if not ok:
            return False, {}
        return True, {
            b["asset"]: float(b["free"])
            for b in data.get("balances", [])
            if float(b["free"]) > 0
        }

    def test_connection(self) -> tuple[bool, str]:
        ok, balances = self.get_balances()
        if not ok:
            return False, "Connection failed"
        usdt = balances.get("USDT", 0)
        env  = "TESTNET" if TRADING_CFG["testnet"] else "LIVE"
        return True, f"✓ Connected ({env}) — {usdt:,.2f} USDT available"

    def get_usdt_balance(self) -> tuple[bool, float]:
        ok, balances = self.get_balances()
        return (True, balances.get("USDT", 0.0)) if ok else (False, 0.0)

    def get_asset_balance(self, asset: str) -> tuple[bool, float]:
        ok, balances = self.get_balances()
        return (True, balances.get(asset, 0.0)) if ok else (False, 0.0)

    # ── Symbol info ──────────────────────────────────────

    def get_symbol_info(self, symbol: str) -> tuple[bool, dict]:
        try:
            resp = requests.get(
                trading_base() + "/api/v3/exchangeInfo",
                params={"symbol": symbol},
                timeout=self.TIMEOUT
            )
            data = resp.json()
            for sym in data.get("symbols", []):
                if sym["symbol"] == symbol:
                    filters = {}
                    for f in sym.get("filters", []):
                        ft = f["filterType"]
                        if ft == "LOT_SIZE":
                            filters["stepSize"] = float(f["stepSize"])
                            filters["minQty"]   = float(f["minQty"])
                        elif ft == "PRICE_FILTER":
                            filters["tickSize"] = float(f["tickSize"])
                        elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                            filters["minNotional"] = float(f.get("minNotional", 10))
                    return True, filters
            return False, {"error": f"Symbol {symbol} not found"}
        except Exception as e:
            return False, {"error": str(e)}

    # ── Rounding helpers ─────────────────────────────────

    @staticmethod
    def round_step(qty: float, step: float) -> float:
        if step <= 0:
            return qty
        precision = max(0, -int(math.floor(math.log10(step))))
        return round(math.floor(qty / step) * step, precision)

    @staticmethod
    def round_tick(price: float, tick: float) -> float:
        if tick <= 0:
            return price
        precision = max(0, -int(math.floor(math.log10(tick))))
        return round(round(price / tick) * tick, precision)

    # ── Orders ───────────────────────────────────────────

    def place_market_buy(self, symbol: str,
                         usdt_amount: float) -> tuple[bool, dict]:
        ok, info = self.get_symbol_info(symbol)
        if not ok:
            return False, info
        step         = info.get("stepSize", 0.00001)
        min_notional = info.get("minNotional", 10.0)
        try:
            pr    = requests.get(trading_base() + "/api/v3/ticker/price",
                                 params={"symbol": symbol}, timeout=self.TIMEOUT).json()
            price = float(pr["price"])
        except Exception as e:
            return False, {"error": f"Price fetch failed: {e}"}

        qty = self.round_step(usdt_amount / price, step)
        if qty * price < min_notional:
            return False, {"error": f"Order too small — minimum {min_notional} USDT notional"}
        return self._signed_request("POST", "/api/v3/order", {
            "symbol": symbol, "side": "BUY", "type": "MARKET",
            "quantity": f"{qty:.8f}",
        })

    def place_oco_sell(self, symbol: str, quantity: float,
                       tp_price: float, sl_price: float,
                       sl_limit_price: float) -> tuple[bool, dict]:
        ok, info = self.get_symbol_info(symbol)
        if not ok:
            return False, info
        tick = info.get("tickSize", 0.00001)
        step = info.get("stepSize", 0.00001)
        return self._signed_request("POST", "/api/v3/order/oco", {
            "symbol":               symbol,
            "side":                 "SELL",
            "quantity":             f"{self.round_step(quantity, step):.8f}",
            "price":                f"{self.round_tick(tp_price, tick):.8f}",
            "stopPrice":            f"{self.round_tick(sl_price, tick):.8f}",
            "stopLimitPrice":       f"{self.round_tick(sl_limit_price, tick):.8f}",
            "stopLimitTimeInForce": "GTC",
        })

    def place_market_sell(self, symbol: str,
                          quantity: float) -> tuple[bool, dict]:
        ok, info = self.get_symbol_info(symbol)
        if not ok:
            return False, info
        step = info.get("stepSize", 0.00001)
        return self._signed_request("POST", "/api/v3/order", {
            "symbol": symbol, "side": "SELL", "type": "MARKET",
            "quantity": f"{self.round_step(quantity, step):.8f}",
        })

    def cancel_order(self, symbol: str, order_id: int) -> tuple[bool, dict]:
        return self._signed_request("DELETE", "/api/v3/order",
                                    {"symbol": symbol, "orderId": order_id})

    def cancel_oco(self, symbol: str, order_list_id: int) -> tuple[bool, dict]:
        return self._signed_request("DELETE", "/api/v3/orderList",
                                    {"symbol": symbol, "orderListId": order_list_id})

    def get_open_orders(self, symbol: str) -> tuple[bool, list]:
        ok, data = self._signed_request("GET", "/api/v3/openOrders", {"symbol": symbol})
        return (True, data) if ok else (False, [])


# single shared instance
_trader = BinanceTrader()
