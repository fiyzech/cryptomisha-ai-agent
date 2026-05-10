import os
import re
import json
import html
import requests
import psycopg2
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from ml_engine import get_ml_signal

load_dotenv()

DB_USER = st.secrets.get("POSTGRES_USER", os.getenv("POSTGRES_USER"))
DB_PASSWORD = st.secrets.get("POSTGRES_PASSWORD", os.getenv("POSTGRES_PASSWORD"))
DB_HOST = st.secrets.get("DB_HOST", os.getenv("DB_HOST"))
DB_PORT = st.secrets.get("DB_PORT", os.getenv("DB_PORT"))
DB_NAME = st.secrets.get("POSTGRES_DB", os.getenv("POSTGRES_DB"))

st.set_page_config(
    page_title="CryptoMisha AI",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
MODEL_NAME = "llama-3.3-70b-versatile"
NEWSAPI_KEY = st.secrets.get("NEWSAPI_KEY", os.getenv("NEWSAPI_KEY", ""))

TIMEFRAME_OPTIONS = {
    "1 година": "1h",
    "4 години": "4h",
    "1 день": "1d",
    "1 тиждень": "1w",
    "1 місяць": "1M"
}

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap');

:root {
    --bg: #010315;
    --surface: #050506;
    --border: rgba(255, 255, 255, 0.1);
    --accent: #C38BFF;
    --accent2: #8348C1;
    --text: #FFFFFF;
    --muted: #A3A4B0;
    --red: #FF3B30;
    --green: #00E676;
    --yellow: #fbbf24;
}

html, body, .stApp, p, div, span, li, a, button, input, select {
    font-family: 'Montserrat', sans-serif !important;
    color: var(--text);
}

.stApp { background-color: var(--bg) !important; }

.material-symbols-rounded, .material-icons, summary span { 
    font-family: 'Material Symbols Rounded', 'Material Icons', sans-serif !important; 
}

.hint, .ml-acc, .card-title, .msg-label, .price-big, .chip-label { 
    font-family: 'Space Mono', monospace !important; 
}

#MainMenu, footer, header { visibility: hidden; }

.block-container { padding: 2rem 2rem 4rem; max-width: 1100px; }

.hero-title { 
    font-size: 2.8rem; font-weight: 800; color: #FFFFFF; margin-bottom: 0; display: flex; align-items: center; gap: 12px; 
}
.hero-title::before { content: '✦'; color: #8348C1; }
.hero-sub { color: var(--muted); font-size: 0.85rem; margin-bottom: 2rem; }

.card, .ml-card, .news-card {
    background: var(--surface) !important; 
    border-radius: 16px; 
    padding: 1.5rem; 
    margin-bottom: 1.5rem; 
    position: relative;
    box-shadow: 0 20px 70px rgba(131,72,193,0.10), 0 8px 25px rgba(0,0,0,0.35);
}
.card::before, .ml-card::before, .news-card::before {
    content: ""; position: absolute; inset: 0; border-radius: 16px; padding: 1px; 
    background: linear-gradient(90deg, rgba(82,46,139,0.32), rgba(179,179,179,0.32));
    -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0); 
    -webkit-mask-composite: xor; mask-composite: exclude; pointer-events: none;
}

.card-title { font-size: 0.85rem; font-weight: 500; color: var(--muted); margin-bottom: 0.5rem; display: flex; justify-content: space-between; }
.price-big { font-size: 2.6rem; font-weight: 800; color: var(--green); }
.change-pos { color: var(--green) !important; font-weight: 600; }
.change-neg { color: var(--red) !important; font-weight: 600; }

.msg-user { background: rgba(131,72,193,0.1); border: 1px solid rgba(131,72,193,0.3); border-radius: 12px; padding: 1rem 1.2rem; margin: 0.75rem 0; font-size: 0.95rem; }
.msg-ai { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem 1.2rem; margin: 0.75rem 0; font-size: 0.95rem; line-height: 1.7; box-shadow: 0 8px 25px rgba(0,0,0,0.2); }
.msg-label { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 0.5rem; }
.msg-label-ai { color: var(--accent); } 
.msg-label-user { color: #FFFFFF; }

.stat-row { background: transparent; border-top: 1px solid var(--border); padding-top: 1rem; display: flex; gap: 1.5rem; flex-wrap: wrap; margin-top: 1rem; }
.stat-chip { display: flex; flex-direction: column; }
.chip-label { color: var(--muted); font-size: 13px; margin-bottom: 4px; }
.stat-chip span:last-child { color: var(--text); font-weight: 600; font-size: 15px; }

.ml-signal-long  { color: var(--green) !important; font-size: 1.8rem; font-weight: 800 !important; }
.ml-signal-short { color: var(--red) !important; font-size: 1.8rem; font-weight: 800 !important; }
.ml-signal-neutral { color: var(--yellow) !important; font-size: 1.8rem; font-weight: 800 !important; }
.ml-conf { font-size: 1rem; color: var(--muted); font-weight: 500; font-family: 'Montserrat', sans-serif !important; }
.ml-indicators { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 1rem; }

[data-tooltip] { position: relative; cursor: help; }
[data-tooltip]:hover::after {
    content: attr(data-tooltip); position: absolute; bottom: 130%; left: 50%; transform: translateX(-50%);
    background-color: #050506 !important; border: 1px solid #8348C1; color: #FFFFFF; padding: 10px 14px; border-radius: 8px; font-size: 12px; font-weight: 500; font-family: 'Montserrat', sans-serif !important; white-space: normal; width: max-content; max-width: 250px; z-index: 99999; box-shadow: 0 10px 30px rgba(0,0,0,0.8); line-height: 1.4; text-align: left;
}

.ml-chip { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 0.4rem 0.8rem; font-size: 0.8rem; transition: background 0.2s; }
.ml-chip:hover { background: rgba(131,72,193,0.15); border-color: #8348C1; }
.ml-chip-label { color: var(--muted); margin-right: 4px; }
.ml-acc { font-size: 0.75rem; color: var(--muted); margin-top: 0.8rem; }

.stTextInput > div > div > input, .stSelectbox > div > div { background-color: var(--surface) !important; border: 1px solid var(--border) !important; color: var(--text) !important; border-radius: 12px !important; }
.stTextInput > div > div > input:focus, .stSelectbox > div > div:focus-within { box-shadow: 0 0 0 1px #8348C1 !important; border-color: #8348C1 !important; }

[data-baseweb="popover"], [data-baseweb="popover"] > div, [data-testid="stVirtualDropdown"] { background-color: #050506 !important; border: 1px solid rgba(131,72,193,0.6) !important; border-radius: 12px !important; }
li[role="option"]:hover, li[role="option"][aria-selected="true"] { background-color: rgba(131,72,193,0.5) !important; color: #FFFFFF !important; }

div[data-testid="stExpander"], details[data-testid="stExpander"] { background-color: #050506 !important; border: 1px solid rgba(131,72,193,0.4) !important; border-radius: 12px !important; }

.stButton > button { background: linear-gradient(90deg, #2C1969 0%, #8348C1 50%, #C38BFF 100%) !important; color: #FFFFFF !important; border: none !important; border-radius: 28px !important; font-weight: 500 !important; }
.divider { border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }
.thinking { color: var(--muted); font-style: italic; animation: pulse 1.5s infinite; }
@keyframes pulse { 0%{opacity:.5} 50%{opacity:1} 100%{opacity:.5} }
</style>
""", unsafe_allow_html=True)


def safe_text(value) -> str:
    return "—" if value is None else html.escape(str(value)).replace("\n", "<br>")


def safe_price(value) -> str:
    if value is None: return "—"
    try:
        value = float(value)
    except:
        return "—"
    if value >= 1: return f"${value:,.2f}"
    if value >= 0.01: return f"${value:.4f}"
    return f"${value:.8f}"


def safe_url(url: str) -> str:
    if not url: return "#"
    return html.escape(url.strip(), quote=True) if url.startswith("http") else "#"


def fmt_price(v: float) -> str:
    return safe_price(v)


def fmt_large(v: float) -> str:
    try:
        v = float(v)
    except:
        return "—"
    if v >= 1e9: return f"${v / 1e9:.2f}B"
    if v >= 1e6: return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


COIN_ALIASES = {
    "BTC": ["btc", "bitcoin", "біткоїн", "биткоин"],
    "ETH": ["eth", "ethereum", "ефір", "ефіріум"],
    "SOL": ["sol", "solana", "солана"],
}


@st.cache_data(ttl=86400)
def get_all_valid_tickers():
    try:
        r = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=5)
        if r.status_code == 200:
            return set(
                [s.get("baseAsset").upper() for s in r.json().get("symbols", []) if s.get("quoteAsset") == "USDT"])
    except:
        pass
    return {"BTC", "ETH", "SOL", "BNB"}


def extract_coin_from_question(text: str):
    if not text: return None
    q = text.lower()
    for symbol, aliases in COIN_ALIASES.items():
        for alias in aliases:
            if re.search(fr'\b{alias}\b', q): return symbol
    valid_tickers = get_all_valid_tickers()
    words = text.replace("?", " ").replace(",", " ").replace(".", " ").split()
    for word in words:
        clean = word.upper().strip()
        if clean in valid_tickers: return clean
    return None


@st.cache_data(ttl=15, show_spinner=False)
def get_realtime_binance_price(symbol: str):
    if not symbol: return None
    pair = f"{symbol.upper().strip()}USDT"
    try:
        r = requests.get("https://data-api.binance.vision/api/v3/ticker/24hr", params={"symbol": pair}, timeout=7)
        if r.status_code == 200:
            data = r.json()
            return {
                "symbol": symbol.upper(), "pair": pair, "price": float(data.get("lastPrice", 0)),
                "change_24h": float(data.get("priceChangePercent", 0)), "high_24h": float(data.get("highPrice", 0)),
                "low_24h": float(data.get("lowPrice", 0)), "volume": float(data.get("quoteVolume", 0))
            }
    except:
        pass
    return None


def build_realtime_price_context(user_question: str):
    symbol = extract_coin_from_question(user_question)
    data = get_realtime_binance_price(symbol) if symbol else None
    if not data: return ""
    return f"\nВАЖЛИВО: Ціна {data['symbol']} зараз {fmt_price(data['price'])}, зміна за 24г {data['change_24h']:+.2f}%."


def rsi_color(rsi: float) -> str:
    if rsi >= 70: return "#FF3B30"
    if rsi <= 30: return "#00E676"
    return "#A3A4B0"


def get_signal_style(signal: str):
    if "LONG" in signal: return "ml-signal-long", "#00E676"
    if "SHORT" in signal: return "ml-signal-short", "#FF3B30"
    return "ml-signal-neutral", "#fbbf24"


@st.cache_data(ttl=86400)
def get_binance_tickers():
    return ["BTC", "ETH", "SOL", "SUI", "DOGE", "PEPE", "TON", "AVAX", "NEAR", "LINK"]


@st.cache_data(ttl=3600)
def search_coingecko_id(query: str):
    try:
        r = requests.get(f"{COINGECKO_BASE}/search?query={query}", timeout=10)
        if r.status_code == 200 and r.json().get("coins"):
            return r.json()["coins"][0].get("id"), r.json()["coins"][0].get("name")
    except:
        pass
    return None, None


@st.cache_data(ttl=30)
def get_coin_data(coin_id: str):
    try:
        r = requests.get(f"{COINGECKO_BASE}/coins/{coin_id}", params={"localization": "false", "tickers": "false"},
                         timeout=10)
        return r.json() if r.status_code == 200 else None
    except:
        return None


@st.cache_data(ttl=120)
def get_sparkline(coin_id: str):
    try:
        r = requests.get(f"{COINGECKO_BASE}/coins/{coin_id}/market_chart", params={"vs_currency": "usd", "days": "7"},
                         timeout=10)
        return [p[1] for p in r.json().get("prices", [])] if r.status_code == 200 else []
    except:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def get_ml_signal_cached(symbol: str, interval: str):
    return get_ml_signal(symbol, interval)


def translate_to_uk(text: str) -> str:
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params={"client": "gtx", "sl": "auto", "tl": "uk", "dt": "t", "q": text}, timeout=3)
        if r.status_code == 200: return "".join([s[0] for s in r.json()[0]])
    except:
        pass
    return text


@st.cache_data(ttl=1800)
def get_crypto_news(coin_name: str, symbol: str = ""):
    if not NEWSAPI_KEY: return []
    try:
        r = requests.get("https://newsapi.org/v2/everything",
                         params={"q": f'"{coin_name}" OR "{symbol}" crypto', "sortBy": "publishedAt", "language": "en",
                                 "pageSize": 3, "apiKey": NEWSAPI_KEY}, timeout=10)
        if r.status_code == 200:
            return [{"title": translate_to_uk(a.get("title", "")), "url": a.get("url", ""),
                     "source": {"name": a.get("source", {}).get("name", "Unknown")}} for a in
                    r.json().get("articles", [])]
    except:
        pass
    return []


def stream_ollama(prompt: str):
    GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", "")).strip()
    if not GROQ_API_KEY: yield "⚠️ Ключ GROQ_API_KEY не знайдено!"; return
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.25, "stream": True}
    try:
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data, stream=True,
                            timeout=30)
        for line in res.iter_lines():
            if line:
                d_line = line.decode("utf-8")
                if d_line.startswith("data: ") and d_line != "data: [DONE]":
                    try:
                        yield json.loads(d_line[6:])["choices"][0]["delta"].get("content", "")
                    except:
                        pass
    except Exception as e:
        yield f"⚠️ Помилка: {e}"


def build_analysis_prompt(name, symbol, price, change_24h, change_7d, cap, spark_info, news):
    n_t = "\n".join([f"- {n.get('title')}" for n in news]) if news else "Немає новин."
    return f"Ти Міша. Проаналізуй {name} ({symbol}): Ціна {fmt_price(price)}, 24г: {change_24h:+.2f}%. {spark_info}. Новини: {n_t}. Коротко, українською."


def build_chat_prompt(name, symbol, price, change_24h, news, history, user_q):
    h_t = "\n".join([f"{m['role']}: {m['content']}" for m in history[-6:]])
    n_t = "\n".join([f"- {n.get('title')}" for n in news]) if news else "Немає новин."
    r_ctx = build_realtime_price_context(user_q)
    return f"Ти Міша. Контекст: {name}, ціна {fmt_price(price)}, 24г {change_24h:+.2f}%. {r_ctx} Новини: {n_t}. Історія: {h_t}. Запит: {user_q}."


for key, default in [("messages", []), ("current_coin", None), ("coin_data", None), ("coin_prices", []),
                     ("cg_name", None), ("generate_new", False), ("selected_interval", "4h")]:
    if key not in st.session_state: st.session_state[key] = default


def handle_chat_submit():
    if st.session_state.chat_input_widget.strip():
        st.session_state.messages.append({"role": "user", "content": st.session_state.chat_input_widget})
        st.session_state.generate_new = True
    st.session_state.chat_input_widget = ""


st.markdown('<div class="hero-title">CryptoMisha AI</div>', unsafe_allow_html=True)
st.markdown(f'<div class="hero-sub">// Оснащено моделлю {MODEL_NAME}</div>', unsafe_allow_html=True)

col1, col2, col3 = st.columns([3, 1, 1])
with col1: user_ticker = (st.selectbox("Пошук монети", options=get_binance_tickers(), index=None,
                                       placeholder="🔍 Введи назву або тікер...",
                                       label_visibility="collapsed") or "").split(" ")[0]
with col2: selected_interval = TIMEFRAME_OPTIONS[
    st.selectbox("Таймфрейм", options=list(TIMEFRAME_OPTIONS.keys()), index=1, label_visibility="collapsed")]
with col3: analyze_btn = st.button("⚡ Аналізувати", use_container_width=True)

if user_ticker and (
        analyze_btn or (st.session_state.current_coin == user_ticker and st.session_state.get("ml_result"))):
    if analyze_btn or st.session_state.current_coin != user_ticker:
        with st.spinner(f"Збираю дані для {user_ticker}..."):
            cg_id, cg_name = search_coingecko_id(user_ticker)
            st.session_state.coin_data = get_coin_data(cg_id) if cg_id else None
            st.session_state.coin_prices = get_sparkline(cg_id) if cg_id else []
            st.session_state.current_coin = user_ticker
            st.session_state.cg_name = cg_name or user_ticker
            if analyze_btn:
                st.session_state.messages = []
                st.session_state.generate_new = False
                st.session_state.pop(f"analysis_{user_ticker}", None)

    data, prices, display_name = st.session_state.coin_data, st.session_state.coin_prices, st.session_state.cg_name
    price, change_24h, change_7d, cap, vol, low_24h, high_24h, spark_info = 0, 0, 0, 0, 0, 0, 0, ""
    rt_data = get_realtime_binance_price(user_ticker)

    if data:
        m = data.get("market_data", {})
        price, change_24h, change_7d = m.get("current_price", {}).get("usd", 0), m.get("price_change_percentage_24h",
                                                                                       0) or 0, m.get(
            "price_change_percentage_7d", 0) or 0
        cap, vol = m.get("market_cap", {}).get("usd", 0), m.get("total_volume", {}).get("usd", 0)
        rank, display_name = data.get("market_cap_rank", "—"), data.get("name", display_name)
        if rt_data: price, change_24h, vol = rt_data.get("price", price), rt_data.get("change_24h",
                                                                                      change_24h), rt_data.get("volume",
                                                                                                               vol)

        st.markdown(f"""
        <div class="card"><div class="card-title">{safe_text(display_name)} · #{safe_text(rank)} · {safe_text(user_ticker)}/USD</div><div class="price-big">{fmt_price(price)}</div>
        <div class="stat-row">
            <div class="stat-chip"><span class="chip-label">Зміна 24г</span><span class="{'change-pos' if change_24h >= 0 else 'change-neg'}">{change_24h:+.2f}%</span></div>
            <div class="stat-chip"><span class="chip-label">Зміна 7д</span><span class="{'change-pos' if change_7d >= 0 else 'change-neg'}">{change_7d:+.2f}%</span></div>
            <div class="stat-chip"><span class="chip-label">MCap</span><span>{fmt_large(cap)}</span></div>
            <div class="stat-chip"><span class="chip-label">Обсяг 24г</span><span>{fmt_large(vol)}</span></div>
        </div></div>""", unsafe_allow_html=True)
        if prices: spark_info = f"Тренд: {'висхідний' if prices[-1] > prices[0] else 'спадний'} ({((prices[-1] - prices[0]) / prices[0] * 100):+.1f}% за 7 днів)"
    elif rt_data:
        price, change_24h, vol = rt_data.get("price", 0), rt_data.get("change_24h", 0), rt_data.get("volume", 0)
        st.markdown(f"""
        <div class="card"><div class="card-title">{safe_text(user_ticker)} · Актуальні дані · {safe_text(user_ticker)}/USDT</div><div class="price-big">{fmt_price(price)}</div>
        <div class="stat-row">
            <div class="stat-chip"><span class="chip-label">Зміна 24г</span><span class="{'change-pos' if change_24h >= 0 else 'change-neg'}">{change_24h:+.2f}%</span></div>
            <div class="stat-chip"><span class="chip-label">Мін 24г</span><span>{fmt_price(rt_data.get('low_24h', 0))}</span></div>
            <div class="stat-chip"><span class="chip-label">Макс 24г</span><span>{fmt_price(rt_data.get('high_24h', 0))}</span></div>
            <div class="stat-chip"><span class="chip-label">Обсяг 24г</span><span>{fmt_large(vol)}</span></div>
        </div></div>""", unsafe_allow_html=True)

    components.html(f"""
    <div class="tradingview-widget-container" style="height:100%;width:100%; border-radius:16px; overflow:hidden; border: 1px solid rgba(255,255,255,0.1); margin-bottom: 1.5rem; box-shadow: 0 20px 70px rgba(131,72,193,0.10);">
      <div id="tv_misha" style="height:420px;width:100%"></div><script src="https://s3.tradingview.com/tv.js"></script>
      <script>new TradingView.widget({{"autosize":true,"symbol":"BINANCE:{user_ticker}USDT","interval":"{{"1h":"60","4h":"240","1d":"D","1w":"W","1M":"M"}.get(selected_interval,"240")}","timezone":"Europe/Kyiv","theme":"dark","style":"1","locale":"uk","backgroundColor":"#050506","gridColor":"rgba(255,255,255,0.05)","container_id":"tv_misha"}});</script>
    </div>""", height=420)

    with st.spinner(f"🧠 ML-модель аналізує ринок (оптимізований режим)..."):
        ml_result = get_ml_signal_cached(user_ticker, selected_interval)
        st.session_state.ml_result = ml_result

    if ml_result.get("status") == "success":
        signal_class, signal_border = get_signal_style(ml_result["signal"])
        rsi_c = rsi_color(ml_result.get("rsi"))
        bb_pct = ml_result.get("bb_percent")
        bb_text = f"{bb_pct}% ({'перекуп.' if bb_pct > 80 else 'перепрод.' if bb_pct < 20 else 'норма'})" if bb_pct else "—"
        obv_icon = "🟢" if ml_result.get("obv_trend") == "↑" else "🔴"

        st.markdown(f"""
<div class="ml-card" style="border-color: {signal_border};">
<div style="font-family:'Space Mono',monospace; font-size:.75rem; color:var(--muted); text-transform:uppercase; letter-spacing:1px; margin-bottom:.5rem;">⚙️ AI Прогноз · XGBoost · {safe_text(tf_label)}</div>
<div class="{signal_class}">{safe_text(ml_result['signal'])}<span class="ml-conf">&nbsp;{ml_result.get('confidence', '—')}% впевненості</span></div>
<div class="ml-indicators">
<div class="ml-chip" data-tooltip="Індекс відносної сили (RSI). Показує імпульс ціни. >70 - перекупленість (сигнал на продаж), <30 - перепроданість (сигнал на покупку)."><span class="ml-chip-label">RSI</span><span style="color:{rsi_c}">{safe_text(ml_result.get("rsi"))}</span></div>
<div class="ml-chip" data-tooltip="Експоненційна ковзна середня (20 періодів). Показує короткостроковий тренд. Ціна вище EMA20 - тренд бичачий."><span class="ml-chip-label">EMA20</span><span>{safe_price(ml_result.get('ema20'))}</span></div>
<div class="ml-chip" data-tooltip="Смуги Боллінджера (%B). Показує, де знаходиться ціна відносно смуг. >80% - близько до верхньої межі, <20% - близько до нижньої."><span class="ml-chip-label">BB%</span><span>{safe_text(bb_text)}</span></div>
<div class="ml-chip" data-tooltip="Балансовий обсяг (OBV). Відображає сукупний тиск покупців та продавців. Зелений = купують, Червоний = продають."><span class="ml-chip-label">OBV</span><span>{obv_icon} {safe_text(ml_result.get('obv_trend'))}</span></div>
</div>
<div class="ml-acc">📊 Accuracy (CV): <b>{ml_result.get('accuracy', '—')}%</b> &nbsp;·&nbsp; Precision: <b>{ml_result.get('precision', '—')}%</b> &nbsp;·&nbsp; Recall: <b>{ml_result.get('recall', '—')}%</b> &nbsp;·&nbsp; F1: <b>{ml_result.get('f1_score', '—')}%</b></div>
<div class="ml-acc">SL: <b>{safe_price(ml_result.get("stop_loss"))}</b> &nbsp;·&nbsp; TP: <b>{safe_price(ml_result.get("take_profit"))}</b></div>
</div>
""", unsafe_allow_html=True)
    else:
        st.error(f"❌ ML прогноз недоступний: {ml_result.get('reason', 'помилка')}")

    st.markdown('<hr class="divider">', unsafe_allow_html=True)

    with st.spinner("Шукаю новини..."):
        news_articles = get_crypto_news(display_name, user_ticker)
    st.markdown('<div class="card-title" style="margin-top: 1.5rem;">📰 Останні новини</div>', unsafe_allow_html=True)
    if news_articles:
        for a in news_articles: st.markdown(
            f'<div class="news-card"><div class="news-title"><a href="{safe_url(a.get("url", "#"))}" target="_blank">{safe_text(a.get("title", "Без заголовку"))}</a></div><div class="news-meta"><span>{safe_text(a.get("source", {}).get("name", "Unknown"))}</span></div></div>',
            unsafe_allow_html=True)
    else:
        st.markdown('<div class="hint">Новин не знайдено.</div>', unsafe_allow_html=True)

    st.markdown('<hr class="divider">', unsafe_allow_html=True)

    a_key = f"analysis_{user_ticker}"
    if analyze_btn or a_key not in st.session_state:
        p_holder = st.empty()
        p_holder.markdown(
            f'<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша · 📊 Аналіз</div><span class="thinking">Аналізую...</span></div>',
            unsafe_allow_html=True)
        f_ans = ""
        for c in stream_ollama(
                build_analysis_prompt(display_name, user_ticker, price, change_24h, change_7d, cap, spark_info,
                                      news_articles)):
            f_ans += c;
            p_holder.markdown(
                f'<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша · 📊 Аналіз</div>{safe_text(f_ans)}</div>',
                unsafe_allow_html=True)
        st.session_state[a_key] = f_ans
        st.session_state.messages = [{"role": "ai", "content": f_ans}]
        st.rerun()

    for m in st.session_state.messages:
        if m["role"] == "ai":
            st.markdown(
                f'<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша</div>{safe_text(m["content"])}</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div class="msg-user"><div class="msg-label msg-label-user">▸ Ти</div>{safe_text(m["content"])}</div>',
                unsafe_allow_html=True)

    if st.session_state.generate_new:
        c_holder = st.empty()
        c_holder.markdown(
            '<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша</div><span class="thinking">Думаю...</span></div>',
            unsafe_allow_html=True)
        f_ans = ""
        for c in stream_ollama(build_chat_prompt(display_name, user_ticker, price, change_24h, news_articles,
                                                 st.session_state.messages[:-1],
                                                 st.session_state.messages[-1]["content"])):
            f_ans += c;
            c_holder.markdown(
                f'<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша</div>{safe_text(f_ans)}</div>',
                unsafe_allow_html=True)
        st.session_state.messages.append({"role": "ai", "content": f_ans})
        st.session_state.generate_new = False
        st.rerun()

    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    st.text_input("Питання", placeholder="Напиши щось Міші...", key="chat_input_widget", on_change=handle_chat_submit,
                  label_visibility="collapsed")

    with st.expander("📜 Журнал логів з БД"):
        try:
            conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)
            df_logs = pd.read_sql_query(
                "SELECT symbol, interval, signal, price, confidence, accuracy, stop_loss, take_profit, created_at FROM model_predictions ORDER BY created_at DESC LIMIT 10",
                conn)
            if not df_logs.empty:
                df_logs['created_at'] = pd.to_datetime(df_logs['created_at']).dt.strftime('%Y-%m-%d %H:%M:%S')
                st.dataframe(df_logs, use_container_width=True, hide_index=True)
            else:
                st.write("База даних порожня. Зачекай першого успішного запису!")
            conn.close()
        except Exception as e:
            st.error(f"Помилка підключення до БД: {e}")