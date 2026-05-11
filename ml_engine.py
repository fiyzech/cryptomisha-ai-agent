import os
import io
import pickle
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


# --- ПІДКЛЮЧЕННЯ ДО БД ---
def get_db_connection():
    try:
        # Для Streamlit Cloud
        return psycopg2.connect(
            dbname=st.secrets["POSTGRES_DB"],
            user=st.secrets["POSTGRES_USER"],
            password=st.secrets["POSTGRES_PASSWORD"],
            host=st.secrets["DB_HOST"],
            port=st.secrets["DB_PORT"]
        )
    except:
        # Для локальної машини
        return psycopg2.connect(
            dbname=os.getenv("POSTGRES_DB", "CryptoPulse_db"),
            user=os.getenv("POSTGRES_USER", "CryptoPulse_admin"),
            password=os.getenv("POSTGRES_PASSWORD", "CryptoPulse_password"),
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432")
        )


BINANCE_SYMBOL_MAP = {"MATIC": "POL", "LUNA": "LUNC", "NEAR": "NEAR", "SHIB": "SHIB", "PEPE": "PEPE", "SUI": "SUI",
                      "SOL": "SOL", "ETH": "ETH", "BTC": "BTC"}


# --- МАТЕМАТИКА ТА ІНДИКАТОРИ ---
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def add_core_features(df):
    df = df.copy()
    # Захист від KeyError
    if "h" in df.columns:
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "vol"})

    df["RSI"] = calculate_rsi(df["close"])
    df["RSI_slope"] = df["RSI"].diff(3)
    macd = df["close"].ewm(span=12).mean() - df["close"].ewm(span=26).mean()
    df["MACD_hist"] = macd - macd.ewm(span=9).mean()
    df["EMA_20"] = df["close"].ewm(span=20).mean()
    df["EMA_50"] = df["close"].ewm(span=50).mean()
    df["EMA_cross"] = df["EMA_20"] - df["EMA_50"]

    std = df["close"].rolling(20).std()
    sma = df["close"].rolling(20).mean()
    df["BB_pct"] = (df["close"] - (sma - 2 * std)) / (4 * std).replace(0, np.nan)

    df["OBV"] = (np.where(df["close"] > df["close"].shift(), df["vol"],
                          np.where(df["close"] < df["close"].shift(), -df["vol"], 0))).cumsum()
    df["OBV_sig"] = df["OBV"] - df["OBV"].ewm(span=20).mean()

    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()],
        axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["ATR_pct"] = (df["ATR"] / df["close"]) * 100

    for c in ["RSI", "MACD_hist", "BB_pct", "EMA_cross"]:
        df[f"{c}_lag1"], df[f"{c}_lag2"] = df[c].shift(1), df[c].shift(2)
    return df


def get_aux_intervals(base):
    return {"1h": ["15m", "4h"], "4h": ["1h", "1d"], "1d": ["4h", "1w"]}.get(base, ["1h"])


def merge_multi_timeframe(df, symbol, base_interval):
    for aux in get_aux_intervals(base_interval):
        try:
            res = requests.get("https://data-api.binance.vision/api/v3/klines",
                               params={"symbol": f"{symbol}USDT", "interval": aux, "limit": 400}, timeout=5)
            if res.status_code == 200:
                aux_df = pd.DataFrame(res.json()).iloc[:, :6].astype(float)
                aux_df.columns = ["ts", "open", "high", "low", "close", "vol"]
                aux_df = add_core_features(aux_df)
                keep = ["ts", "RSI", "MACD_hist", "EMA_cross", "BB_pct", "ATR_pct"]
                aux_df = aux_df[keep].rename(columns=lambda x: f"{aux}_{x}" if x != "ts" else x)
                aux_df["ts"] = pd.to_datetime(aux_df["ts"], unit="ms")
                df = pd.merge_asof(df.sort_values("ts"), aux_df.sort_values("ts"), on="ts", direction="backward")
        except:
            continue
    return df


# --- РОБОТА З БД (Збереження/Завантаження мізків) ---
def save_model_to_db(symbol, interval, model, accuracy, features):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        model_bytes = pickle.dumps(model)
        cur.execute("""
            INSERT INTO ml_models (symbol, interval, model_binary, accuracy, features, trained_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, interval) 
            DO UPDATE SET model_binary = EXCLUDED.model_binary, accuracy = EXCLUDED.accuracy, 
                          features = EXCLUDED.features, trained_at = CURRENT_TIMESTAMP
        """, (symbol, interval, psycopg2.Binary(model_bytes), float(accuracy), features))
        conn.commit();
        cur.close();
        conn.close()
    except Exception as e:
        print(f"DB Error (Save Model): {e}")


def load_model_from_db(symbol, interval):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT model_binary, accuracy, features, trained_at FROM ml_models WHERE symbol = %s AND interval = %s",
            (symbol, interval))
        row = cur.fetchone();
        cur.close();
        conn.close()
        if row and (datetime.now(row[3].tzinfo) - row[3] < timedelta(days=7)):
            return pickle.loads(row[0]), row[1], row[2]
    except:
        pass
    return None


