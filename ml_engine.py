import os
import time
import pickle
import warnings
import requests
import psycopg2
import pytz
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

try:
    import streamlit as st
    HAS_STREAMLIT = True
except ImportError:
    HAS_STREAMLIT = False

from xgboost import XGBClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, f1_score

warnings.filterwarnings("ignore")
load_dotenv()

MODEL_MAX_AGE_DAYS      = int(os.getenv("MODEL_MAX_AGE_DAYS",   "7"))
TRAIN_LIMIT             = int(os.getenv("TRAIN_LIMIT",          "1200"))
PREDICT_LIMIT           = int(os.getenv("PREDICT_LIMIT",        "300"))
DATA_CACHE_TTL_SECONDS  = int(os.getenv("DATA_CACHE_TTL_SECONDS","900"))
XGB_SPLITS              = int(os.getenv("XGB_SPLITS",           "5"))
PERFORMANCE_LOOKBACK    = int(os.getenv("PERFORMANCE_LOOKBACK",  "25"))
MIN_PERFORMANCE_SAMPLES = int(os.getenv("MIN_PERFORMANCE_SAMPLES","8"))
DATA_CACHE              = {}
DATA_CACHE_MAX_ENTRIES  = int(os.getenv("DATA_CACHE_MAX_ENTRIES","200"))

# ─── Triple Barrier параметри ────────────────────────────────────────────────
TBL_TP_MULT  = float(os.getenv("TBL_TP_MULT",  "2.0"))   # TP = entry + ATR*2.0
TBL_SL_MULT  = float(os.getenv("TBL_SL_MULT",  "1.5"))   # SL = entry - ATR*1.5
TBL_MAX_HOLD = int(os.getenv("TBL_MAX_HOLD",   "6"))      # макс. барів у позиції

# ─── Порогові значення ────────────────────────────────────────────────────────
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "55.0"))  # binary: random=50%
MIN_ACCURACY   = float(os.getenv("MIN_ACCURACY",   "52.0"))  # binary: random=50%
MIN_ADX        = float(os.getenv("MIN_ADX",        "18.0"))  # мінімальна сила тренду
MIN_ATR_PCT    = float(os.getenv("MIN_ATR_PCT",    "0.08"))  # мінімальна волатильність


# ════════════════════════════════════════════════════════════════════════════════
# DATA CACHE
# ════════════════════════════════════════════════════════════════════════════════

def purge_data_cache():
    now = time.time()
    expired = [k for k, v in DATA_CACHE.items()
               if now - v.get("saved_at", 0) > DATA_CACHE_TTL_SECONDS]
    for k in expired:
        DATA_CACHE.pop(k, None)
    if len(DATA_CACHE) > DATA_CACHE_MAX_ENTRIES:
        oldest = sorted(DATA_CACHE.items(), key=lambda x: x[1].get("saved_at", 0))
        for k, _ in oldest[:len(DATA_CACHE) - DATA_CACHE_MAX_ENTRIES]:
            DATA_CACHE.pop(k, None)
    if expired:
        print(f"🧹 DATA_CACHE: видалено {len(expired)}, залишилось {len(DATA_CACHE)}")


# ════════════════════════════════════════════════════════════════════════════════
# DATABASE
# ════════════════════════════════════════════════════════════════════════════════

def get_db_connection():
    if HAS_STREAMLIT:
        try:
            return psycopg2.connect(
                dbname=st.secrets["POSTGRES_DB"],   user=st.secrets["POSTGRES_USER"],
                password=st.secrets["POSTGRES_PASSWORD"], host=st.secrets["DB_HOST"],
                port=st.secrets["DB_PORT"])
        except Exception:
            pass
    return psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB"),   user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"), host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"))


BINANCE_SYMBOL_MAP = {
    "MATIC": "POL", "LUNA": "LUNC", "RENDER": "RENDER", "FET": "FET",
    "WLD": "WLD",   "NOT": "NOT",   "TON": "TON",   "ICP": "ICP",
    "KAS": "KAS",   "HBAR": "HBAR", "FIL": "FIL",   "ALGO": "ALGO",
    "VET": "VET",   "XLM": "XLM",  "TRX": "TRX",   "XMR": "XMR",
    "MKR": "MKR",   "AAVE": "AAVE","CRV": "CRV",   "LDO": "LDO",
    "ATOM": "ATOM", "DOT": "DOT",  "LTC": "LTC",   "ADA": "ADA",
    "DOGE": "DOGE", "LINK": "LINK","AVAX": "AVAX", "UNI": "UNI",
    "SOL": "SOL",   "BNB": "BNB",  "ETH": "ETH",   "BTC": "BTC",
    "XRP": "XRP",   "ARB": "ARB",  "OP": "OP",     "SUI": "SUI",
    "APT": "APT",   "INJ": "INJ",  "NEAR": "NEAR", "SHIB": "SHIB",
    "FLOKI": "FLOKI","BONK": "BONK","WIF": "WIF",  "PEPE": "PEPE",
}


