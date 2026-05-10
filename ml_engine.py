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

# Підключення до БД
try:
    DB_PARAMS = {
        "dbname": st.secrets["POSTGRES_DB"],
        "user": st.secrets["POSTGRES_USER"],
        "password": st.secrets["POSTGRES_PASSWORD"],
        "host": st.secrets["DB_HOST"],
        "port": st.secrets["DB_PORT"]
    }
except:
    DB_PARAMS = {
        "dbname": os.getenv("POSTGRES_DB"),
        "user": os.getenv("POSTGRES_USER"),
        "password": os.getenv("POSTGRES_PASSWORD"),
        "host": os.getenv("DB_HOST"),
        "port": os.getenv("DB_PORT")
    }

BINANCE_SYMBOL_MAP = {"MATIC": "POL", "LUNA": "LUNC", "NEAR": "NEAR", "SHIB": "SHIB", "PEPE": "PEPE", "SUI": "SUI",
                      "SOL": "SOL", "ETH": "ETH", "BTC": "BTC"}


# --- МАТЕМАТИЧНІ ФУНКЦІЇ (RSI, MACD тощо - залишаємо як були) ---
def calculate_rsi(s, p=14):
    d = s.diff();
    g = d.where(d > 0, 0.0).ewm(alpha=1 / p).mean();
    l = (-d.where(d < 0, 0.0)).ewm(alpha=1 / p).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))


def add_core_features(df):
    df = df.copy()
    df["RSI"] = calculate_rsi(df["close"])
    df["EMA_20"] = df["close"].ewm(span=20).mean()
    df["EMA_50"] = df["close"].ewm(span=50).mean()
    df["ATR"] = pd.concat(
        [df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()],
        axis=1).max(axis=1).rolling(14).mean()
    df["ATR_pct"] = df["ATR"] / df["close"] * 100
    df["BB_std"] = df["close"].rolling(20).std()
    df["BB_pct"] = (df["close"] - (df["close"].rolling(20).mean() - 2 * df["BB_std"])) / (4 * df["BB_std"])
    # Додаткові лаги для пам'яті
    for c in ["RSI", "BB_pct"]: df[f"{c}_lag1"] = df[c].shift(1)
    return df


# --- РОБОТА З МОДЕЛЯМИ В БД ---

def save_model_to_db(symbol, interval, model, accuracy, features):
    """Записує 'мозок' моделі прямо в PostgreSQL"""
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        # Перетворюємо об'єкт моделі в байти
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
        print(f"Помилка збереження моделі: {e}")


def load_model_from_db(symbol, interval):
    """Дістає модель з бази, якщо вона не старіша за 7 днів"""
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        cur.execute(
            "SELECT model_binary, accuracy, features, trained_at FROM ml_models WHERE symbol = %s AND interval = %s",
            (symbol, interval))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row:
            model_bytes, acc, features, trained_at = row
            # Перевірка на "протухання" (7 днів)
            if datetime.now(trained_at.tzinfo) - trained_at > timedelta(days=7):
                return None  # Треба перенавчити
            return pickle.loads(model_bytes), acc, features
    except:
        return None
    return None


# --- ТРЕНУВАННЯ ---

def train_new_model(symbol, interval, future_bars, atr_mult):
    """Скачує 2000 свічок і вчить новий розумний мозок"""
    url = "https://data-api.binance.vision/api/v3/klines"
    res = requests.get(url, params={"symbol": f"{symbol}USDT", "interval": interval, "limit": 1000}, timeout=10)
    if res.status_code != 200: return None, 0, []

    df = pd.DataFrame(res.json()).iloc[:, :6]
    df.columns = ["ts", "open", "high", "low", "close", "vol"]
    df[["open", "high", "low", "close", "vol"]] = df[["open", "high", "low", "close", "vol"]].astype(float)

    df = add_core_features(df)
    features = [c for c in df.columns if c not in ["ts", "open", "high", "low", "close", "vol", "ATR", "BB_std"]]

    df["target"] = np.where(df["close"].shift(-future_bars) > df["close"] * (1 + (df["ATR_pct"] * atr_mult / 100)), 1,
                            np.where(
                                df["close"].shift(-future_bars) < df["close"] * (1 - (df["ATR_pct"] * atr_mult / 100)),
                                0, np.nan))

    df_clean = df.dropna(subset=features + ["target"])
    if len(df_clean) < 300: return None, 0, []

    X, y = df_clean[features].values, df_clean["target"].astype(int).values
    model = XGBClassifier(n_estimators=150, max_depth=5, learning_rate=0.05, verbosity=0)
    model.fit(X, y)

    # Рахуємо приблизну точність для метаданих
    acc = round(accuracy_score(y, model.predict(X)) * 100, 1)

    # Зберігаємо результат у БД
    save_model_to_db(symbol, interval, model, acc, features)

    return model, acc, features


# --- ГОЛОВНА ФУНКЦІЯ ---

def get_ml_signal(symbol, interval="4h"):
    binance_sym = BINANCE_SYMBOL_MAP.get(symbol.upper(), symbol.upper())
    # Налаштування горизонту прогнозу
    mapping = {"1h": ("5m", 6, 0.5), "4h": ("15m", 8, 0.5), "1d": ("1h", 12, 0.5)}
    act_tf, fut_bars, atr_m = mapping.get(interval, ("15m", 8, 0.5))

    # 1. Намагаємось завантажити мозок з БД
    data = load_model_from_db(binance_sym, interval)
    if data:
        model, acc, features = data
        mode_text = "З банку пам'яті"
    else:
        # 2. Якщо в базі немає або стара - вчимо нову
        with st.spinner(f"🧠 Вчу нову модель для {symbol}..."):
            model, acc, features = train_new_model(binance_sym, act_tf, fut_bars, atr_m)
            mode_text = "Натреновано щойно"

    if not model: return {"status": "error", "reason": "Data error"}

    # 3. Швидкий прогноз на свіжих даних (всього 100 свічок)
    res = requests.get("https://data-api.binance.vision/api/v3/klines",
                       params={"symbol": f"{binance_sym}USDT", "interval": act_tf, "limit": 100})
    df_now = add_core_features(
        pd.DataFrame(res.json(), columns=["ts", "o", "h", "l", "c", "v", "ct", "qv", "nt", "tb", "tg", "i"]).iloc[
            :, :6].astype(float).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "vol"}))

    X_now = df_now[features].iloc[-1:].fillna(0).values
    pred = int(model.predict(X_now)[0])
    prob = round(float(model.predict_proba(X_now)[0][pred]) * 100, 1)

    price = df_now["close"].iloc[-1]
    signal = "LONG 🟢" if pred == 1 else "SHORT 🔴"

    result = {
        "status": "success", "symbol": symbol.upper(), "interval": interval, "signal": signal,
        "confidence": prob, "accuracy": acc, "price": price,
        "trained_mode": mode_text
    }

    # Логуємо прогноз (у твою іншу таблицю для журналу)
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO model_predictions (symbol, interval, signal, price, confidence, accuracy) VALUES (%s, %s, %s, %s, %s, %s)",
            (symbol.upper(), interval, signal, price, prob, acc))
        conn.commit();
        cur.close();
        conn.close()
    except:
        pass

    return result