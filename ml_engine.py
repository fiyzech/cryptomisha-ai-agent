import os
import csv
import json
import time
import warnings
import requests
import pandas as pd
import numpy as np
from datetime import datetime

from xgboost import XGBClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

warnings.filterwarnings("ignore")

BINANCE_SYMBOL_MAP = {
    "MATIC": "POL", "LUNA": "LUNC", "NEAR": "NEAR", "SHIB": "SHIB",
    "FLOKI": "FLOKI", "BONK": "BONK", "WIF": "WIF", "PEPE": "PEPE",
    "ARB": "ARB", "OP": "OP", "SUI": "SUI", "APT": "APT", "INJ": "INJ",
    "RENDER": "RENDER", "FET": "FET", "WLD": "WLD", "NOT": "NOT",
    "TON": "TON", "ICP": "ICP", "KAS": "KAS", "HBAR": "HBAR", "FIL": "FIL",
    "ALGO": "ALGO", "VET": "VET", "XLM": "XLM", "TRX": "TRX", "XMR": "XMR",
    "MKR": "MKR", "AAVE": "AAVE", "CRV": "CRV", "LDO": "LDO", "ATOM": "ATOM",
    "DOT": "DOT", "LTC": "LTC", "ADA": "ADA", "DOGE": "DOGE", "LINK": "LINK",
    "AVAX": "AVAX", "UNI": "UNI", "SOL": "SOL", "BNB": "BNB", "ETH": "ETH",
    "BTC": "BTC", "XRP": "XRP",
}


def log_prediction(data: dict):
    filename = "predictions_log.csv"
    file_exists = os.path.isfile(filename)

    with open(filename, mode='a', newline='', encoding='utf-8') as f:
        fieldnames = ['timestamp', 'symbol', 'interval', 'signal', 'price', 'confidence', 'accuracy', 'raw_prediction',
                      'stop_loss', 'take_profit']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'symbol': data.get('symbol', ''), 'interval': data.get('interval', ''),
            'signal': data.get('signal', ''), 'price': data.get('price', ''),
            'confidence': data.get('confidence', ''), 'accuracy': data.get('accuracy', ''),
            'raw_prediction': data.get('raw_prediction', ''), 'stop_loss': data.get('stop_loss', ''),
            'take_profit': data.get('take_profit', '')
        })


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram


def calculate_bollinger_bands(series: pd.Series, period: int = 20, std: int = 2):
    sma = series.rolling(window=period).mean()
    rolling_std = series.rolling(window=period).std()
    upper = sma + (rolling_std * std)
    lower = sma - (rolling_std * std)
    bandwidth = (upper - lower) / sma.replace(0, np.nan)
    percent_b = (series - lower) / (upper - lower).replace(0, np.nan)
    return upper, lower, bandwidth, percent_b


def calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    obv = np.where(close > close.shift(), volume, np.where(close < close.shift(), -volume, 0))
    return pd.Series(obv, index=close.index).cumsum()


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3):
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    d = k.rolling(window=d_period).mean()
    return k, d


def fetch_binance_data(symbol: str = "BTCUSDT", interval: str = "4h", limit: int = 3000):
    url = "https://data-api.binance.vision/api/v3/klines"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    all_data = []
    end_time = None
    req_limit = 1000
    remaining = limit

    while remaining > 0:
        current_limit = min(remaining, req_limit)
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

    df = pd.DataFrame(all_data)
    df = df.iloc[:, :6]
    df.columns = ["ts", "open", "high", "low", "close", "vol"]

    num_cols = ["open", "high", "low", "close", "vol"]
    df[num_cols] = df[num_cols].astype(float)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)

    return df.tail(limit).reset_index(drop=True)


def resolve_binance_symbol(symbol: str) -> str:
    sym = symbol.upper().replace("-", "").replace(" ", "")
    return BINANCE_SYMBOL_MAP.get(sym, sym)


def get_aux_intervals(base_interval: str):
    mapping = {"5m": ["15m", "1h"], "15m": ["1h", "4h"], "1h": ["4h", "1d"], "4h": ["1d", "1w"], "1d": ["1w", "1M"],
               "1w": ["1M"], "1M": ["1w"]}
    return mapping.get(base_interval, ["1d"])


