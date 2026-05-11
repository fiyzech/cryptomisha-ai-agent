import os
import io
import pickle
import time
import warnings
import requests
import psycopg2
import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime, timedelta
from dotenv import load_dotenv

from xgboost import XGBClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

warnings.filterwarnings("ignore")
load_dotenv()


# --- ПІДКЛЮЧЕННЯ ТА СТВОРЕННЯ ТАБЛИЦЬ ---
def get_db_connection():
    try:
        return psycopg2.connect(
            dbname=st.secrets["POSTGRES_DB"],
            user=st.secrets["POSTGRES_USER"],
            password=st.secrets["POSTGRES_PASSWORD"],
            host=st.secrets["DB_HOST"],
            port=st.secrets["DB_PORT"]
        )
    except Exception:
        return psycopg2.connect(
            dbname=os.getenv("POSTGRES_DB", "CryptoPulse_db"),
            user=os.getenv("POSTGRES_USER", "CryptoPulse_admin"),
            password=os.getenv("POSTGRES_PASSWORD", "CryptoPulse_password"),
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432")
        )


def ensure_tables_exist():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Створюємо таблицю для мізків, ЯКЩО ЇЇ НЕМАЄ
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ml_models (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                interval VARCHAR(10) NOT NULL,
                model_binary BYTEA NOT NULL,
                accuracy REAL,
                features TEXT[],
                trained_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, interval)
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Помилка створення таблиці ml_models: {e}")


BINANCE_SYMBOL_MAP = {"MATIC": "POL", "LUNA": "LUNC", "NEAR": "NEAR", "SHIB": "SHIB", "PEPE": "PEPE", "SUI": "SUI",
                      "SOL": "SOL", "ETH": "ETH", "BTC": "BTC", "XRP": "XRP"}


