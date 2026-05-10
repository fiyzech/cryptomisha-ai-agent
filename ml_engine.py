import os
import re
import json
import html
import requests
import psycopg2
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from ml_engine import get_ml_signal, get_db_connection

# Змінні оточення для підключення до БД
DB_USER = os.getenv("POSTGRES_USER", "CryptoPulse_admin")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "CryptoPulse_password")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "CryptoPulse_db")

st.set_page_config(page_title="CryptoMisha AI", page_icon="✦", layout="wide", initial_sidebar_state="collapsed")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
MODEL_NAME = "llama-3.3-70b-versatile"
NEWSAPI_KEY = st.secrets.get("NEWSAPI_KEY", "")

TIMEFRAME_OPTIONS = {"1 година": "1h", "4 години": "4h", "1 день": "1d", "1 тиждень": "1w", "1 місяць": "1M"}

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap');
:root { --bg: #010315; --surface: #050506; --border: rgba(255, 255, 255, 0.1); --accent: #C38BFF; --accent2: #8348C1; --text: #FFFFFF; --muted: #A3A4B0; --red: #FF3B30; --green: #00E676; --yellow: #fbbf24; }
html, body, .stApp, p, div, span, li, a, button, input, select { font-family: 'Montserrat', sans-serif !important; color: var(--text); }
.stApp { background-color: var(--bg) !important; }
.hero-title { font-size: 2.8rem; font-weight: 800; color: #FFFFFF; margin-bottom: 0; display: flex; align-items: center; gap: 12px; }
.hero-title::before { content: '✦'; color: #8348C1; }
.card, .ml-card, .news-card { background: var(--surface) !important; border-radius: 16px; padding: 1.5rem; margin-bottom: 1.5rem; position: relative; box-shadow: 0 20px 70px rgba(131,72,193,0.10), 0 8px 25px rgba(0,0,0,0.35); border: 1px solid var(--border); }
.price-big { font-size: 2.6rem; font-weight: 800; color: var(--green); font-family: 'Space Mono'; }
.ml-signal-long { color: var(--green) !important; font-size: 1.8rem; font-weight: 800 !important; }
.ml-signal-short { color: var(--red) !important; font-size: 1.8rem; font-weight: 800 !important; }

/* TOOLTIPS */
[data-tooltip] { position: relative; cursor: help; }
[data-tooltip]:hover::after {
    content: attr(data-tooltip); position: absolute; bottom: 130%; left: 50%; transform: translateX(-50%);
    background-color: #050506 !important; border: 1px solid #8348C1; color: #FFFFFF; padding: 10px 14px; border-radius: 8px; font-size: 12px; z-index: 99999; width: max-content; max-width: 250px;
}
.ml-indicators { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 1rem; }
.ml-chip { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 0.4rem 0.8rem; font-size: 0.8rem; font-family: 'Space Mono'; }
.ml-acc { font-size: 0.75rem; color: var(--muted); margin-top: 0.8rem; font-family: 'Space Mono'; }
.stButton > button { background: linear-gradient(90deg, #2C1969 0%, #8348C1 50%, #C38BFF 100%) !important; color: #FFFFFF !important; border-radius: 28px !important; font-weight: 600 !important; height: 44px !important; }
.divider { border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }
</style>
""", unsafe_allow_html=True)


def safe_text(value) -> str: return "—" if value is None else html.escape(str(value)).replace("\n", "<br>")


def safe_price(value) -> str:
    if value is None: return "—"
    try:
        v = float(value)
        return f"${v:,.2f}" if v >= 1 else f"${v:.6f}"
    except:
        return "—"


@st.cache_data(ttl=86400)
def get_binance_tickers():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets",
                         params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 150, "page": 1})
        cg = {c['symbol'].upper(): c['name'] for c in r.json()}
        b = requests.get("https://api.binance.com/api/v3/exchangeInfo")
        valid = [f"{s['baseAsset']} ({cg[s['baseAsset']]})" for s in b.json()['symbols'] if
                 s['quoteAsset'] == 'USDT' and s['baseAsset'] in cg]
        return valid[:125]
    except:
        return ["BTC (Bitcoin)", "ETH (Ethereum)", "SOL (Solana)"]


def get_crypto_news(coin_name, symbol):
    if not NEWSAPI_KEY: return []
    try:
        r = requests.get("https://newsapi.org/v2/everything",
                         params={"q": f'"{coin_name}" OR "{symbol}" crypto', "pageSize": 3, "apiKey": NEWSAPI_KEY})
        return r.json().get("articles", [])
    except:
        return []


st.markdown('<div class="hero-title">CryptoMisha AI</div>', unsafe_allow_html=True)
st.markdown(
    '<div style="color:var(--muted); margin-bottom: 2rem;">// Потужний ML-аналіз та XGBoost прогнозування</div>',
    unsafe_allow_html=True)

col1, col2, col3 = st.columns([3, 1, 1])
with col1: user_selection = st.selectbox("Монета", options=get_binance_tickers(), index=None, placeholder="🔍 Пошук...")
with col2: tf_label = st.selectbox("Таймфрейм", options=list(TIMEFRAME_OPTIONS.keys()), index=1)
with col3: analyze_btn = st.button("⚡ Аналізувати")

if user_selection and analyze_btn:
    ticker = user_selection.split(" ")[0]
    interval = TIMEFRAME_OPTIONS[tf_label]

    with st.spinner(f"🧠 Міша аналізує {ticker}..."):
        res = get_ml_signal(ticker, interval)

    if res["status"] == "success":
        st.markdown(f"""
        <div class="card">
            <div style="color:var(--muted); font-size:0.8rem; margin-bottom:0.5rem;">АКТУАЛЬНА ЦІНА · {ticker}/USD</div>
            <div class="price-big">{safe_price(res['price'])}</div>
        </div>

        <div class="ml-card">
            <div style="font-family:'Space Mono'; font-size:.75rem; color:var(--muted); text-transform:uppercase;">⚙️ AI ПРОГНОЗ · XGBOOST · {tf_label.upper()}</div>
            <div class="{'ml-signal-long' if 'LONG' in res['signal'] else 'ml-signal-short'}">{res['signal']} <span style="font-size:1.2rem; color:var(--muted); font-weight:400;">{res['confidence']}% впевненості</span></div>

            <div class="ml-indicators">
                <div class="ml-chip" data-tooltip="RSI: Показує силу тренду. >70 - перекуплено, <30 - перепродано.">RSI {res['rsi']}</div>
                <div class="ml-chip" data-tooltip="EMA20: Середня ціна. Якщо ціна вище - тренд вгору.">EMA20 {safe_price(res['ema20'])}</div>
                <div class="ml-chip" data-tooltip="BB%: Положення ціни відносно волатильності.">BB% {res['bb_percent']}%</div>
                <div class="ml-chip" data-tooltip="OBV VOLUME: Тиск покупців/продавців.">OBV {'🟢' if res['obv_trend'] == '↑' else '🔴'} {res['obv_trend']}</div>
            </div>

            <div class="ml-acc">
                📊 Accuracy (CV): <b>{res['accuracy']}%</b> &nbsp;·&nbsp; Precision: <b>{res.get('precision', '—')}%</b> &nbsp;·&nbsp; Recall: <b>{res.get('recall', '—')}%</b> &nbsp;·&nbsp; F1: <b>{res.get('f1_score', '—')}%</b>
            </div>
            <div class="ml-acc" style="margin-top:5px;">
                SL: <b style="color:var(--red)">{safe_price(res['stop_loss'])}</b> &nbsp;·&nbsp; TP: <b style="color:var(--green)">{safe_price(res['take_profit'])}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

        components.html(f"""
            <div id="tv_chart" style="height:400px; border-radius:16px; overflow:hidden; border: 1px solid rgba(255,255,255,0.1);"></div>
            <script src="https://s3.tradingview.com/tv.js"></script>
            <script>new TradingView.widget({{"autosize":true,"symbol":"BINANCE:{ticker}USDT","interval":"{{"1h":"60","4h":"240","1d":"D"}.get(interval,"240")}","theme":"dark","style":"1","locale":"uk","container_id":"tv_chart"}});</script>
        """, height=400)

        # Новини
        articles = get_crypto_news(user_selection.split("(")[1].replace(")", ""), ticker)
        if articles:
            st.markdown('<hr class="divider">', unsafe_allow_html=True)
            st.markdown("### 📰 Останні новини")
            for a in articles:
                st.markdown(
                    f'<div class="news-card"><a href="{a["url"]}" target="_blank" style="text-decoration:none; font-weight:600;">{a["title"]}</a><br><small style="color:var(--muted);">{a["source"]["name"]}</small></div>',
                    unsafe_allow_html=True)

# Журнал з бази
with st.expander("📜 Журнал логів з БД (Supabase)"):
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(
            "SELECT symbol, interval, signal, price, confidence, accuracy, created_at FROM model_predictions ORDER BY created_at DESC LIMIT 10",
            conn)
        st.dataframe(df, use_container_width=True, hide_index=True)
        conn.close()
    except Exception as e:
        st.write(f"Помилка БД: {e}")