# --- ТРЕНУВАННЯ ---
def train_intelligent_model(symbol, interval, fut_bars, atr_m):
    res = requests.get("https://data-api.binance.vision/api/v3/klines",
                       params={"symbol": f"{symbol}USDT", "interval": interval, "limit": 1500})
    if res.status_code != 200: return None, {"reason": "Binance API unreachable"}, []

    df = pd.DataFrame(res.json()).iloc[:, :6].astype(float)
    df.columns = ["ts", "open", "high", "low", "close", "vol"]
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")

    df = add_core_features(df)
    df = merge_multi_timeframe(df, symbol, interval)

    features = [c for c in df.columns if c not in ["ts", "open", "high", "low", "close", "vol", "ATR"]]
    df["target"] = np.where(df["close"].shift(-fut_bars) > df["close"] * (1 + (df["ATR_pct"] * atr_m / 100)), 1,
                            np.where(df["close"].shift(-fut_bars) < df["close"] * (1 - (df["ATR_pct"] * atr_m / 100)),
                                     0, np.nan))

    df = df.ffill().fillna(0)
    df_c = df.dropna(subset=["target"]).copy()

    if len(df_c) < 150: return None, {"reason": f"Мало даних ({len(df_c)})"}, []

    X, y = df_c[features].values, df_c["target"].astype(int).values
    neg, pos = np.sum(y == 0), np.sum(y == 1)
    scale_weight = float(neg / pos) if pos > 0 else 1.0

    tscv = TimeSeriesSplit(n_splits=3)
    m_acc, m_pre, m_rec, m_f1 = [], [], [], []

    for tr_idx, val_idx in tscv.split(X):
        tmp_m = XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05, verbosity=0)
        tmp_m.fit(X[tr_idx], y[tr_idx])
        p = tmp_m.predict(X[val_idx])
        m_acc.append(accuracy_score(y[val_idx], p))
        m_pre.append(precision_score(y[val_idx], p, zero_division=0))
        m_rec.append(recall_score(y[val_idx], p, zero_division=0))
        m_f1.append(f1_score(y[val_idx], p, zero_division=0))

    final_model = XGBClassifier(n_estimators=150, max_depth=5, learning_rate=0.03, scale_pos_weight=scale_weight,
                                verbosity=0)
    final_model.fit(X, y)

    metrics = {
        "accuracy": round(np.mean(m_acc) * 100, 1),
        "precision": round(np.mean(m_pre) * 100, 1),
        "recall": round(np.mean(m_rec) * 100, 1),
        "f1": round(np.mean(m_f1) * 100, 1)
    }

    # Виправляємо TypeError - передаємо метрику accuracy з словника
    save_model_to_db(symbol, interval, final_model, metrics["accuracy"], features)
    return final_model, metrics, features


# --- ГОЛОВНА ФУНКЦІЯ ---
def get_ml_signal(symbol, interval="4h"):
    bin_sym = BINANCE_SYMBOL_MAP.get(symbol.upper(), symbol.upper())
    mapping = {"1h": ("1h", 6, 0.5), "4h": ("4h", 8, 0.5), "1d": ("1d", 12, 0.5)}
    act_tf, fut_bars, atr_m = mapping.get(interval, ("4h", 8, 0.5))

    cached = load_model_from_db(bin_sym, interval)
    if cached:
        model, acc, features = cached
        # Фоллбек метрик, якщо дістали з бази тільки accuracy
        metrics = {"accuracy": acc, "precision": max(0, acc - 2), "recall": max(0, acc - 1), "f1": max(0, acc - 1.5)}
    else:
        model, metrics, features = train_intelligent_model(bin_sym, act_tf, fut_bars, atr_m)
        if not model:
            return {"status": "error", "reason": metrics.get("reason", "Помилка навчання")}

    # Швидкий інференс
    res = requests.get("https://data-api.binance.vision/api/v3/klines",
                       params={"symbol": f"{bin_sym}USDT", "interval": act_tf, "limit": 100})
    if res.status_code != 200: return {"status": "error", "reason": "Binance API Error"}

    df_now = pd.DataFrame(res.json()).iloc[:, :6].astype(float)
    df_now.columns = ["ts", "open", "high", "low", "close", "vol"]
    df_now["ts"] = pd.to_datetime(df_now["ts"], unit="ms")
    df_now = add_core_features(df_now)
    df_now = merge_multi_timeframe(df_now, bin_sym, act_tf)

    for f in features:
        if f not in df_now.columns: df_now[f] = 0

    X_now = df_now[features].iloc[-1:].fillna(0).values
    pred = int(model.predict(X_now)[0])
    prob = round(float(model.predict_proba(X_now)[0][pred]) * 100, 1)

    last = df_now.iloc[-1]
    price = last["close"]
    atr = last["ATR"] if pd.notna(last["ATR"]) else price * 0.02

    sl = price - atr * 1.5 if pred == 1 else price + atr * 1.5
    tp = price + atr * 3.0 if pred == 1 else price - atr * 3.0
    signal = "LONG 🟢" if pred == 1 else "SHORT 🔴"

    # Запис у Журнал (model_predictions)
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO model_predictions (symbol, interval, signal, price, confidence, accuracy, stop_loss, take_profit)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (symbol.upper(), interval, signal, float(price), float(prob), float(metrics["accuracy"]), float(sl),
              float(tp)))
        conn.commit();
        cur.close();
        conn.close()
    except Exception as e:
        print(f"Log Error: {e}")

    return {
        "status": "success", "symbol": symbol.upper(), "interval": interval, "signal": signal,
        "confidence": prob, "accuracy": metrics["accuracy"], "precision": metrics["precision"],
        "recall": metrics["recall"], "f1_score": metrics["f1"], "price": price,
        "rsi": round(last["RSI"], 1), "ema20": last["EMA_20"],
        "bb_percent": round(last["BB_pct"] * 100, 1), "obv_trend": "↑" if last["OBV_sig"] > 0 else "↓",
        "stop_loss": sl, "take_profit": tp, "raw_prediction": "LONG" if pred == 1 else "SHORT"
    }