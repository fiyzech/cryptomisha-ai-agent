import os
import re
import json
import html
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from ml_engine import get_ml_signal, get_db_connection

# Конфігурація
st.set_page_config(page_title="CryptoMisha AI", page_icon="✦", layout="wide", initial_sidebar_state="collapsed")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
MODEL_NAME = "llama-3.3-70b-versatile"
NEWSAPI_KEY = st.secrets.get("NEWSAPI_KEY", "")

# --- ПОВНІ СТИЛІ ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;800&family=Space+Mono&display=swap');
:root { --bg: #010315; --surface: #050506; --border: rgba(255,255,255,0.1); --accent: #C38BFF; --text: #FFFFFF; --muted: #A3A4B0; --red: #FF3B30; --green: #00E676; }
html, body, .stApp { background-color: var(--bg) !important; font-family: 'Montserrat', sans-serif !important; color: var(--text); }
.hero-title { font-size: 2.8rem; font-weight: 800; display: flex; align-items: center; gap: 12px; }
.hero-title::before { content: '✦'; color: #8348C1; }
.card { background: var(--surface); border-radius: 16px; padding: 1.5rem; margin-bottom: 1.5rem; border: 1px solid var(--border); box-shadow: 0 20px 70px rgba(131,72,193,0.1); }
.price-big { font-size: 2.6rem; font-weight: 800; color: var(--green); font-family: 'Space Mono', monospace; }
.ml-signal-long { color: var(--green); font-size: 1.8rem; font-weight: 800; }
.ml-signal-short { color: var(--red); font-size: 1.8rem; font-weight: 800; }

/* ПІДКАЗКИ (TOOLTIPS) */
[data-tooltip] { position: relative; cursor: help; }
[data-tooltip]:hover::after {
    content: attr(data-tooltip); position: absolute; bottom: 125%; left: 50%; transform: translateX(-50%);
    background: #0A0A0A; border: 1px solid #8348C1; color: #fff; padding: 10px 14px; border-radius: 8px;
    font-size: 12px; width: 250px; z-index: 1000; line-height: 1.4; box-shadow: 0 10px 30px rgba(0,0,0,0.8);
}

.ml-indicators { display: flex; gap: 0.8rem; margin-top: 1rem; flex-wrap: wrap; }
.ml-chip { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 0.5rem 0.8rem; font-family: 'Space Mono'; font-size: 0.85rem; }
.msg-ai { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.2rem; margin: 1rem 0; }
.msg-user { background: rgba(131,72,193,0.1); border: 1px solid rgba(131,72,193,0.3); border-radius: 12px; padding: 1rem; margin: 1rem 0; }
.stButton > button { background: linear-gradient(90deg, #2C1969, #8348C1, #C38BFF) !important; color: #fff !important; border-radius: 28px !important; border: none !important; height: 44px; font-weight: 600; width: 100%; }
.divider { border: none; border-top: 1px solid var(--border); margin: 2rem 0; }
.thinking { color: var(--muted); font-style: italic; animation: pulse 1.5s infinite; }
@keyframes pulse { 0%{opacity:.5} 50%{opacity:1} 100%{opacity:.5} }
</style>
""", unsafe_allow_html=True)


# --- ЛОГІКА ДАНИХ ---
def fmt_price(v):
    if v is None: return "—"
    return f"${v:,.2f}" if v >= 1 else f"${v:.6f}"


@st.cache_data(ttl=86400)
def get_top_125_tickers():
    try:
        r = requests.get(f"{COINGECKO_BASE}/coins/markets",
                         params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 150, "page": 1})
        cg_coins = {c['symbol'].upper(): c['name'] for c in r.json()}
        b_r = requests.get("https://api.binance.com/api/v3/exchangeInfo")
        b_symbols = {s['baseAsset'] for s in b_r.json()['symbols'] if s['quoteAsset'] == 'USDT'}
        final = []
        for sym, name in cg_coins.items():
            if sym in b_symbols: final.append(f"{sym} ({name})")
            if len(final) >= 125: break
        return final
    except:
        return ["BTC (Bitcoin)", "ETH (Ethereum)"]


def get_crypto_news(coin_name, symbol):
    if not NEWSAPI_KEY: return []
    try:
        r = requests.get("https://newsapi.org/v2/everything",
                         params={"q": f'"{coin_name}" OR "{symbol}" crypto', "language": "en", "pageSize": 3,
                                 "apiKey": NEWSAPI_KEY})
        return r.json().get("articles", [])
    except:
        return []


def stream_groq(prompt):
    key = st.secrets.get("GROQ_API_KEY")
    if not key: yield "⚠️ API ключ не знайдено!"; return
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    data = {"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.25, "stream": True}
    res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data, stream=True)
    for line in res.iter_lines():
        if line:
            line = line.decode("utf-8")
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    yield json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                except:
                    pass


# --- ІНТЕРФЕЙС ---
st.markdown('<div class="hero-title">CryptoMisha AI</div>', unsafe_allow_html=True)
st.markdown(
    '<div style="color:var(--muted); margin-bottom: 2rem;">// Machine Learning XGBoost + Groq LLM Intelligence</div>',
    unsafe_allow_html=True)

col1, col2, col3 = st.columns([3, 1, 1])
with col1: user_selection = st.selectbox("Монета", options=get_top_125_tickers(), index=None,
                                         placeholder="🔍 Вибери валюту для аналізу...")
with col2: tf_label = st.selectbox("Таймфрейм", options=["1 година", "4 години", "1 день"], index=1)
with col3: analyze_btn = st.button("⚡ Аналізувати")

if user_selection and analyze_btn:
    ticker = user_selection.split(" ")[0]
    tf_map = {"1 година": "1h", "4 години": "4h", "1 день": "1d"}
    interval = tf_map[tf_label]

    res = get_ml_signal(ticker, interval)

    if res["status"] == "success":
        st.markdown(f"""
        <div class="card">
            <div style="color:var(--muted); font-size:0.85rem; margin-bottom:0.5rem; font-family:'Space Mono'">АКТУАЛЬНА ЦІНА · {ticker}/USD</div>
            <div class="price-big">{fmt_price(res['price'])}</div>
        </div>

        <div class="card">
            <div style="color:var(--muted); font-size:0.85rem; margin-bottom:0.5rem; font-family:'Space Mono'">AI ПРОГНОЗ · XGBOOST · {tf_label.upper()}</div>
            <div class="{'ml-signal-long' if 'LONG' in res['signal'] else 'ml-signal-short'}">{res['signal']} <span style="font-size:1rem; color:var(--muted); font-weight:400;">({res['confidence']}% впевненості)</span></div>

            <div class="ml-indicators">
                <div class="ml-chip" data-tooltip="RSI (Relative Strength Index): Показує силу тренду. >70 — перекуплено (час продавати), <30 — перепродано (час купувати). Зараз: {res['rsi']}"><span style="color:var(--muted)">RSI</span> {res['rsi']}</div>
                <div class="ml-chip" data-tooltip="EMA20 (Exponential Moving Average): Середня ціна за останні 20 періодів. Якщо ціна вище — тренд висхідний."><span style="color:var(--muted)">EMA20</span> {fmt_price(res['ema20'])}</div>
                <div class="ml-chip" data-tooltip="BB% (Bollinger Bands %B): Показує положення ціни відносно волатильності. 100% — верхня межа, 0% — нижня.">{res['bb_percent']}% BB</div>
                <div class="ml-chip" data-tooltip="OBV (On-Balance Volume): Тренд об'єму торгів. Показує, чи накопичують великі гравці активи.">{res['obv_trend']} VOLUME</div>
            </div>
            <div style="margin-top:1.2rem; font-size:0.8rem; color:var(--muted); font-family:'Space Mono'">Історична точність моделі (Backtest): <b>{res['accuracy']}%</b></div>
        </div>
        """, unsafe_allow_html=True)

        components.html(f"""
            <div id="tv_chart" style="height:450px; border-radius:12px; overflow:hidden;"></div>
            <script src="https://s3.tradingview.com/tv.js"></script>
            <script>new TradingView.widget({{"autosize":true,"symbol":"BINANCE:{ticker}USDT","interval":"{{"1h":"60","4h":"240","1d":"D"}.get(interval,"240")}","theme":"dark","style":"1","locale":"uk","container_id":"tv_chart"}});</script>
        """, height=450)

        # AI Аналіз та Новини
        news = get_crypto_news(user_selection.split("(")[1].replace(")", ""), ticker)
        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        st.markdown('### ⬡ AI Аналітика та Новини')

        prompt = f"Ти Міша, фінансовий AI. Проаналізуй {ticker}. Ціна {res['price']}. Сигнал {res['signal']}. RSI {res['rsi']}. Новини: {str([n['title'] for n in news])}. Напиши короткий висновок українською (3-4 речення)."

        a_holder = st.empty()
        full_a = ""
        for chunk in stream_groq(prompt):
            full_a += chunk
            a_holder.markdown(f'<div class="msg-ai">{full_a}</div>', unsafe_allow_html=True)

# Журнал логів
with st.expander("📜 Останні прогнози в базі даних"):
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(
            "SELECT symbol, interval, signal, price, confidence, accuracy, created_at FROM model_predictions ORDER BY created_at DESC LIMIT 10",
            conn)
        st.dataframe(df, use_container_width=True, hide_index=True)
        conn.close()
    except:
        st.write("База даних ще не містить логів.")