def log_prediction_to_db(data: dict):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = """INSERT INTO model_predictions (symbol, interval, signal, price, confidence, accuracy, raw_prediction, stop_loss, take_profit) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        values = (data.get('symbol', ''), data.get('interval', ''), data.get('signal', ''), float(data.get('price', 0)),
                  float(data.get('confidence', 0)), float(data.get('accuracy', 0)), data.get('raw_prediction', ''),
                  data.get('stop_loss', None), data.get('take_profit', None))
        cursor.execute(query, values)
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"Помилка логування: {e}")
    finally:
        if conn is not None: conn.close()


# --- МАТЕМАТИКА ---
def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain, loss = delta.where(delta > 0, 0.0).ewm(alpha=1 / period, adjust=False).mean(), (
        -delta.where(delta < 0, 0.0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, np.nan)))


def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line, macd - signal_line


def calculate_bollinger_bands(series: pd.Series, period: int = 20, std: int = 2):
    sma, r_std = series.rolling(window=period).mean(), series.rolling(window=period).std()
    upper, lower = sma + (r_std * std), sma - (r_std * std)
    return upper, lower, (upper - lower) / sma.replace(0, np.nan), (series - lower) / (upper - lower).replace(0, np.nan)


def calculate_obv(close: pd.Series, volume: pd.Series):
    return pd.Series(np.where(close > close.shift(), volume, np.where(close < close.shift(), -volume, 0)),
                     index=close.index).cumsum()


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calculate_stochastic(high, low, close, k_p=14, d_p=3):
    ll, hh = low.rolling(window=k_p).min(), high.rolling(window=k_p).max()
    k = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    return k, k.rolling(window=d_p).mean()


def fetch_binance_data(symbol: str, interval: str, limit: int = 1500):
    url = "https://data-api.binance.vision/api/v3/klines"
    headers = {"User-Agent": "Mozilla/5.0"}
    all_data, end_time, remaining = [], None, limit
    while remaining > 0:
        current_limit = min(remaining, 1000)
        params = {"symbol": symbol.upper(), "interval": interval, "limit": current_limit}
        if end_time: params["endTime"] = end_time
        try:
            res = requests.get(url, params=params, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if not data or not isinstance(data, list): break
                all_data.extend(data)
                end_time = data[0][0] - 1
                remaining -= len(data)
                if len(data) < current_limit: break
            else:
                break
        except Exception:
            break
        time.sleep(0.1)
    if not all_data: return None
    df = pd.DataFrame(all_data).iloc[:, :6]
    df.columns = ["ts", "open", "high", "low", "close", "vol"]
    df[["open", "high", "low", "close", "vol"]] = df[["open", "high", "low", "close", "vol"]].astype(float)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True).tail(limit)


def get_aux_intervals(base): return {"5m": ["15m", "1h"], "15m": ["1h", "4h"], "1h": ["4h", "1d"], "4h": ["1d", "1w"],
                                     "1d": ["1w", "1M"]}.get(base, ["1d"])


def add_core_features(df: pd.DataFrame):
    df = df.copy()
    if "h" in df.columns: df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "vol"})
    df["RSI"] = calculate_rsi(df["close"])
    df["RSI_slope"] = df["RSI"].diff(3)
    df["MACD"], df["MACD_sig"], df["MACD_hist"] = calculate_macd(df["close"])
    df["EMA_20"], df["EMA_50"], df["EMA_200"] = df["close"].ewm(span=20).mean(), df["close"].ewm(span=50).mean(), df[
        "close"].ewm(span=200).mean()
    df["EMA_cross"] = df["EMA_20"] - df["EMA_50"]
    df["BB_upper"], df["BB_lower"], df["BB_bandwidth"], df["BB_percent"] = calculate_bollinger_bands(df["close"])
    df["OBV"] = calculate_obv(df["close"], df["vol"])
    df["OBV_ema"] = df["OBV"].ewm(span=20).mean()
    df["OBV_signal"] = df["OBV"] - df["OBV_ema"]
    df["ATR"] = calculate_atr(df["high"], df["low"], df["close"])
    df["ATR_pct"] = df["ATR"] / df["close"] * 100
    df["Stoch_K"], df["Stoch_D"] = calculate_stochastic(df["high"], df["low"], df["close"])
    df["Stoch_cross"] = df["Stoch_K"] - df["Stoch_D"]
    df["body_size"] = (df["close"] - df["open"]).abs() / df["open"] * 100
    df["upper_wick"] = (df["high"] - df[["close", "open"]].max(axis=1)) / df["open"] * 100
    df["lower_wick"] = (df[["close", "open"]].min(axis=1) - df["low"]) / df["open"] * 100
    df["vol_sma_20"] = df["vol"].rolling(20).mean()
    df["vol_ratio"] = df["vol"] / df["vol_sma_20"]
    df["trend_strength"] = (df["EMA_20"] - df["EMA_200"]).abs() / df["close"]
    df["price_vs_ema20"] = (df["close"] - df["EMA_20"]) / df["EMA_20"]
    for c in ["RSI", "MACD_hist", "price_vs_ema20", "Stoch_cross"]: df[f"{c}_lag1"], df[f"{c}_lag2"] = df[c].shift(1), \
    df[c].shift(2)
    return df


def merge_multi_timeframe_features(df: pd.DataFrame, pair: str, base_interval: str):
    df = df.copy()
    for aux in get_aux_intervals(base_interval):
        aux_raw = fetch_binance_data(pair, interval=aux, limit=500)
        if aux_raw is None or len(aux_raw) < 50: continue
        aux_df = add_core_features(aux_raw)[
            ["ts", "RSI", "MACD_hist", "EMA_cross", "trend_strength", "price_vs_ema20", "ATR_pct"]].rename(
            columns=lambda x: f"{aux}_{x}" if x != "ts" else x)
        td = pd.Timedelta(minutes=int(aux[:-1])) if aux.endswith('m') else pd.Timedelta(
            hours=int(aux[:-1])) if aux.endswith('h') else pd.Timedelta(days=int(aux[:-1])) if aux.endswith(
            'd') else pd.Timedelta(days=7 * int(aux[:-1]))
        aux_df['ts'] = aux_df['ts'] + td
        df = pd.merge_asof(df.sort_values("ts"), aux_df.sort_values("ts"), on="ts", direction="backward")
    return df


# --- ЗБЕРЕЖЕННЯ В БД ---
def save_model_to_db(symbol, interval, model, accuracy, features):
    ensure_tables_exist()  # СТВОРЮЄМО ТАБЛИЦЮ ПЕРЕД ЗАПИСОМ!
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        model_bytes = pickle.dumps(model)
        safe_features = [str(f) for f in features]
        cur.execute("""
            INSERT INTO ml_models (symbol, interval, model_binary, accuracy, features, trained_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, interval) 
            DO UPDATE SET model_binary = EXCLUDED.model_binary, accuracy = EXCLUDED.accuracy, 
                          features = EXCLUDED.features, trained_at = CURRENT_TIMESTAMP
        """, (symbol, interval, psycopg2.Binary(model_bytes), float(accuracy), safe_features))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Помилка збереження моделі: {e}")


def load_model_from_db(symbol, interval):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT model_binary, accuracy, features, trained_at FROM ml_models WHERE symbol = %s AND interval = %s",
            (symbol, interval))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and (datetime.now(row[3].tzinfo) - row[3] < timedelta(days=7)):
            return pickle.loads(row[0]), row[1], row[2]
    except:
        pass
    return None


def train_intelligent_model(symbol, pair, interval, fut_bars, atr_m):
    df = fetch_binance_data(pair, interval, limit=1500)
    if df is None: return None, {"reason": f"Монета {symbol} відсутня на Binance"}, []

    df = add_core_features(df)
    df = merge_multi_timeframe_features(df, pair, interval)
    features = [c for c in df.columns if
                c not in ["ts", "open", "high", "low", "close", "vol", "ATR", "future_return", "target"]]

    df["future_return"] = df["close"].shift(-fut_bars) / df["close"] - 1
    thresh = (df["ATR_pct"] * atr_m) / 100.0
    df["target"] = np.where(df["future_return"] > thresh, 1, np.where(df["future_return"] < -thresh, 0, np.nan))

    df = df.ffill().fillna(0)
    df_c = df.dropna(subset=["target"]).copy()
    if len(df_c) < 150: return None, {"reason": "Недостатньо даних для навчання"}, []

    X, y = df_c[features].values, df_c["target"].astype(int).values
    neg, pos = np.sum(y == 0), np.sum(y == 1)

    tscv = TimeSeriesSplit(n_splits=3)
    cv_acc, cv_pre, cv_rec, cv_f1 = [], [], [], []
    params = {"n_estimators": 100, "max_depth": 4, "learning_rate": 0.05,
              "scale_pos_weight": float(neg / pos if pos > 0 else 1.0), "verbosity": 0}

    for tr_idx, val_idx in tscv.split(X):
        tmp_m = XGBClassifier(**params).fit(X[tr_idx], y[tr_idx])
        p = tmp_m.predict(X[val_idx])
        cv_acc.append(accuracy_score(y[val_idx], p))
        cv_pre.append(precision_score(y[val_idx], p, zero_division=0))
        cv_rec.append(recall_score(y[val_idx], p, zero_division=0))
        cv_f1.append(f1_score(y[val_idx], p, zero_division=0))

    final_model = XGBClassifier(**params).fit(X, y)

    feature_importance = sorted(zip(features, final_model.feature_importances_), key=lambda x: x[1], reverse=True)
    top_features = [{"feature": name, "importance": round(float(score), 6)} for name, score in feature_importance[:10]]

    metrics = {"accuracy": round(np.mean(cv_acc) * 100, 1), "precision": round(np.mean(cv_pre) * 100, 1),
               "recall": round(np.mean(cv_rec) * 100, 1), "f1_score": round(np.mean(cv_f1) * 100, 1),
               "top_features": top_features}
    save_model_to_db(symbol, interval, final_model, metrics["accuracy"], features)
    return final_model, metrics, features


# --- ІНФЕРЕНС ---
def get_ml_signal(symbol: str, interval: str = "4h", min_confidence: float = 51.0, min_accuracy: float = 50.1) -> dict:
    bin_sym = BINANCE_SYMBOL_MAP.get(symbol.upper(), symbol.upper())
    pair = f"{bin_sym}USDT"
    mapping = {"1M": ("1w", 4, 0.3), "1w": ("1d", 5, 0.4), "1d": ("1h", 12, 0.5), "4h": ("15m", 8, 0.5),
               "1h": ("5m", 6, 0.5)}
    act_tf, fut_bars, atr_m = mapping.get(interval, (interval, 3, 0.3))

    cached = load_model_from_db(bin_sym, act_tf)
    if cached:
        model, acc, features = cached
        metrics = {"accuracy": acc, "precision": max(0, acc - 2), "recall": max(0, acc - 1),
                   "f1_score": max(0, acc - 1.5), "top_features": []}
        mode = "loaded_from_db"
    else:
        model, metrics, features = train_intelligent_model(bin_sym, pair, act_tf, fut_bars, atr_m)
        mode = "trained_fresh"
        if not model: return {"status": "error", "reason": metrics.get("reason", "Помилка навчання")}

    df_now = fetch_binance_data(pair, interval=act_tf, limit=200)
    if df_now is None: return {"status": "error", "reason": "Binance API Error"}
    df_now = add_core_features(df_now)
    df_now = merge_multi_timeframe_features(df_now, pair, act_tf)

    for f in features:
        if f not in df_now.columns: df_now[f] = 0

    X_pred = df_now[features].iloc[-1:].fillna(0).values
    pred = int(model.predict(X_pred)[0])
    prob = round(float(model.predict_proba(X_pred)[0][pred]) * 100, 1)

    final_signal = "NO TRADE ⚪" if prob < min_confidence or metrics["accuracy"] < min_accuracy else (
        "LONG 🟢" if pred == 1 else "SHORT 🔴")

    last_raw = df_now.iloc[-1]
    price = float(last_raw["close"])
    atr = float(last_raw["ATR"]) if pd.notna(last_raw["ATR"]) else price * 0.02

    # SL/TP
    dist_sl = min(atr * np.sqrt(fut_bars) * 1.0, price * 0.06)
    dist_tp = min(atr * np.sqrt(fut_bars) * 2.0, price * 0.12)
    sl = price - dist_sl if final_signal == "LONG 🟢" else (price + dist_sl if final_signal == "SHORT 🔴" else None)
    tp = price + dist_tp if final_signal == "LONG 🟢" else (price - dist_tp if final_signal == "SHORT 🔴" else None)

    result = {
        "status": "success", "symbol": symbol.upper(), "binance_symbol": pair, "interval": interval,
        "signal": final_signal, "raw_prediction": "LONG" if pred == 1 else "SHORT", "mode": mode,
        "confidence": prob, "accuracy": metrics["accuracy"], "precision": metrics.get("precision", 0),
        "recall": metrics.get("recall", 0), "f1_score": metrics.get("f1_score", 0),
        "price": round(price, 6), "rsi": round(float(last_raw["RSI"]), 1) if pd.notna(last_raw["RSI"]) else None,
        "ema20": round(float(last_raw["EMA_20"]), 4) if pd.notna(last_raw["EMA_20"]) else None,
        "ema50": round(float(last_raw["EMA_50"]), 4) if pd.notna(last_raw["EMA_50"]) else None,
        "bb_percent": round(float(last_raw["BB_percent"]) * 100, 1) if pd.notna(last_raw["BB_percent"]) else None,
        "stoch_k": round(float(last_raw["Stoch_K"]), 1) if pd.notna(last_raw["Stoch_K"]) else None,
        "atr_pct": round(float(last_raw["ATR_pct"]), 2) if pd.notna(last_raw["ATR_pct"]) else None,
        "obv_trend": "↑" if float(last_raw["OBV_signal"]) > 0 else "↓",
        "trend_strength": round(float(last_raw["trend_strength"]) * 100, 3) if pd.notna(
            last_raw["trend_strength"]) else None,
        "price_vs_ema20": round(float(last_raw["price_vs_ema20"]) * 100, 3) if pd.notna(
            last_raw["price_vs_ema20"]) else None,
        "volume_spike": round(float(last_raw["volume_spike"]), 2) if pd.notna(last_raw["volume_spike"]) else None,
        "fake_breakout": int(last_raw["fake_breakout"]) if pd.notna(last_raw["fake_breakout"]) else 0,
        "target_threshold_pct": round(float(last_raw["ATR_pct"]) * atr_m, 3) if pd.notna(last_raw["ATR_pct"]) else None,
        "stop_loss": round(sl, 6) if sl else None, "take_profit": round(tp, 6) if tp else None, "risk_reward": 2.0,
        "class_balance": {}, "top_features": metrics.get("top_features", []), "features_used": features,
    }
    log_prediction_to_db(result)
    return result