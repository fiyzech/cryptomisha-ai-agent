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
from sklearn.metrics import accuracy_score

warnings.filterwarnings("ignore")
load_dotenv()


# --- ПІДКЛЮЧЕННЯ ДО БД ---
def get_db_connection():
    try:
        return psycopg2.connect(
            dbname=st.secrets["POSTGRES_DB"],
            user=st.secrets["POSTGRES_USER"],
            password=st.secrets["POSTGRES_PASSWORD"],
            host=st.secrets["DB_HOST"],
            port=st.secrets["DB_PORT"]
        )
    except:
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
    # Перевірка наявності необхідних колонок
    required = ["open", "high", "low", "close", "vol"]
    for col in required:
        if col not in df.columns:
            # Якщо колонки мають короткі назви (o, h, l, c, v)
            mapping = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "vol"}
            df = df.rename(columns=mapping)
            break

    df["RSI"] = calculate_rsi(df["close"])
    df["RSI_slope"] = df["RSI"].diff(3)
    macd = df["close"].ewm(span=12).mean() - df["close"].ewm(span=26).mean()
    df["MACD_hist"] = macd - macd.ewm(span=9).mean()
    df["EMA_20"] = df["close"].ewm(span=20).mean()
    df["EMA_50"] = df["close"].ewm(span=50).mean()
    df["EMA_200"] = df["close"].ewm(span=200).mean()
    df["EMA_cross"] = df["EMA_20"] - df["EMA_50"]

    std = df["close"].rolling(20).std()
    sma = df["close"].rolling(20).mean()
    df["BB_pct"] = (df["close"] - (sma - 2 * std)) / (4 * std).replace(0, np.nan)

    df["OBV"] = (np.where(df["close"] > df["close"].shift(), df["vol"],
                          np.where(df["close"] < df["close"].shift(), -df["vol"], 0))).cumsum()
    df["OBV_sig"] = df["OBV"] - df["OBV"].ewm(span=20).mean()

    # ATR та ATR_pct (Ось тут була помилка KeyError)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()],
        axis=1).max(axis=1)
    df["ATR_pct"] = (tr.rolling(14).mean() / df["close"]) * 100

    for c in ["RSI", "MACD_hist", "BB_pct", "EMA_cross"]:
        df[f"{c}_lag1"] = df[c].shift(1)
        df[f"{c}_lag2"] = df[c].shift(2)
    return df


def get_aux_intervals(base):
    return {"1h": ["5m", "15m"], "4h": ["15m", "1h"], "1d": ["1h", "4h"]}.get(base, ["1h"])


def merge_multi_timeframe(df, symbol, base_interval):
    for aux in get_aux_intervals(base_interval):
        res = requests.get("https://data-api.binance.vision/api/v3/klines",
                           params={"symbol": f"{symbol}USDT", "interval": aux, "limit": 500})
        if res.status_code == 200:
            raw_data = res.json()
            aux_df = pd.DataFrame(raw_data).iloc[:, :6].astype(float)
            aux_df.columns = ["ts", "open", "high", "low", "close", "vol"]
            aux_df = add_core_features(aux_df)

            keep_cols = ["ts", "RSI", "MACD_hist", "EMA_cross", "BB_pct", "ATR_pct"]
            aux_df = aux_df[keep_cols].rename(columns=lambda x: f"{aux}_{x}" if x != "ts" else x)
            aux_df["ts"] = pd.to_datetime(aux_df["ts"], unit="ms")

            df = df.sort_values("ts")
            aux_df = aux_df.sort_values("ts")
            df = pd.merge_asof(df, aux_df, on="ts", direction="backward")
    return df


# --- РОБОТА З МОДЕЛЯМИ ---
def save_model_to_db(symbol, interval, model, accuracy, features):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        model_bytes = pickle.dumps(model)
        query = """
            INSERT INTO ml_models (symbol, interval, model_binary, accuracy, features, trained_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, interval) 
            DO UPDATE SET model_binary = EXCLUDED.model_binary, accuracy = EXCLUDED.accuracy, 
                          features = EXCLUDED.features, trained_at = CURRENT_TIMESTAMP
        """
        cur.execute(query, (symbol, interval, psycopg2.Binary(model_bytes), accuracy, features))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB Save Error: {e}")


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
        if row:
            trained_at = row[3]
            if datetime.now(trained_at.tzinfo) - trained_at < timedelta(days=7):
                return pickle.loads(row[0]), row[1], row[2]
    except:
        pass
    return None