def add_core_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["RSI"] = calculate_rsi(df["close"])
    df["RSI_slope"] = df["RSI"].diff(3)
    macd, macd_sig, macd_hist = calculate_macd(df["close"])
    df["MACD"], df["MACD_sig"], df["MACD_hist"] = macd, macd_sig, macd_hist
    df["EMA_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["EMA_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["EMA_200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["EMA_cross"] = df["EMA_20"] - df["EMA_50"]
    bb_upper, bb_lower, bb_bw, bb_pct = calculate_bollinger_bands(df["close"])
    df["BB_upper"], df["BB_lower"], df["BB_bandwidth"], df["BB_percent"] = bb_upper, bb_lower, bb_bw, bb_pct
    df["OBV"] = calculate_obv(df["close"], df["vol"])
    df["OBV_ema"] = df["OBV"].ewm(span=20, adjust=False).mean()
    df["OBV_signal"] = df["OBV"] - df["OBV_ema"]
    df["ATR"] = calculate_atr(df["high"], df["low"], df["close"])
    df["ATR_pct"] = df["ATR"] / df["close"] * 100
    stoch_k, stoch_d = calculate_stochastic(df["high"], df["low"], df["close"])
    df["Stoch_K"], df["Stoch_D"] = stoch_k, stoch_d
    df["Stoch_cross"] = df["Stoch_K"] - df["Stoch_D"]
    df["body_size"] = (df["close"] - df["open"]).abs() / df["open"] * 100
    df["upper_wick"] = (df["high"] - df[["close", "open"]].max(axis=1)) / df["open"] * 100
    df["lower_wick"] = (df[["close", "open"]].min(axis=1) - df["low"]) / df["open"] * 100
    df["vol_sma_20"] = df["vol"].rolling(20).mean()
    df["vol_ratio"] = df["vol"] / df["vol_sma_20"]
    df["volume_spike"] = df["vol"] / df["vol"].rolling(50).mean()
    df["momentum_3"] = df["close"].pct_change(3) * 100
    df["momentum_7"] = df["close"].pct_change(7) * 100
    df["trend_strength"] = (df["EMA_20"] - df["EMA_200"]).abs() / df["close"]
    df["price_vs_ema20"] = (df["close"] - df["EMA_20"]) / df["EMA_20"]
    rolling_high_20 = df["high"].rolling(20).max().shift(1)
    df["fake_breakout"] = ((df["high"] > rolling_high_20) & (df["close"] < df["open"])).astype(int)

    core_cols_to_lag = ["RSI", "MACD_hist", "price_vs_ema20", "Stoch_cross"]
    for col in core_cols_to_lag:
        df[f"{col}_lag1"] = df[col].shift(1)
        df[f"{col}_lag2"] = df[col].shift(2)
    return df


def build_aux_features(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    df = add_core_features(df)
    keep_cols = ["ts", "RSI", "MACD_hist", "EMA_cross", "trend_strength", "price_vs_ema20", "ATR_pct"]
    aux = df[keep_cols].copy()
    aux = aux.rename(
        columns={"RSI": f"{prefix}_RSI", "MACD_hist": f"{prefix}_MACD_hist", "EMA_cross": f"{prefix}_EMA_cross",
                 "trend_strength": f"{prefix}_trend_strength", "price_vs_ema20": f"{prefix}_price_vs_ema20",
                 "ATR_pct": f"{prefix}_ATR_pct"})
    return aux


def merge_multi_timeframe_features(base_df: pd.DataFrame, symbol: str, base_interval: str) -> pd.DataFrame:
    df = base_df.copy()
    aux_intervals = get_aux_intervals(base_interval)

    for aux_interval in aux_intervals:
        aux_raw = fetch_binance_data(f"{symbol}USDT", interval=aux_interval, limit=3000)
        if aux_raw is None or len(aux_raw) < 50: continue

        aux = build_aux_features(aux_raw, prefix=aux_interval)
        if aux_interval.endswith('m'):
            td = pd.Timedelta(minutes=int(aux_interval[:-1]))
        elif aux_interval.endswith('h'):
            td = pd.Timedelta(hours=int(aux_interval[:-1]))
        elif aux_interval.endswith('d'):
            td = pd.Timedelta(days=int(aux_interval[:-1]))
        elif aux_interval.endswith('w'):
            td = pd.Timedelta(days=7 * int(aux_interval[:-1]))
        elif aux_interval.endswith('M'):
            td = pd.Timedelta(days=30 * int(aux_interval[:-1]))
        else:
            td = pd.Timedelta(0)

        aux['ts'] = aux['ts'] + td
        df = pd.merge_asof(df.sort_values("ts"), aux.sort_values("ts"), on="ts", direction="backward")
    return df


def build_feature_list(df: pd.DataFrame):
    features = [
        "RSI", "RSI_slope", "MACD", "MACD_sig", "MACD_hist", "EMA_cross", "BB_bandwidth", "BB_percent", "OBV_signal",
        "ATR_pct", "Stoch_K", "Stoch_D", "Stoch_cross", "body_size", "upper_wick", "lower_wick", "vol_ratio",
        "volume_spike", "momentum_3", "momentum_7", "trend_strength", "price_vs_ema20", "fake_breakout", "RSI_lag1",
        "RSI_lag2", "MACD_hist_lag1", "MACD_hist_lag2", "price_vs_ema20_lag1", "price_vs_ema20_lag2",
        "Stoch_cross_lag1", "Stoch_cross_lag2"
    ]
    mtf_candidates = [
        "5m_RSI", "5m_MACD_hist", "5m_EMA_cross", "5m_trend_strength", "5m_price_vs_ema20", "5m_ATR_pct", "15m_RSI",
        "15m_MACD_hist", "15m_EMA_cross", "15m_trend_strength", "15m_price_vs_ema20", "15m_ATR_pct", "1h_RSI",
        "1h_MACD_hist", "1h_EMA_cross", "1h_trend_strength", "1h_price_vs_ema20", "1h_ATR_pct", "4h_RSI",
        "4h_MACD_hist", "4h_EMA_cross", "4h_trend_strength", "4h_price_vs_ema20", "4h_ATR_pct", "1d_RSI",
        "1d_MACD_hist", "1d_EMA_cross", "1d_trend_strength", "1d_price_vs_ema20", "1d_ATR_pct", "1w_RSI",
        "1w_MACD_hist", "1w_EMA_cross", "1w_trend_strength", "1w_price_vs_ema20", "1w_ATR_pct", "1M_RSI",
        "1M_MACD_hist", "1M_EMA_cross", "1M_trend_strength", "1M_price_vs_ema20", "1M_ATR_pct",
    ]
    features += [col for col in mtf_candidates if col in df.columns]
    return features


def choose_n_splits(n_samples: int) -> int:
    if n_samples >= 1000: return 7
    if n_samples >= 500: return 5
    if n_samples >= 250: return 4
    if n_samples >= 120: return 3
    return 2


def get_xgb_params(actual_fetch_interval: str, scale_weight: float) -> dict:
    params = {"tree_method": "hist", "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 2,
              "scale_pos_weight": scale_weight, "eval_metric": "logloss", "verbosity": 0, "random_state": 42}
    if actual_fetch_interval in ["5m", "15m"]:
        params.update({"max_depth": 3, "learning_rate": 0.05, "n_estimators": 100, "gamma": 0.2, "reg_lambda": 2.0,
                       "reg_alpha": 0.5})
    elif actual_fetch_interval in ["1h", "4h"]:
        params.update({"max_depth": 5, "learning_rate": 0.05, "n_estimators": 150, "gamma": 0.1, "reg_lambda": 1.0,
                       "reg_alpha": 0.1})
    else:
        params.update({"max_depth": 4, "learning_rate": 0.03, "n_estimators": 200, "gamma": 0.1, "reg_lambda": 1.5,
                       "reg_alpha": 0.2})
    return params


def train_and_evaluate(X_train, y_train, X_pred, features, future_bars, actual_fetch_interval):
    n_splits = choose_n_splits(len(X_train))
    tscv = TimeSeriesSplit(n_splits=n_splits, gap=future_bars)
    cv_accuracy, cv_precision, cv_recall, cv_f1 = [], [], [], []

    neg_cases, pos_cases = np.sum(y_train == 0), np.sum(y_train == 1)
    scale_weight = float(neg_cases / pos_cases) if pos_cases > 0 else 1.0

    xgb_params = get_xgb_params(actual_fetch_interval, scale_weight)

    for train_idx, val_idx in tscv.split(X_train):
        model = XGBClassifier(**xgb_params)
        model.fit(X_train[train_idx], y_train[train_idx])
        preds = model.predict(X_train[val_idx])
        cv_accuracy.append(accuracy_score(y_train[val_idx], preds))
        cv_precision.append(precision_score(y_train[val_idx], preds, zero_division=0))
        cv_recall.append(recall_score(y_train[val_idx], preds, zero_division=0))
        cv_f1.append(f1_score(y_train[val_idx], preds, zero_division=0))

    final_model = XGBClassifier(**xgb_params)
    final_model.fit(X_train, y_train)
    prediction = int(final_model.predict(X_pred)[0])
    prob = final_model.predict_proba(X_pred)[0]

    feature_importance = sorted(zip(features, final_model.feature_importances_), key=lambda x: x[1], reverse=True)
    top_features = [{"feature": name, "importance": round(float(score), 6)} for name, score in feature_importance[:10]]

    return {"prediction": prediction, "confidence": round(float(prob[prediction]) * 100, 1),
            "accuracy": round(float(np.mean(cv_accuracy)) * 100, 1),
            "precision": round(float(np.mean(cv_precision)) * 100, 1),
            "recall": round(float(np.mean(cv_recall)) * 100, 1), "f1_score": round(float(np.mean(cv_f1)) * 100, 1),
            "top_features": top_features}


def get_ml_signal(symbol: str, interval: str = "4h", min_confidence: float = 51.0, min_accuracy: float = 50.1) -> dict:
    binance_sym = resolve_binance_symbol(symbol)
    if interval == "1M":
        actual_fetch_interval, future_bars, atr_target_mult = "1w", 4, 0.3
    elif interval == "1w":
        actual_fetch_interval, future_bars, atr_target_mult = "1d", 5, 0.4
    elif interval == "1d":
        actual_fetch_interval, future_bars, atr_target_mult = "1h", 12, 0.5
    elif interval == "4h":
        actual_fetch_interval, future_bars, atr_target_mult = "15m", 8, 0.5
    elif interval == "1h":
        actual_fetch_interval, future_bars, atr_target_mult = "5m", 6, 0.5
    else:
        actual_fetch_interval, future_bars, atr_target_mult = interval, 3, 0.3

    df = fetch_binance_data(f"{binance_sym}USDT", interval=actual_fetch_interval, limit=3000)
    min_bars_required = 200
    if df is None or len(df) < min_bars_required: return {"status": "error",
                                                          "reason": f"Немає достатньо даних для {symbol} ({binance_sym}) на таймфреймі {actual_fetch_interval} (можливо, блокування Binance)"}

    df = add_core_features(df)
    df = merge_multi_timeframe_features(df, binance_sym, actual_fetch_interval)
    features = build_feature_list(df)

    last_features_row = df[features].iloc[-1]
    if last_features_row.isna().any(): return {"status": "error",
                                               "reason": f"Останній рядок містить NaN у фічах: {last_features_row[last_features_row.isna()].index.tolist()}"}

    X_pred = last_features_row.values.reshape(1, -1)
    df["future_return"] = df["close"].shift(-future_bars) / df["close"] - 1
    dynamic_threshold = (df["ATR_pct"] * atr_target_mult) / 100.0

    df["target_strict"] = np.where(df["future_return"] > dynamic_threshold, 1,
                                   np.where(df["future_return"] < -dynamic_threshold, 0, np.nan))
    df_strict = df.dropna(subset=features + ["target_strict"]).copy()
    df["target_fallback"] = np.where(df["future_return"] > dynamic_threshold, 1, 0)
    df_fallback = df.dropna(subset=features + ["target_fallback"]).copy()

    if len(df_strict) >= min_bars_required - future_bars:
        mode, df_train, target_col = "strict", df_strict, "target_strict"
    elif len(df_fallback) >= min_bars_required - future_bars:
        mode, df_train, target_col = "fallback_binary", df_fallback, "target_fallback"
    else:
        return {"status": "error", "reason": "Недостатньо розмічених даних для бектесту"}

    X_train, y_train = df_train[features].values, df_train[target_col].astype(int).values
    unique, counts = np.unique(y_train, return_counts=True)
    class_balance = {int(k): int(v) for k, v in zip(unique, counts)}

    metrics = train_and_evaluate(X_train, y_train, X_pred, features, future_bars, actual_fetch_interval)
    final_signal = "NO TRADE ⚪" if metrics["confidence"] < min_confidence or metrics["accuracy"] < min_accuracy else (
        "LONG 🟢" if metrics["prediction"] == 1 else "SHORT 🔴")

    last_raw = df.iloc[-1]
    price = float(last_raw["close"])
    atr = float(last_raw["ATR"]) if pd.notna(last_raw["ATR"]) else 0.0
    actual_sl_dist = min(atr * np.sqrt(future_bars) * 1.0, price * 0.06)
    actual_tp_dist = min(atr * np.sqrt(future_bars) * 2.0, price * 0.12)

    stop_loss, take_profit = None, None
    if final_signal == "LONG 🟢":
        stop_loss, take_profit = price - actual_sl_dist, price + actual_tp_dist
    elif final_signal == "SHORT 🔴":
        stop_loss, take_profit = price + actual_sl_dist, price - actual_tp_dist

    result = {
        "status": "success", "symbol": symbol.upper(), "binance_symbol": f"{binance_sym}USDT", "interval": interval,
        "signal": final_signal, "raw_prediction": "LONG" if metrics["prediction"] == 1 else "SHORT", "mode": mode,
        "confidence": metrics["confidence"], "accuracy": metrics["accuracy"], "precision": metrics["precision"],
        "recall": metrics["recall"], "f1_score": metrics["f1_score"],
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
        "target_threshold_pct": round(float(last_raw["ATR_pct"]) * atr_target_mult, 3) if pd.notna(
            last_raw["ATR_pct"]) else None,
        "stop_loss": round(stop_loss, 6) if stop_loss is not None else None,
        "take_profit": round(take_profit, 6) if take_profit is not None else None,
        "risk_reward": 2.0 if stop_loss is not None and take_profit is not None else None,
        "class_balance": class_balance, "top_features": metrics["top_features"], "features_used": features,
    }
    log_prediction(result)
    return result


if __name__ == "__main__":
    print(json.dumps(get_ml_signal("BTC", interval="1d"), indent=2, ensure_ascii=False))