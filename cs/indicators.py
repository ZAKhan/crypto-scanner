import statistics

from cs.config import CFG


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
    if len(candles) >= 4:
        recent3 = candles[-4:-1]
        def _is_rejection_candle(c):
            body = abs(c["close"] - c["open"])
            rng  = c["high"] - c["low"]
            uw   = c["high"] - max(c["open"], c["close"])
            if rng == 0 or body == 0:
                return False
            return uw > body * 2.0 and uw / rng > 0.4
        if all(_is_rejection_candle(c) for c in recent3):
            return "Rejection ↓"

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
                 vol_ratio=1.0, macd_rising=False, bb_width_pct=0.0,
                 trend_1h="flat"):
    """
    Confluence scoring — quality over quantity.
    """
    long_score  = 0
    short_score = 0

    # ── RSI ────────────────────────────────────────────
    if rsi < 25:    long_score += 5
    elif rsi < 30:  long_score += 4
    elif rsi < 35:  long_score += 3
    elif rsi < 40:  long_score += 2
    elif rsi < 45:  long_score += 1
    if rsi > 75:    short_score += 5
    elif rsi > 70:  short_score += 4
    elif rsi > 65:  short_score += 3
    elif rsi > 60:  short_score += 2
    elif rsi > 55:  short_score += 1

    # ── Stochastic RSI ──────────────────────────────────
    if stoch_rsi < 20:  long_score  += 2
    elif stoch_rsi < 40: long_score += 1
    if stoch_rsi > 80:  short_score += 2
    elif stoch_rsi > 60: short_score+= 1

    # ── MACD — weight by freshness ─
    if macd_h > 0:
        long_score  += 3 if macd_rising else 1
    elif macd_h < 0:
        short_score += 3 if not macd_rising else 1

    # ── Bollinger Band position ─────────────────────────
    if bb_lower and bb_upper and bb_upper > bb_lower:
        pos = (price - bb_lower) / (bb_upper - bb_lower)
        bb_mult = 0.5 if bb_width_pct > 12 else 1.0

        if pos < 0.10:    long_score  += int(3 * bb_mult)
        elif pos < 0.25:  long_score  += int(2 * bb_mult)
        elif pos < 0.40:  long_score  += int(1 * bb_mult)

        if pos > 0.90:    short_score += int(3 * bb_mult)
        elif pos > 0.75:  short_score += int(2 * bb_mult)
        elif pos > 0.60:  short_score += int(1 * bb_mult)

    # ── Candlestick pattern ─────────────────────────────
    BULLISH = ["Hammer", "Bullish Engulf", "Vol Spike ↑", "Uptrend"]
    BEARISH  = ["Shooting Star", "Bearish Engulf", "Vol Spike ↓", "Downtrend", "Rejection"]
    for p in BULLISH:
        if p in pattern:
            long_score  += 2
            short_score -= 1
            break
    for p in BEARISH:
        if p in pattern:
            short_score += 2
            long_score  -= 1
            break
    if "Squeeze" in pattern:
        if rsi < 45: long_score  += 1
        if rsi > 55: short_score += 1

    # ── 24h momentum ───────────────────────────────────
    if change_24h > 15:    short_score += 2
    elif change_24h > 8:   short_score += 1
    elif change_24h < -10: long_score  += 1

    # ── 1h trend alignment ────────────────────
    if trend_1h == "up":
        long_score  += 1
    elif trend_1h == "down":
        long_score  -= 2
        short_score += 1

    # ── Clip negatives ──────────────────────────────────
    long_score  = max(0, long_score)
    short_score = max(0, short_score)

    # ── Determine signal with margin check ──────────────
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
    """
    score = 0
    sig = r["signal"]

    if "STRONG" in sig:  score += 30
    elif "BUY" in sig or "SELL" in sig: score += 15

    vr = r.get("vol_ratio", 1)
    if vr > 3:    score += 25
    elif vr > 2:  score += 18
    elif vr > 1.5:score += 12
    elif vr > 1:  score += 6

    bbu, bbl = r.get("bb_upper"), r.get("bb_lower")
    price = r["price"]
    if bbu and bbl and bbu != bbl:
        pos = (price - bbl) / (bbu - bbl)
        if "BUY" in sig:
            score += int((1 - pos) * 20)
        else:
            score += int(pos * 20)

    rsi = r["rsi"]
    if "BUY" in sig:
        if rsi < 25:    score += 15
        elif rsi < 35:  score += 10
        elif rsi < 45:  score += 5
    else:
        if rsi > 75:    score += 15
        elif rsi > 65:  score += 10
        elif rsi > 55:  score += 5

    mh = r.get("macd_hist", 0)
    if ("BUY" in sig and mh > 0) or ("SELL" in sig and mh < 0):
        score += 10

    srsi = r.get("stoch_rsi", 50)
    if "BUY" in sig and srsi < 20:    score += 10
    elif "SELL" in sig and srsi > 80: score += 10

    return min(score, 100)


def calc_expected_move(candles, signal):
    """
    Estimate expected % move based on ATR, BB width, and recent momentum.
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
    if len(closes) >= 20:
        win = closes[-20:]
        mid = statistics.mean(win)
        std = statistics.stdev(win)
        bb_width_pct = (std * 4 / price) * 100

    # Momentum: avg candle body size over last 5 candles as % of price
    bodies = [abs(c["close"] - c["open"]) for c in candles[-5:]]
    momentum_pct = (statistics.mean(bodies) / price) * 100

    expected = (atr_pct * 0.5) + (bb_width_pct * 0.3) + (momentum_pct * 0.2)

    if "STRONG" in signal:
        expected *= 1.4
    elif "BUY" in signal or "SELL" in signal:
        expected *= 1.1

    return round(expected, 2)


def analyse(symbol, raw_klines, change_24h=0.0, trend_1h="flat"):
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

    macd_rising = False
    if len(macd_hist_series) >= 2:
        macd_rising = macd_hist_series[-1] > macd_hist_series[-2]

    bb_width_pct = 0.0
    if bbu and bbl and closes[-1] > 0:
        bb_width_pct = (bbu - bbl) / closes[-1] * 100

    adr_pct = 0.0
    if len(candles) >= 5 and closes[-1] > 0:
        recent = candles[-10:]
        ranges = [(c["high"] - c["low"]) / c["close"] * 100
                  for c in recent if c["close"] > 0]
        adr_pct = round(statistics.mean(ranges), 2) if ranges else 0.0

    signal, sig_clr, long_sc, short_sc = score_signal(
        rsi, mh, closes[-1], bbu, bbl, bbm, pattern, change_24h, stoch_rsi,
        vol_ratio=vol_ratio, macd_rising=macd_rising, bb_width_pct=bb_width_pct,
        trend_1h=trend_1h)

    # ── PRE-BREAKOUT detection ──────────────────────────
    pre_breakout = False
    if signal in ("NEUTRAL", "BUY") and bbu and bbl and closes[-1] > 0:
        bb_pct_pos    = (closes[-1] - bbl) / (bbu - bbl) * 100 if (bbu - bbl) > 0 else 50
        recent_high   = max(highs[-15:])
        at_resistance = closes[-1] >= recent_high * 0.99
        pre_breakout = (
            1.5 <= bb_width_pct < 5.0 and
            vol_ratio >= 1.5 and
            35 <= rsi <= 55 and
            bb_pct_pos < 25 and
            not at_resistance
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
        "vol_spike":    vol_ratio >= 2.0,
        "adr_pct":      adr_pct,
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