# --- ТРЕНУВАННЯ ---
def train_intelligent_model(symbol, interval, fut_bars, atr_m):
    res = requests.get("https://data-api.binance.vision/api/v3/klines",
                       params={"symbol": f"{symbol}USDT", "interval": interval, "limit": 1500})
    if res.status_code != 200: return None, 0, []

    df = pd.DataFrame(res.json()).iloc[:, :6].astype(float)
    df.columns = ["ts", "open", "high", "low", "close", "vol"]
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")

    df = add_core_features(df)
    df = merge_multi_timeframe(df, symbol, interval)

    features = [c for c in df.columns if c not in ["ts", "open", "high", "low", "close", "vol"]]

    df["target"] = np.where(df["close"].shift(-fut_bars) > df["close"] * (1 + (df["ATR_pct"] * atr_m / 100)), 1,
                            np.where(df["close"].shift(-fut_bars) < df["close"] * (1 - (df["ATR_pct"] * atr_m / 100)),
                                     0, np.nan))

    df_c = df.dropna(subset=features + ["target"])
    if len(df_c) < 200: return None, 0, []

    X, y = df_c[features].values, df_c["target"].astype(int).values
    neg, pos = np.sum(y == 0), np.sum(y == 1)

    model = XGBClassifier(n_estimators=150, max_depth=4, learning_rate=0.05,
                          scale_pos_weight=float(neg / pos if pos > 0 else 1), verbosity=0)
    model.fit(X, y)

    acc = round(accuracy_score(y, model.predict(X)) * 100, 1)
    save_model_to_db(symbol, interval, model, acc, features)
    return model, acc, features


# --- ГОЛОВНА ФУНКЦІЯ ---
def get_ml_signal(symbol, interval="4h"):
    bin_sym = BINANCE_SYMBOL_MAP.get(symbol.upper(), symbol.upper())
    mapping = {"1h": ("5m", 6, 0.5), "4h": ("15m", 8, 0.5), "1d": ("1h", 12, 0.5)}
    act_tf, fut_bars, atr_m = mapping.get(interval, ("15m", 8, 0.5))

    data = load_model_from_db(bin_sym, interval)
    if data:
        model, acc, features = data
    else:
        model, acc, features = train_intelligent_model(bin_sym, act_tf, fut_bars, atr_m)

    if not model: return {"status": "error", "reason": "Data error"}

    res = requests.get("https://data-api.binance.vision/api/v3/klines",
                       params={"symbol": f"{bin_sym}USDT", "interval": act_tf, "limit": 200})
    df_now = pd.DataFrame(res.json()).iloc[:, :6].astype(float)
    df_now.columns = ["ts", "open", "high", "low", "close", "vol"]
    df_now["ts"] = pd.to_datetime(df_now["ts"], unit="ms")
    df_now = add_core_features(df_now)
    df_now = merge_multi_timeframe(df_now, bin_sym, act_tf)

    X_now = df_now[features].iloc[-1:].fillna(0).values
    pred = int(model.predict(X_now)[0])
    prob = round(float(model.predict_proba(X_now)[0][pred]) * 100, 1)

    price = df_now["close"].iloc[-1]
    signal = "LONG 🟢" if pred == 1 else "SHORT 🔴"

    # Логування прогнозу в модельний журнал
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO model_predictions (symbol, interval, signal, price, confidence, accuracy) VALUES (%s, %s, %s, %s, %s, %s)",
            (symbol.upper(), interval, signal, price, prob, acc))
        conn.commit();
        cur.close();
        conn.close()
    except:
        pass

    return {
        "status": "success", "symbol": symbol.upper(), "interval": interval, "signal": signal,
        "confidence": prob, "accuracy": acc, "price": price,
        "rsi": round(df_now["RSI"].iloc[-1], 1), "ema20": df_now["EMA_20"].iloc[-1],
        "bb_percent": round(df_now["BB_pct"].iloc[-1] * 100, 1),
        "obv_trend": "↑" if df_now["OBV_sig"].iloc[-1] > 0 else "↓"
    }