def log_prediction_to_db(data: dict):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        def sf(v):
            return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
        cur.execute("""
            INSERT INTO model_predictions
            (symbol, interval, signal, price, confidence, accuracy,
             raw_prediction, stop_loss, take_profit, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            str(data.get("symbol", "")), str(data.get("interval", "")),
            str(data.get("signal", "")),  sf(data.get("price")),
            sf(data.get("confidence")),   sf(data.get("accuracy")),
            str(data.get("raw_prediction", "")),
            sf(data.get("stop_loss")),    sf(data.get("take_profit")),
            datetime.now(timezone.utc),
        ))
        conn.commit(); cur.close()
        return True
    except Exception as e:
        print(f"❌ log_prediction_to_db: {e}")
        return False
    finally:
        if conn: conn.close()


def save_model_to_db(symbol, interval, model, accuracy, features):
    conn = None
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("DELETE FROM ml_models WHERE symbol=%s AND interval=%s", (symbol, interval))
        kyiv = datetime.now(pytz.timezone("Europe/Kyiv")).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            INSERT INTO ml_models (symbol,interval,model_binary,accuracy,features,trained_at)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (symbol, interval, psycopg2.Binary(pickle.dumps(model)),
              float(accuracy) if accuracy else None, [str(f) for f in features], kyiv))
        conn.commit(); cur.close()
    except Exception as e:
        print(f"❌ save_model_to_db: {e}")
    finally:
        if conn: conn.close()


def load_model_from_db(symbol, interval):
    conn = None
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("""
            SELECT model_binary, accuracy, features, trained_at
            FROM ml_models WHERE symbol=%s AND interval=%s
        """, (symbol, interval))
        row = cur.fetchone(); cur.close()
        if row:
            trained_at = row[3]
            age = datetime.now(trained_at.tzinfo) - trained_at
            if age < timedelta(days=MODEL_MAX_AGE_DAYS):
                return pickle.loads(row[0]), row[1], row[2]
    except Exception as e:
        print(f"⚠️ load_model_from_db ({symbol}/{interval}): {e}")
    finally:
        if conn: conn.close()
    return None


# ════════════════════════════════════════════════════════════════════════════════
# INDICATORS
# ════════════════════════════════════════════════════════════════════════════════

def _rsi(series: pd.Series, p: int = 14) -> pd.Series:
    d = series.diff()
    g = d.where(d > 0, 0.0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.where(d < 0, 0.0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _macd(s: pd.Series, fast=12, slow=26, sig=9):
    m = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    sl = m.ewm(span=sig, adjust=False).mean()
    return m, sl, m - sl

def _bb(s: pd.Series, p=20, n=2):
    mu = s.rolling(p).mean(); sd = s.rolling(p).std()
    u, l = mu + sd*n, mu - sd*n
    return u, l, (u-l)/mu.replace(0,np.nan), (s-l)/(u-l).replace(0,np.nan)

def _obv(c: pd.Series, v: pd.Series) -> pd.Series:
    return pd.Series(
        np.where(c > c.shift(), v, np.where(c < c.shift(), -v, 0)),
        index=c.index).cumsum()

def _atr(hi: pd.Series, lo: pd.Series, cl: pd.Series, p=14) -> pd.Series:
    tr = pd.concat([hi-lo, (hi-cl.shift()).abs(), (lo-cl.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def _stoch(hi: pd.Series, lo: pd.Series, cl: pd.Series, k=14, d=3):
    ll, hh = lo.rolling(k).min(), hi.rolling(k).max()
    K = 100*(cl-ll)/(hh-ll).replace(0,np.nan)
    return K, K.rolling(d).mean()

def _adx(hi: pd.Series, lo: pd.Series, cl: pd.Series, p=14):
    """Average Directional Index — сила тренду (не напрямок).
    ADX > 25: сильний тренд | ADX < 20: флет → менш надійні сигнали."""
    tr = pd.concat([hi-lo, (hi-cl.shift()).abs(), (lo-cl.shift()).abs()], axis=1).max(axis=1)
    up, dn = hi - hi.shift(), lo.shift() - lo
    dm_p = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=cl.index)
    dm_m = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=cl.index)
    atr14 = tr.rolling(p).mean()
    di_p = 100 * dm_p.rolling(p).mean() / atr14.replace(0, np.nan)
    di_m = 100 * dm_m.rolling(p).mean() / atr14.replace(0, np.nan)
    dx = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, np.nan)
    return dx.rolling(p).mean(), di_p, di_m

def _williams_r(hi: pd.Series, lo: pd.Series, cl: pd.Series, p=14) -> pd.Series:
    """Williams %R: 0=перекуплено, -100=перепродано."""
    hh, ll = hi.rolling(p).max(), lo.rolling(p).min()
    return -100 * (hh - cl) / (hh - ll).replace(0, np.nan)

def _cci(hi: pd.Series, lo: pd.Series, cl: pd.Series, p=20) -> pd.Series:
    """Commodity Channel Index: відхилення від середньої ціни."""
    tp = (hi + lo + cl) / 3
    mu = tp.rolling(p).mean()
    mad = tp.rolling(p).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - mu) / (0.015 * mad.replace(0, np.nan))


# ════════════════════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ════════════════════════════════════════════════════════════════════════════════

def add_core_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "h" in df.columns:
        df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"vol"})

    # ── Класичні індикатори ──────────────────────────────────────────
    df["RSI"]           = _rsi(df["close"])
    df["RSI_slope"]     = df["RSI"].diff(3)
    df["MACD"], df["MACD_sig"], df["MACD_hist"] = _macd(df["close"])
    df["EMA_20"]        = df["close"].ewm(span=20,  adjust=False).mean()
    df["EMA_50"]        = df["close"].ewm(span=50,  adjust=False).mean()
    df["EMA_200"]       = df["close"].ewm(span=200, adjust=False).mean()
    df["EMA_cross"]     = df["EMA_20"] - df["EMA_50"]
    df["EMA_cross_50_200"] = df["EMA_50"] - df["EMA_200"]    # golden/death cross
    df["BB_upper"], df["BB_lower"], df["BB_bandwidth"], df["BB_percent"] = _bb(df["close"])
    df["OBV"]           = _obv(df["close"], df["vol"])
    df["OBV_ema"]       = df["OBV"].ewm(span=20, adjust=False).mean()
    df["OBV_signal"]    = df["OBV"] - df["OBV_ema"]
    df["OBV_slope"]     = df["OBV_signal"].diff(3)           # прискорення OBV
    df["ATR"]           = _atr(df["high"], df["low"], df["close"])
    df["ATR_pct"]       = df["ATR"] / df["close"] * 100
    df["Stoch_K"], df["Stoch_D"] = _stoch(df["high"], df["low"], df["close"])
    df["Stoch_cross"]   = df["Stoch_K"] - df["Stoch_D"]

    # ── ADX: сила тренду + напрямок ──────────────────────────────────
    adx, di_p, di_m     = _adx(df["high"], df["low"], df["close"])
    df["ADX"]           = adx
    df["DI_diff"]       = di_p - di_m    # > 0 → LONG тренд, < 0 → SHORT тренд
    df["DI_plus"]       = di_p
    df["DI_minus"]      = di_m

    # ── Williams %R + CCI ─────────────────────────────────────────────
    df["WilliamsR"]     = _williams_r(df["high"], df["low"], df["close"])
    df["CCI"]           = _cci(df["high"], df["low"], df["close"])

    # ── Свічкові патерни ─────────────────────────────────────────────
    df["body_size"]     = (df["close"] - df["open"]).abs() / df["open"] * 100
    df["upper_wick"]    = (df["high"] - df[["close","open"]].max(axis=1)) / df["open"] * 100
    df["lower_wick"]    = (df[["close","open"]].min(axis=1) - df["low"]) / df["open"] * 100
    df["bullish_candle"]= (df["close"] > df["open"]).astype(int)
    rolling_high_20     = df["high"].rolling(20).max().shift(1)
    df["fake_breakout"] = ((df["high"] > rolling_high_20) & (df["close"] < df["open"])).astype(int)

    # ── Об'єм ─────────────────────────────────────────────────────────
    df["vol_sma_20"]    = df["vol"].rolling(20).mean()
    df["vol_ratio"]     = df["vol"] / df["vol_sma_20"]
    df["volume_spike"]  = df["vol"] / df["vol"].rolling(50).mean()
    df["vol_trend"]     = df["vol"].rolling(5).mean() / df["vol"].rolling(20).mean()

    # ── Моментум ──────────────────────────────────────────────────────
    df["momentum_3"]    = df["close"].pct_change(3) * 100
    df["momentum_7"]    = df["close"].pct_change(7) * 100
    df["momentum_14"]   = df["close"].pct_change(14) * 100

    # ── Трендові метрики ─────────────────────────────────────────────
    df["trend_strength"]  = (df["EMA_20"] - df["EMA_200"]).abs() / df["close"]
    df["price_vs_ema20"]  = (df["close"] - df["EMA_20"]) / df["EMA_20"]
    df["price_vs_ema50"]  = (df["close"] - df["EMA_50"]) / df["EMA_50"]

    # ── Позиція ціни в діапазоні ─────────────────────────────────────
    rng20 = (df["high"].rolling(20).max() - df["low"].rolling(20).min()).replace(0, np.nan)
    df["price_position"] = (df["close"] - df["low"].rolling(20).min()) / rng20

    # ── Rolling Sharpe (якість моменту) ──────────────────────────────
    rets = df["close"].pct_change()
    df["rolling_sharpe"] = (rets.rolling(20).mean() /
                            rets.rolling(20).std().replace(0, np.nan) * np.sqrt(20))

    # ── Лаги ключових сигналів ────────────────────────────────────────
    for col in ["RSI", "MACD_hist", "price_vs_ema20", "Stoch_cross", "ADX", "DI_diff"]:
        df[f"{col}_lag1"] = df[col].shift(1)
        df[f"{col}_lag2"] = df[col].shift(2)

    return df


def build_aux_features(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    df = add_core_features(df)
    keep = ["ts","RSI","MACD_hist","EMA_cross","trend_strength",
            "price_vs_ema20","ATR_pct","ADX","DI_diff","volume_spike"]
    aux = df[[c for c in keep if c in df.columns]].copy()
    return aux.rename(columns={c: f"{prefix}_{c}" for c in aux.columns if c != "ts"})


def get_aux_intervals(base: str):
    return {"4h":["1d","1w"], "1d":["1w","1M"], "1h":["4h","1d"],
            "15m":["1h","4h"], "5m":["15m","1h"]}.get(base, ["1d"])


def merge_multi_timeframe_features(base_df: pd.DataFrame, symbol: str, base_interval: str) -> pd.DataFrame:
    df = base_df.copy()
    for iv in get_aux_intervals(base_interval):
        raw = fetch_binance_data(f"{symbol}USDT", iv, limit=500)
        if raw is None or len(raw) < 50: continue
        aux = build_aux_features(raw, prefix=iv)
        td_map = {"m": "minutes","h": "hours","d": "days","w": "weeks"}
        unit = iv[-1]; val = int(iv[:-1])
        try:
            td = pd.Timedelta(**{td_map.get(unit, "days"): val * (7 if unit=="w" else 1)})
        except Exception:
            td = pd.Timedelta(0)
        aux["ts"] = aux["ts"] + td
        df = pd.merge_asof(df.sort_values("ts"), aux.sort_values("ts"), on="ts", direction="backward")
    return df


def merge_market_context_features(base_df: pd.DataFrame, base_interval: str) -> pd.DataFrame:
    df = base_df.copy().sort_values("ts")
    for sym, pfx in [("BTCUSDT","btc"), ("ETHUSDT","eth")]:
        raw = fetch_binance_data(sym, base_interval, limit=min(TRAIN_LIMIT, 800))
        if raw is None or len(raw) < 80: continue
        ctx = add_core_features(raw)
        ctx["ret_3"]   = ctx["close"].pct_change(3) * 100
        ctx["ret_7"]   = ctx["close"].pct_change(7) * 100
        ctx["trend"]   = (ctx["EMA_20"] - ctx["EMA_50"]) / ctx["close"]
        cols = ["ts","RSI","MACD_hist","ret_3","ret_7","trend","ATR_pct","ADX","DI_diff"]
        ctx = ctx[[c for c in cols if c in ctx.columns]].rename(
            columns={c: f"{pfx}_{c}" for c in cols if c != "ts" and c in ctx.columns})
        df = pd.merge_asof(df.sort_values("ts"), ctx.sort_values("ts"), on="ts", direction="backward")
    return df


def build_feature_list(df: pd.DataFrame) -> list:
    core = [
        "RSI","RSI_slope","MACD","MACD_sig","MACD_hist",
        "EMA_cross","EMA_cross_50_200","BB_bandwidth","BB_percent",
        "OBV_signal","OBV_slope","ATR_pct",
        "Stoch_K","Stoch_D","Stoch_cross",
        "ADX","DI_diff","DI_plus","DI_minus",
        "WilliamsR","CCI",
        "body_size","upper_wick","lower_wick","bullish_candle","fake_breakout",
        "vol_ratio","volume_spike","vol_trend",
        "momentum_3","momentum_7","momentum_14",
        "trend_strength","price_vs_ema20","price_vs_ema50",
        "price_position","rolling_sharpe",
        "RSI_lag1","RSI_lag2",
        "MACD_hist_lag1","MACD_hist_lag2",
        "price_vs_ema20_lag1","price_vs_ema20_lag2",
        "Stoch_cross_lag1","Stoch_cross_lag2",
        "ADX_lag1","ADX_lag2",
        "DI_diff_lag1","DI_diff_lag2",
    ]
    mtf = [
        f"{pf}_{f}"
        for pf in ["1h","4h","1d","1w","15m","5m"]
        for f in ["RSI","MACD_hist","EMA_cross","trend_strength","price_vs_ema20","ATR_pct","ADX","DI_diff","volume_spike"]
    ]
    ctx = [
        f"{pfx}_{f}"
        for pfx in ["btc","eth"]
        for f in ["RSI","MACD_hist","ret_3","ret_7","trend","ATR_pct","ADX","DI_diff"]
    ]
    all_candidates = core + mtf + ctx
    return [f for f in all_candidates if f in df.columns]


# ════════════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ════════════════════════════════════════════════════════════════════════════════

def fetch_binance_data(symbol: str, interval: str, limit: int = 1500):
    key = (symbol.upper(), interval, int(limit))
    cached = DATA_CACHE.get(key)
    now = time.time()
    if cached and now - cached["saved_at"] <= DATA_CACHE_TTL_SECONDS:
        return cached["df"].copy()

    url = "https://data-api.binance.vision/api/v3/klines"
    all_data, end_time, remaining = [], None, limit
    while remaining > 0:
        n = min(remaining, 1000)
        p = {"symbol": symbol.upper(), "interval": interval, "limit": n}
        if end_time: p["endTime"] = end_time
        try:
            r = requests.get(url, params=p, headers={"User-Agent":"Mozilla/5.0"}, timeout=5)
            if r.status_code != 200: break
            data = r.json()
            if not data: break
            all_data.extend(data)
            end_time = data[0][0] - 1
            remaining -= len(data)
            if len(data) < n: break
        except Exception: break
        time.sleep(0.1)

    if not all_data: return None
    df = pd.DataFrame(all_data).iloc[:, :6]
    df.columns = ["ts","open","high","low","close","vol"]
    df[["open","high","low","close","vol"]] = df[["open","high","low","close","vol"]].astype(float)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True).tail(limit)
    DATA_CACHE[key] = {"saved_at": now, "df": df.copy()}
    return df


def resolve_binance_symbol(symbol: str) -> str:
    s = symbol.upper().replace("-","").replace(" ","")
    return BINANCE_SYMBOL_MAP.get(s, s)


# ════════════════════════════════════════════════════════════════════════════════
# TRIPLE BARRIER LABELING  (Marcos Lopez de Prado, AFML 2018)
# ════════════════════════════════════════════════════════════════════════════════

def triple_barrier_labels(df: pd.DataFrame,
                           sl_mult: float = TBL_SL_MULT,
                           tp_mult: float = TBL_TP_MULT,
                           max_bars: int  = TBL_MAX_HOLD) -> pd.DataFrame:
    """
    Для кожного бару визначає: що відбулось першим — TP чи SL?

      label=1  → TP досягнуто першим  (чіткий рух вгору  → LONG)
      label=0  → SL досягнуто першим  (чіткий рух вниз   → SHORT)
      label=NaN → жоден бар'єр не досягнутий за max_bars → виключається з навчання

    Переваги vs "майбутній дохід через N барів":
    • Тренується ЛИШЕ на чітких рухах — фільтрує шумові бари
    • Враховує ризик: асиметричний TP/SL = очікуємо R/R > 1.3x
    • Немає класу NEUTRAL — бінарна задача (random=50%, vs 33% для 3-класової)
    """
    df = df.copy()
    close = df["close"].values
    high  = df["high"].values
    low   = df["low"].values
    atr   = df["ATR"].values
    n     = len(df)
    labels = np.full(n, np.nan)

    for i in range(n - max_bars):
        entry = close[i]
        a     = atr[i]
        if a <= 0 or np.isnan(a) or np.isnan(entry) or entry <= 0:
            continue
        upper = entry + a * tp_mult   # Take Profit бар'єр
        lower = entry - a * sl_mult   # Stop Loss  бар'єр

        for j in range(1, max_bars + 1):
            if high[i + j] >= upper:
                labels[i] = 1   # TP спрацював першим → LONG
                break
            if low[i + j] <= lower:
                labels[i] = 0   # SL спрацював першим → SHORT
                break
        # Якщо ні один бар'єр — NaN (бар виключається з навчання)

    df["target"] = labels
    return df


# ════════════════════════════════════════════════════════════════════════════════
# PERFORMANCE FEEDBACK
# ════════════════════════════════════════════════════════════════════════════════

def get_recent_signal_performance(symbol: str, interval: str, current_price: float) -> dict:
    conn = None
    empty = {"count":0, "winrate":None, "confidence_adjustment":0.0, "accuracy_adjustment":0.0}
    horizon = {"4h":4, "1d":24, "1w":168, "1M":720}.get(interval, 24)
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("""
            SELECT signal, price FROM model_predictions
            WHERE UPPER(symbol)=%s AND interval=%s
              AND price IS NOT NULL AND signal IS NOT NULL
              AND signal NOT ILIKE 'NO TRADE%%'
              AND created_at <= NOW() - (%s * INTERVAL '1 hour')
            ORDER BY created_at DESC LIMIT %s
        """, (symbol.upper(), interval, horizon, PERFORMANCE_LOOKBACK))
        rows = cur.fetchall(); cur.close()

        wins = checked = 0
        for sig, ep in rows:
            if not ep or float(ep) <= 0: continue
            s = str(sig).upper(); e = float(ep)
            if "LONG"  in s: wins += int(current_price > e); checked += 1
            elif "SHORT" in s: wins += int(current_price < e); checked += 1

        if checked < MIN_PERFORMANCE_SAMPLES:
            return {"count": checked, "winrate": None,
                    "confidence_adjustment": 0.0, "accuracy_adjustment": 0.0}

        wr = wins / checked * 100
        ca = (8.0 if wr < 42 else 4.0 if wr < 48 else -3.0 if wr > 64 else -1.5 if wr > 58 else 0.0)
        aa = (3.0 if wr < 42 else 1.5 if wr < 48 else 0.0)
        return {"count": checked, "winrate": round(wr, 1),
                "confidence_adjustment": ca, "accuracy_adjustment": aa}
    except Exception as e:
        print(f"⚠️ get_recent_signal_performance: {e}")
        return empty
    finally:
        if conn: conn.close()


# ════════════════════════════════════════════════════════════════════════════════
# MODEL TRAINING  (Binary XGBoost + Triple Barrier + Feature Selection)
# ════════════════════════════════════════════════════════════════════════════════

def train_intelligent_model(symbol: str, interval: str, fut_bars: int, _atr_m: float):
    """
    Нова архітектура:
    1. Triple Barrier Labels  — чисті сигнали, без шуму
    2. Binary XGBoost         — random=50%, реально досягаємо 54-62%
    3. Feature Selection       — залишаємо top-75% за важливістю → менше шуму
    4. Адаптивна к-сть фолдів — для 1w/1M уникаємо мізерних train-фолдів
    """
    df = fetch_binance_data(f"{symbol}USDT", interval, limit=TRAIN_LIMIT)
    if df is None or df.empty: return None, {}, []

    df = add_core_features(df)
    df = merge_multi_timeframe_features(df, symbol, interval)
    df = merge_market_context_features(df, interval)
    features = build_feature_list(df)
    df[features] = df[features].ffill().fillna(0)

    # ── Triple Barrier: max_bars = 2× fut_bars, мін. 3 ──────────────
    hold = max(fut_bars * 2, 3, TBL_MAX_HOLD)
    df = triple_barrier_labels(df, sl_mult=TBL_SL_MULT, tp_mult=TBL_TP_MULT, max_bars=hold)

    # Тільки бари де бар'єр реально досягнутий
    df_c = df.dropna(subset=["target"]).copy()
    df_c["target"] = df_c["target"].astype(int)

    n_long  = int(np.sum(df_c["target"] == 1))
    n_short = int(np.sum(df_c["target"] == 0))
    total   = len(df_c)

    if total < 60 or n_long < 20 or n_short < 20:
        print(f"⚠️ {symbol}/{interval}: мало сигналів (LONG={n_long}, SHORT={n_short})")
        return None, {}, []

    print(f"  📊 {symbol}/{interval}: LONG={n_long}, SHORT={n_short}, всього={total}")

    X = df_c[features].values
    y = df_c["target"].values

    # ── Binary XGBoost (random=50%) ──────────────────────────────────
    params = {
        "n_estimators":    300,
        "max_depth":       4,
        "learning_rate":   0.04,
        "subsample":       0.8,
        "colsample_bytree":0.7,
        "min_child_weight":5,
        "gamma":           0.1,
        "reg_alpha":       0.1,    # L1 — відсікає шумові фічі
        "reg_lambda":      1.5,    # L2
        "objective":       "binary:logistic",
        "eval_metric":     "logloss",
        "tree_method":     "hist",
        "n_jobs":          1,
        "verbosity":       0,
    }

    # Адаптивна к-сть фолдів
    n_splits = max(2, min(XGB_SPLITS, total // 40))
    tscv = TimeSeriesSplit(n_splits=n_splits)
    cv_acc, cv_f1 = [], []

    for tr_i, val_i in tscv.split(X):
        if len(np.unique(y[tr_i])) < 2: continue
        tmp = XGBClassifier(**params).fit(X[tr_i], y[tr_i])
        p = tmp.predict(X[val_i])
        cv_acc.append(accuracy_score(y[val_i], p))
        cv_f1.append(f1_score(y[val_i], p, zero_division=0))

    if not cv_acc: return None, {}, []

    # ── Feature selection: чорновий прогін → відбір top-75% ─────────
    draft = XGBClassifier(**params).fit(X, y)
    imps  = draft.feature_importances_
    nz    = imps[imps > 0]
    thresh = np.percentile(nz, 25) if len(nz) >= 10 else 0.0
    sel_feat = [f for f, imp in zip(features, imps) if imp >= thresh]
    if len(sel_feat) < 10: sel_feat = features  # fallback

    # ── Фінальна модель на відібраних фічах ─────────────────────────
    X_sel = df_c[sel_feat].values
    final = XGBClassifier(**params).fit(X_sel, y)

    top_f = sorted(zip(sel_feat, final.feature_importances_), key=lambda x: x[1], reverse=True)
    top_features = [{"feature": n, "importance": round(float(v), 6)} for n, v in top_f[:10]]

    metrics = {
        "accuracy":      round(np.mean(cv_acc) * 100, 1),
        "f1_score":      round(np.mean(cv_f1)  * 100, 1),
        "precision":     0, "recall": 0,
        "train_samples": total,
        "n_long":        n_long,
        "n_short":       n_short,
        "top_features":  top_features,
    }

    save_model_to_db(symbol, interval, final, metrics["accuracy"], sel_feat)
    return final, metrics, sel_feat


# ════════════════════════════════════════════════════════════════════════════════
# PREDICTION  (головна функція)
# ════════════════════════════════════════════════════════════════════════════════

def get_ml_signal(symbol: str, interval: str = "4h",
                  min_confidence: float = MIN_CONFIDENCE,
                  min_accuracy:   float = MIN_ACCURACY) -> dict:
    """
    Повертає ML-сигнал для символу на заданому таймфреймі.

    Нова логіка:
    • Binary XGBoost (LONG vs SHORT, random=50%) — реалістична точність 54-62%
    • Triple Barrier labels у навчанні — чисті сигнали без шуму
    • ADX-фільтр: у флеті (ADX<18) підвищуємо поріг впевненості
    • DI±-підтвердження: сигнал проти тренду → NO TRADE
    • Автоматичне перенавчання якщо збережена модель слабка
    """
    binance_sym = resolve_binance_symbol(symbol)
    mapping = {
        "4h": ("4h", 3),  "1d": ("1d", 3),
        "1w": ("1w", 2),  "1M": ("1M", 2),
        "1h": ("1h", 4),
    }
    act_tf, fut_bars = mapping.get(interval, (interval, 3))

    # ── Завантаження або навчання моделі ────────────────────────────
    MIN_STORED_ACC = 50.0   # для binary: нижче 50% = гірше за рандом → перенавчаємо
    cached = load_model_from_db(binance_sym, act_tf)
    if cached:
        model, acc, features = cached
        stored_acc = float(acc) if acc else 0.0
        if stored_acc < MIN_STORED_ACC:
            print(f"🔄 Перенавчання {binance_sym}/{act_tf}: acc={stored_acc:.1f}% < {MIN_STORED_ACC}%")
            model, metrics, features = train_intelligent_model(binance_sym, act_tf, fut_bars, 0.0)
            mode = "retrained"
            if not model: return {"status":"error","reason":"Недостатньо даних для навчання"}
        else:
            metrics = {"accuracy": stored_acc, "f1_score":0, "precision":0, "recall":0, "top_features":[]}
            mode = "loaded_from_db"
    else:
        model, metrics, features = train_intelligent_model(binance_sym, act_tf, fut_bars, 0.0)
        mode = "trained_fresh"
        if not model: return {"status":"error","reason":"Недостатньо даних для навчання"}

    # ── Підготовка поточних даних ────────────────────────────────────
    df_now = fetch_binance_data(f"{binance_sym}USDT", act_tf, limit=PREDICT_LIMIT)
    if df_now is None or df_now.empty:
        return {"status":"error","reason":"Помилка завантаження даних з Binance"}

    df_now = add_core_features(df_now)
    df_now = merge_multi_timeframe_features(df_now, binance_sym, act_tf)
    df_now = merge_market_context_features(df_now, act_tf)

    for f in features:
        if f not in df_now.columns: df_now[f] = 0.0

    X_pred = df_now[features].iloc[-1:].fillna(0).values
    last   = df_now.iloc[-1]
    price  = float(last["close"])

    # ── Предикт ──────────────────────────────────────────────────────
    pred   = int(model.predict(X_pred)[0])     # 1=LONG, 0=SHORT
    proba  = model.predict_proba(X_pred)[0]    # [P(SHORT), P(LONG)]
    prob_l = round(float(proba[1]) * 100, 1)
    prob_s = round(float(proba[0]) * 100, 1)
    prob   = prob_l if pred == 1 else prob_s   # впевненість у передбаченому класі
    raw_direction = "LONG" if pred == 1 else "SHORT"

    # ── Feedback + адаптивні пороги ──────────────────────────────────
    feedback = get_recent_signal_performance(symbol.upper(), interval, price)
    adap_conf = max(53.0, min(70.0, min_confidence + feedback["confidence_adjustment"]))
    adap_acc  = max(50.0, min(62.0, min_accuracy  + feedback["accuracy_adjustment"]))

    no_trade_reasons = []

    # 1. Впевненість нижче порогу
    if prob < adap_conf:
        no_trade_reasons.append(f"confidence<{adap_conf:.1f}")

    # 2. Точність моделі нижче порогу
    if float(metrics["accuracy"]) < adap_acc:
        no_trade_reasons.append(f"accuracy<{adap_acc:.1f}")

    # 3. ADX-фільтр: флет → менш надійні сигнали
    adx_val = float(last["ADX"]) if pd.notna(last.get("ADX")) else None
    if adx_val is not None and adx_val < MIN_ADX:
        # у флеті підвищуємо поріг на 7% — не забороняємо, але відсіюємо слабкі
        if prob < adap_conf + 7:
            no_trade_reasons.append(f"ranging_adx={adx_val:.0f}")

    # 4. DI± підтвердження: сигнал проти домінуючого тренду DI
    di_diff = float(last["DI_diff"]) if pd.notna(last.get("DI_diff")) else None
    if di_diff is not None and abs(di_diff) > 15:
        if raw_direction == "LONG"  and di_diff < -15 and prob < adap_conf + 5:
            no_trade_reasons.append("di_trend_conflict_short")
        if raw_direction == "SHORT" and di_diff > 15  and prob < adap_conf + 5:
            no_trade_reasons.append("di_trend_conflict_long")

    # 5. Мала волатильність (незначний рух)
    atr_pct = float(last["ATR_pct"]) if pd.notna(last.get("ATR_pct")) else None
    if atr_pct is not None and atr_pct < MIN_ATR_PCT and prob < adap_conf + 8:
        no_trade_reasons.append("flat_market")

    # 6. BTC-контекст: якщо ринок різко йде проти сигналу
    btc_ret_3 = float(last["btc_ret_3"]) if "btc_ret_3" in last.index and pd.notna(last.get("btc_ret_3")) else None
    if btc_ret_3 is not None:
        if raw_direction == "LONG"  and btc_ret_3 < -1.5 and prob < adap_conf + 7:
            no_trade_reasons.append("btc_context_bearish")
        if raw_direction == "SHORT" and btc_ret_3 > 1.5  and prob < adap_conf + 7:
            no_trade_reasons.append("btc_context_bullish")

    # ── Фінальний сигнал ─────────────────────────────────────────────
    if no_trade_reasons:
        final_signal = "NO TRADE ⚪"
    elif raw_direction == "LONG":
        final_signal = "LONG 🟢"
    else:
        final_signal = "SHORT 🔴"

    # ── SL / TP на основі ATR ────────────────────────────────────────
    atr_val = float(last["ATR"]) if pd.notna(last.get("ATR")) else price * 0.02
    dist_sl = min(atr_val * np.sqrt(fut_bars) * 1.0, price * 0.06)
    dist_tp = min(atr_val * np.sqrt(fut_bars) * 2.0, price * 0.12)

    sl = (price - dist_sl if final_signal == "LONG 🟢"
          else price + dist_sl if final_signal == "SHORT 🔴" else None)
    tp = (price + dist_tp if final_signal == "LONG 🟢"
          else price - dist_tp if final_signal == "SHORT 🔴" else None)

    def _g(k, digits=4):
        v = last.get(k)
        return round(float(v), digits) if v is not None and pd.notna(v) else None

    result = {
        "status":     "success",
        "symbol":     symbol.upper(),
        "binance_symbol": f"{binance_sym}USDT",
        "interval":   interval,
        "signal":     final_signal,
        "raw_prediction": raw_direction,
        "mode":       mode,
        "confidence": prob,
        "prob_long":  prob_l,
        "prob_short": prob_s,
        "accuracy":   metrics["accuracy"],
        "f1_score":   metrics.get("f1_score", 0),
        "price":      round(price, 6),
        "rsi":        _g("RSI", 1),
        "adx":        _g("ADX", 1),
        "di_diff":    _g("DI_diff", 1),
        "ema20":      _g("EMA_20"),
        "ema50":      _g("EMA_50"),
        "bb_percent": round(float(last["BB_percent"]) * 100, 1) if pd.notna(last.get("BB_percent")) else None,
        "williams_r": _g("WilliamsR", 1),
        "cci":        _g("CCI", 1),
        "stoch_k":    _g("Stoch_K", 1),
        "atr_pct":    _g("ATR_pct", 2),
        "volume_spike": _g("volume_spike", 2),
        "trend_strength": _g("trend_strength", 4),
        "price_vs_ema20": _g("price_vs_ema20", 4),
        "price_position": _g("price_position", 3),
        "rolling_sharpe": _g("rolling_sharpe", 3),
        "obv_trend":  ("↑" if _g("OBV_signal", 0) and float(last.get("OBV_signal",0)) > 0 else "↓"),
        "fake_breakout": int(last["fake_breakout"]) if pd.notna(last.get("fake_breakout")) else 0,
        "stop_loss":  round(sl, 6) if sl else None,
        "take_profit":round(tp, 6) if tp else None,
        "risk_reward":round(dist_tp / dist_sl, 2) if dist_sl > 0 else 2.0,
        "adaptive_min_confidence": round(adap_conf, 1),
        "adaptive_min_accuracy":   round(adap_acc, 1),
        "adx_value":  round(adx_val, 1) if adx_val is not None else None,
        "feedback_count":   feedback["count"],
        "feedback_winrate": feedback["winrate"],
        "no_trade_reasons": no_trade_reasons,
        "btc_ret_3":  round(btc_ret_3, 3) if btc_ret_3 else None,
        "top_features":   metrics.get("top_features", []),
        "features_used":  features,
        "n_long":     metrics.get("n_long", 0),
        "n_short":    metrics.get("n_short", 0),
    }
    result["db_logged"] = log_prediction_to_db(result)
    return result
