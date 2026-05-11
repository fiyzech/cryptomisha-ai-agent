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

.ml-chip { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 0.4rem 0.8rem; font-size: 0.8rem; }
.ml-chip-label { color: var(--muted); margin-right: 4px; }
.ml-acc { font-size: 0.75rem; color: var(--muted); margin-top: 0.8rem; }

.stTextInput > div > div > input, .stSelectbox > div > div { background-color: var(--surface) !important; border: 1px solid var(--border) !important; color: var(--text) !important; border-radius: 12px !important; }
.stTextInput > div > div > input:focus, .stSelectbox > div > div:focus-within { box-shadow: 0 0 0 1px #8348C1 !important; border-color: #8348C1 !important; }

[data-baseweb="popover"], [data-baseweb="popover"] > div, [data-testid="stVirtualDropdown"], [data-testid="stVirtualDropdown"] > div, [data-testid="stVirtualDropdown"] ul, ul[role="listbox"], div[role="listbox"] { background-color: #050506 !important; }
[data-baseweb="popover"] > div, [data-testid="stVirtualDropdown"] { border: 1px solid rgba(131,72,193,0.6) !important; border-radius: 12px !important; box-shadow: 0 15px 40px rgba(0,0,0,0.9) !important; overflow: hidden !important; }
li[role="option"], div[role="option"] { background-color: transparent !important; color: #FFFFFF !important; font-family: 'Montserrat', sans-serif !important; padding-top: 8px !important; padding-bottom: 8px !important; }
li[role="option"]:hover, li[role="option"][aria-selected="true"], div[role="option"]:hover, div[role="option"][aria-selected="true"] { background-color: rgba(131,72,193,0.5) !important; color: #FFFFFF !important; }

div[data-testid="stExpander"], details[data-testid="stExpander"] { background-color: #050506 !important; border: 1px solid rgba(131,72,193,0.4) !important; border-radius: 12px !important; }
div[data-testid="stExpander"] summary p, details[data-testid="stExpander"] summary p { font-family: 'Montserrat', sans-serif !important; color: #FFFFFF !important; font-weight: 600 !important; }

.stButton > button { background: linear-gradient(90deg, #2C1969 0%, #8348C1 50%, #C38BFF 100%) !important; color: #FFFFFF !important; border: none !important; border-radius: 28px !important; font-weight: 500 !important; font-size: 14px !important; height: 44px !important; padding: 0 24px !important; transition-property: transform !important; transition-duration: 150ms !important; }
.stButton > button:hover { transform: scale(1.05) !important; }

.divider { border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }
.hint { font-size: 0.75rem; color: var(--muted); margin-top: 0.3rem; cursor: default; }
.thinking { color: var(--muted); font-style: italic; animation: pulse 1.5s infinite; }
@keyframes pulse { 0%{opacity:.5} 50%{opacity:1} 100%{opacity:.5} }

.news-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem; margin: 0.5rem 0; box-shadow: 0 8px 25px rgba(0,0,0,0.2); }
.news-title { font-weight: 600; font-size: 0.95rem; margin-bottom: 0.5rem; }
.news-title a { color: var(--text); text-decoration: none; }
.news-title a:hover { color: var(--accent); }
.news-meta { font-size: 0.75rem; color: var(--muted); display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
</style>
""", unsafe_allow_html=True)


def safe_text(value) -> str:
    if value is None:
        return "—"
    return html.escape(str(value)).replace("\n", "<br>")


def safe_price(value) -> str:
    if value is None:
        return "—"
    try:
        value = float(value)
    except:
        return "—"
    if value >= 1:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:.4f}"
    return f"${value:.8f}"


def safe_url(url: str) -> str:
    if not url:
        return "#"
    url = str(url).strip()
    if url.startswith("http://") or url.startswith("https://"):
        return html.escape(url, quote=True)
    return "#"


def fmt_price(v: float) -> str:
    try:
        v = float(v)
    except:
        return "—"
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:.4f}"
    return f"${v:.8f}"


def fmt_large(v: float) -> str:
    try:
        v = float(v)
    except:
        return "—"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


COIN_ALIASES = {
    "BTC": ["btc", "bitcoin", "біткоїн", "биткоин", "біткоін", "біток"],
    "ETH": ["eth", "ethereum", "ефір", "ефіріум", "етереум"],
    "SOL": ["sol", "solana", "солана"],
    "BNB": ["bnb"],
    "XRP": ["xrp", "ripple", "ріпл"],
    "DOGE": ["doge", "dogecoin", "дог", "догі"],
    "ADA": ["ada", "cardano", "кардано"],
    "TON": ["ton", "toncoin", "тон"],
    "SUI": ["sui"],
    "PEPE": ["pepe", "пепе"],
    "SHIB": ["shib", "shiba", "шіба"],
}


@st.cache_data(ttl=86400)
def get_all_valid_tickers():
    """Стягує всі актуальні тікери з Binance для валідації."""
    try:
        r = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=5)
        if r.status_code == 200:
            symbols = r.json().get("symbols", [])
            return set([s.get("baseAsset").upper() for s in symbols if s.get("quoteAsset") == "USDT"])
    except Exception:
        pass
    return {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "SUI"}


def extract_coin_from_question(text: str):
    if not text:
        return None

    q = text.lower()

    for symbol, aliases in COIN_ALIASES.items():
        for alias in aliases:
            if re.search(fr'\b{alias}\b', q):
                return symbol

    valid_tickers = get_all_valid_tickers()

    cleaned = text.replace("?", " ").replace(",", " ").replace(".", " ").replace("!", " ")
    words = cleaned.split()

    for word in words:
        clean = word.upper().strip()
        if clean in valid_tickers:
            return clean

    return None


@st.cache_data(ttl=15, show_spinner=False)
def get_realtime_binance_price(symbol: str):
    if not symbol:
        return None
    symbol = symbol.upper().strip()
    pair = f"{symbol}USDT"
    try:
        r = requests.get(
            "https://data-api.binance.vision/api/v3/ticker/24hr",
            params={"symbol": pair},
            timeout=7
        )
        if r.status_code == 200:
            data = r.json()
            return {
                "symbol": symbol,
                "pair": pair,
                "price": float(data.get("lastPrice", 0)),
                "change_24h": float(data.get("priceChangePercent", 0)),
                "high_24h": float(data.get("highPrice", 0)),
                "low_24h": float(data.get("lowPrice", 0)),
                "volume": float(data.get("quoteVolume", 0)),
                "source": "Binance"
            }
    except Exception:
        pass
    return None


def build_realtime_price_context(user_question: str):
    symbol = extract_coin_from_question(user_question)
    if not symbol:
        return ""
    data = get_realtime_binance_price(symbol)
    if not data:
        return f"""
Користувач, можливо, питає про монету {symbol}, але актуальну ціну з Binance зараз отримати не вдалося.
Не вигадуй точну ціну. Скажи, що зараз не можеш підтягнути актуальні дані по цій монеті.
"""
    return f"""
ВАЖЛИВО: користувач питає про монету {data['symbol']}.
Ось АКТУАЛЬНІ ринкові дані з {data['source']}:
- Пара: {data['pair']}
- Поточна ціна: {fmt_price(data['price'])}
- Зміна за 24г: {data['change_24h']:+.2f}%
- Мінімум 24г: {fmt_price(data['low_24h'])}
- Максимум 24г: {fmt_price(data['high_24h'])}
- Обсяг 24г: {fmt_large(data['volume'])}

Якщо користувач питає "яка ціна", відповідай саме цією ціною.
Не вигадуй іншу ціну.
"""


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
    names_dict = {
        "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "BNB": "BNB",
        "XRP": "Ripple", "DOGE": "Dogecoin", "ADA": "Cardano", "TRX": "TRON",
        "LINK": "Chainlink", "DOT": "Polkadot", "MATIC": "Polygon", "POL": "Polygon",
        "LTC": "Litecoin", "BCH": "Bitcoin Cash", "PEPE": "Pepe", "SHIB": "Shiba Inu",
        "AVAX": "Avalanche", "SUI": "Sui", "APT": "Aptos", "NEAR": "NEAR Protocol",
        "UNI": "Uniswap", "TON": "Toncoin", "NOT": "Notcoin", "WIF": "dogwifhat",
        "AR": "Arweave", "FIL": "Filecoin", "FET": "Fetch.ai", "RNDR": "Render",
        "RENDER": "Render", "INJ": "Injective", "TIA": "Celestia", "SEI": "Sei",
        "JUP": "Jupiter", "BONK": "Bonk", "FLOKI": "Floki", "ATOM": "Cosmos",
        "ARB": "Arbitrum", "OP": "Optimism"
    }
    try:
        cg_r = requests.get(f"{COINGECKO_BASE}/coins/list", timeout=5)
        if cg_r.status_code == 200:
            for coin in cg_r.json():
                symbol = coin.get("symbol", "").upper()
                name = coin.get("name", "")
                if symbol and name and symbol not in names_dict:
                    names_dict[symbol] = name
    except Exception:
        pass

    try:
        b_r = requests.get("https://data-api.binance.vision/api/v3/exchangeInfo", timeout=5)
        if b_r.status_code == 200:
            symbols = b_r.json().get("symbols", [])
            tickers = set()
            for s in symbols:
                if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                    tickers.add(s.get("baseAsset"))
            top_coins_to_sort = [
                "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "PEPE",
                "SUI", "WIF", "TON", "NOT", "LINK", "AVAX", "NEAR"
            ]
            final_list = []

            def fmt(t):
                if t in names_dict and names_dict[t].upper() != t:
                    return f"{t} ({names_dict[t]})"
                return t

            for coin in top_coins_to_sort:
                if coin in tickers:
                    final_list.append(fmt(coin))
                    tickers.remove(coin)
            final_list.extend(sorted([fmt(t) for t in list(tickers)]))
            return final_list
    except Exception:
        pass
    return ["BTC (Bitcoin)", "ETH (Ethereum)", "SOL (Solana)", "SUI (Sui)"]


@st.cache_data(ttl=3600)
def search_coingecko_id(query: str):
    if not query:
        return None, None
    try:
        r = requests.get(f"{COINGECKO_BASE}/search?query={query}", timeout=10)
        if r.status_code == 200:
            coins = r.json().get("coins", [])
            if coins:
                for c in coins:
                    if c.get("symbol", "").upper() == query.upper():
                        return c.get("id"), c.get("name")
                return coins[0].get("id"), coins[0].get("name")
    except Exception:
        pass
    return None, None


@st.cache_data(ttl=30)
def get_coin_data(coin_id: str):
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}",
            params={
                "localization": "false", "tickers": "false", "community_data": "true", "developer_data": "false"
            },
            timeout=10
        )
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


@st.cache_data(ttl=120)
def get_sparkline(coin_id: str):
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": "7", "interval": "daily"},
            timeout=10
        )
        if r.status_code == 200:
            return [p[1] for p in r.json().get("prices", [])]
        return []
    except Exception:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def get_ml_signal_cached(symbol: str, interval: str):
    return get_ml_signal(symbol, interval)


def translate_to_uk(text: str) -> str:
    if not text or text == "Без заголовку":
        return text
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "uk", "dt": "t", "q": text}
        r = requests.get(url, params=params, timeout=3)
        if r.status_code == 200:
            return "".join([s[0] for s in r.json()[0]])
    except Exception:
        pass
    return text


@st.cache_data(ttl=1800)
def get_crypto_news(coin_name: str, symbol: str = ""):
    if not NEWSAPI_KEY: return []
    query = f'"{coin_name}" OR ("{symbol}" AND crypto)'
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": query, "sortBy": "publishedAt", "language": "en", "pageSize": 3, "apiKey": NEWSAPI_KEY},
            timeout=10
        )
        if r.status_code != 200: return []
        articles = r.json().get("articles", [])
        cleaned, seen = [], set()
        for a in articles:
            title = (a.get("title") or "").strip()
            if not title or title in seen: continue
            seen.add(title)
            ukr_title = translate_to_uk(title)
            cleaned.append({
                "title": ukr_title, "url": a.get("url", ""),
                "source": {"name": a.get("source", {}).get("name", "Unknown")},
                "publishedAt": a.get("publishedAt", "")
            })
        return cleaned
    except Exception:
        return []


def stream_ollama(prompt: str):
    GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", "").strip()
    if not GROQ_API_KEY:
        yield "⚠️ Ключ GROQ_API_KEY не знайдено! Додай його у Secrets."
        return
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.25, "stream": True}
    try:
        res = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=data, stream=True, timeout=30
        )
        if res.status_code != 200:
            yield f"⚠️ Помилка Groq API: {res.text}"
            return
        for line in res.iter_lines():
            if line:
                decoded_line = line.decode("utf-8")
                if decoded_line.startswith("data: "):
                    if decoded_line == "data: [DONE]": break
                    try:
                        chunk = json.loads(decoded_line[6:])
                        delta = chunk["choices"][0]["delta"].get("content", "")
                        if delta: yield delta
                    except:
                        pass
    except Exception as e:
        yield f"⚠️ Технічна помилка: {e}"


def build_analysis_prompt(name, symbol, price, change_24h, change_7d, cap, vol, ath, low_24h, high_24h, spark_info,
                          news):
    news_text = "Актуальні новини:\n" + "\n".join([f"- {n.get('title')}" for n in news]) if news else "Новин немає."
    return f"""Ти — Міша, фінансовий AI-асистент. Пиши чистою українською. Стиль: коротко, впевнено.
Проаналізуй {name} ({symbol}):
- Ціна: {fmt_price(price)}
- Зміна 24г: {change_24h:+.2f}%
- {spark_info}
- Капіталізація: {fmt_large(cap)}
{news_text}
Напиши короткий огляд у 4-5 реченнях. Не давай фінансову пораду."""


def build_chat_prompt(name, symbol, price, change_24h, cap, vol, change_7d, news, history, user_q):
    history_text = "\n".join([f"{'Користувач' if m['role'] == 'user' else 'Ти'}: {m['content']}" for m in history[-6:]])
    news_text = "\n".join([f"- {n.get('title')}" for n in news]) if news else "Немає новин."
    realtime_context = build_realtime_price_context(user_q)
    return f"""Ти — Міша, CryptoMisha AI.
Стиль: українська мова, коротко, без токсичності.
Контекст: {name} ({symbol})
- Ціна: {fmt_price(price)}
- Зміна 24г: {change_24h:+.2f}%
{realtime_context}
Новини: {news_text}
Історія:
{history_text}
Запит користувача: {user_q}
Відповідай природно."""


for key, default in [("messages", []), ("current_coin", None), ("coin_data", None), ("coin_prices", []),
                     ("cg_name", None), ("generate_new", False), ("selected_interval", "4h")]:
    if key not in st.session_state: st.session_state[key] = default


def handle_chat_submit():
    user_val = st.session_state.chat_input_widget
    if user_val.strip():
        st.session_state.messages.append({"role": "user", "content": user_val})
        st.session_state.generate_new = True
    st.session_state.chat_input_widget = ""


st.markdown('<div class="hero-title">CryptoMisha AI</div>', unsafe_allow_html=True)
st.markdown(f'<div class="hero-sub">// Оснащено моделлю {MODEL_NAME}</div>',
            unsafe_allow_html=True)

col1, col2, col3 = st.columns([3, 1, 1])

with col1:
    tickers_list = get_binance_tickers()
    raw_selection = st.selectbox("Пошук монети", options=tickers_list, index=None,
                                 placeholder="🔍 Введи назву або тікер...", label_visibility="collapsed")
    user_ticker = raw_selection.split(" ")[0] if raw_selection else ""

with col2:
    tf_label = st.selectbox("Таймфрейм", options=list(TIMEFRAME_OPTIONS.keys()), index=1, label_visibility="collapsed")
    selected_interval = TIMEFRAME_OPTIONS[tf_label]

with col3:
    analyze_btn = st.button("⚡ Аналізувати", use_container_width=True)

if user_ticker and (
        analyze_btn or (st.session_state.current_coin == user_ticker and st.session_state.get("ml_result"))):
    if analyze_btn or st.session_state.current_coin != user_ticker:
        with st.spinner(f"Шукаю {user_ticker}..."):
            cg_id, cg_name = search_coingecko_id(user_ticker)
            if cg_id:
                data = get_coin_data(cg_id)
                prices = get_sparkline(cg_id)
            else:
                data, prices = None, []
            st.session_state.coin_data = data
            st.session_state.current_coin = user_ticker
            st.session_state.coin_prices = prices
            st.session_state.cg_name = cg_name if cg_name else user_ticker

            if analyze_btn:
                st.session_state.messages = []
                st.session_state.generate_new = False
                st.session_state.pop(f"analysis_{user_ticker}", None)
    else:
        data = st.session_state.coin_data
        prices = st.session_state.coin_prices
        cg_name = st.session_state.cg_name

    price, change_24h, change_7d, cap, vol, ath, low_24h, high_24h, spark_info = 0, 0, 0, 0, 0, 0, 0, 0, ""
    display_name = st.session_state.cg_name
    realtime_selected = get_realtime_binance_price(user_ticker)

    if data:
        m = data.get("market_data", {})
        price = m.get("current_price", {}).get("usd", 0)
        change_1h = m.get("price_change_percentage_1h_in_currency", {}).get("usd", 0) or 0
        change_24h = m.get("price_change_percentage_24h", 0) or 0
        change_7d = m.get("price_change_percentage_7d", 0) or 0
        cap = m.get("market_cap", {}).get("usd", 0)
        vol = m.get("total_volume", {}).get("usd", 0)
        ath = m.get("ath", {}).get("usd", 0)
        low_24h = m.get("low_24h", {}).get("usd", 0)
        high_24h = m.get("high_24h", {}).get("usd", 0)
        rank = data.get("market_cap_rank", "—")
        display_name = data.get("name", display_name)

        if realtime_selected:
            price, change_24h = realtime_selected.get("price", price), realtime_selected.get("change_24h", change_24h)
            low_24h, high_24h, vol = realtime_selected.get("low_24h", low_24h), realtime_selected.get("high_24h",
                                                                                                      high_24h), realtime_selected.get(
                "volume", vol)

        st.markdown(f"""
        <div class="card">
            <div class="card-title">{safe_text(display_name)} · #{safe_text(rank)} · {safe_text(user_ticker)}/USD</div>
            <div class="price-big">{fmt_price(price)}</div>
            <div class="stat-row">
                <div class="stat-chip"><span class="chip-label">Зміна 24г</span><span class="{'change-pos' if change_24h >= 0 else 'change-neg'}">{change_24h:+.2f}%</span></div>
                <div class="stat-chip"><span class="chip-label">Зміна 7д</span><span class="{'change-pos' if change_7d >= 0 else 'change-neg'}">{change_7d:+.2f}%</span></div>
                <div class="stat-chip"><span class="chip-label">MCap</span><span>{fmt_large(cap)}</span></div>
                <div class="stat-chip"><span class="chip-label">Обсяг 24г</span><span>{fmt_large(vol)}</span></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if prices:
            trend = "висхідний" if prices[-1] > prices[0] else "спадний"
            pct = ((prices[-1] - prices[0]) / prices[0] * 100) if prices[0] else 0
            spark_info = f"Тижневий тренд: {trend} ({pct:+.1f}% за 7 днів)"

    elif realtime_selected:
        price, change_24h = realtime_selected.get("price", 0), realtime_selected.get("change_24h", 0)
        low_24h, high_24h, vol = realtime_selected.get("low_24h", 0), realtime_selected.get("high_24h",
                                                                                            0), realtime_selected.get(
            "volume", 0)
        display_name = user_ticker

        st.markdown(f"""
        <div class="card">
            <div class="card-title">{safe_text(display_name)} · Актуальні дані · {safe_text(user_ticker)}/USDT</div>
            <div class="price-big">{fmt_price(price)}</div>
            <div class="stat-row">
                <div class="stat-chip"><span class="chip-label">Зміна 24г</span><span class="{'change-pos' if change_24h >= 0 else 'change-neg'}">{change_24h:+.2f}%</span></div>
                <div class="stat-chip"><span class="chip-label">Мін. 24г</span><span>{fmt_price(low_24h)}</span></div>
                <div class="stat-chip"><span class="chip-label">Макс. 24г</span><span>{fmt_price(high_24h)}</span></div>
                <div class="stat-chip"><span class="chip-label">Обсяг 24г</span><span>{fmt_large(vol)}</span></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    tv_interval_map = {"1h": "60", "4h": "240", "1d": "D", "1w": "W", "1M": "M"}
    tv_html = f"""
    <div class="tradingview-widget-container" style="height:100%;width:100%; border-radius:16px; overflow:hidden; border: 1px solid rgba(255,255,255,0.1); margin-bottom: 1.5rem; position: relative; box-shadow: 0 20px 70px rgba(131,72,193,0.10), 0 8px 25px rgba(0,0,0,0.35);">
      <div id="tradingview_misha" style="height:420px;width:100%"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
      new TradingView.widget({{"autosize": true, "symbol": "BINANCE:{user_ticker}USDT", "interval": "{tv_interval_map.get(selected_interval, "240")}", "timezone": "Europe/Kyiv", "theme": "dark", "style": "1", "locale": "uk", "enable_publishing": false, "backgroundColor": "#050506", "gridColor": "rgba(255,255,255,0.05)", "hide_top_toolbar": false, "hide_legend": false, "save_image": false, "container_id": "tradingview_misha"}});
      </script>
    </div>
    """
    components.html(tv_html, height=420)

    with st.spinner(f"🧠 ML-модель збирає дані..."):
        ml_result = get_ml_signal_cached(user_ticker, selected_interval)
        st.session_state.ml_result = ml_result

    if ml_result.get("status") == "success":
        if price == 0: price = ml_result.get("price", 0)
        signal_class, signal_border = get_signal_style(ml_result["signal"])
        rsi_val = ml_result.get("rsi")
        rsi_c = rsi_color(rsi_val) if isinstance(rsi_val, (int, float)) else "#e2e8f0"
        bb_pct = ml_result.get("bb_percent")
        bb_text = f"{bb_pct}% ({'перекуп.' if bb_pct > 80 else 'перепрод.' if bb_pct < 20 else 'норма'})" if isinstance(
            bb_pct, (int, float)) else "—"
        obv_icon = "🟢" if ml_result.get("obv_trend") == "↑" else "🔴"

        # ДОДАНО: Вивід усіх індикаторів
        extra_metrics = f"&nbsp;·&nbsp; Precision: <b>{ml_result.get('precision', '—')}%</b> &nbsp;·&nbsp; Recall: <b>{ml_result.get('recall', '—')}%</b> &nbsp;·&nbsp; F1: <b>{ml_result.get('f1_score', '—')}%</b>"
        sltp_html = f'<div class="ml-acc">🛑 SL: <b>{safe_price(ml_result.get("stop_loss"))}</b> &nbsp;·&nbsp; 🎯 TP: <b>{safe_price(ml_result.get("take_profit"))}</b></div>'

        st.markdown(f"""
<div class="ml-card" style="border-color: {signal_border};">
<div style="font-family:'Space Mono',monospace; font-size:.75rem; color:var(--muted); text-transform:uppercase; letter-spacing:1px; margin-bottom:.5rem;">⚙️ AI Прогноз · XGBoost · {safe_text(tf_label)}</div>
<div class="{signal_class}">{safe_text(ml_result['signal'])}<span class="ml-conf">&nbsp;{ml_result.get('confidence', '—')}% впевненості</span></div>
<div class="ml-indicators">
<div class="ml-chip" data-tooltip="RSI: Індекс відносної сили. >70 - перекуплено, <30 - перепродано."><span class="ml-chip-label">RSI</span><span style="color:{rsi_c}">{safe_text(rsi_val)}</span></div>
<div class="ml-chip" data-tooltip="EMA20: Експоненціальна ковзна середня. Показує короткостроковий тренд."><span>EMA20 {safe_price(ml_result.get('ema20'))}</span></div>
<div class="ml-chip" data-tooltip="BB%: Положення ціни відносно смуг Боллінджера."><span>BB% {safe_text(bb_text)}</span></div>
<div class="ml-chip" data-tooltip="OBV: Балансовий обсяг. Показує тиск покупців/продавців."><span>OBV {obv_icon} {safe_text(ml_result.get('obv_trend'))}</span></div>
</div>
<div class="ml-acc">📊 Accuracy: <b>{ml_result.get('accuracy', '—')}%</b> {extra_metrics}</div>
{sltp_html}
</div>
""", unsafe_allow_html=True)
    else:
        st.error(f"❌ ML прогноз недоступний: {ml_result.get('reason', 'невідома причина')}")

    st.markdown('<hr class="divider">', unsafe_allow_html=True)

    with st.spinner("Шукаю новини..."):
        news_articles = get_crypto_news(display_name, user_ticker)

    st.markdown('<div class="card-title" style="margin-top: 1.5rem;">📰 Останні новини</div>', unsafe_allow_html=True)
    if news_articles:
        for article in news_articles:
            st.markdown(
                f'<div class="news-card"><div class="news-title"><a href="{safe_url(article.get("url", "#"))}" target="_blank">{safe_text(article.get("title", "Без заголовку"))}</a></div><div class="news-meta"><span>{safe_text(article.get("source", {}).get("name", "Unknown"))}</span></div></div>',
                unsafe_allow_html=True)
    else:
        st.markdown('<div class="hint">Новин не знайдено.</div>', unsafe_allow_html=True)

    st.markdown('<hr class="divider">', unsafe_allow_html=True)

    analysis_key = f"analysis_{user_ticker}"
    if analyze_btn or analysis_key not in st.session_state:
        prompt = build_analysis_prompt(display_name, user_ticker, price, change_24h, change_7d, cap, vol, ath, low_24h,
                                       high_24h, spark_info, news_articles)
        analysis_placeholder = st.empty()
        analysis_placeholder.markdown(
            f'<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша · 📊 Аналіз</div><span class="thinking">Аналізую...</span></div>',
            unsafe_allow_html=True)
        full_analysis = ""
        for chunk in stream_ollama(prompt):
            full_analysis += chunk
            analysis_placeholder.markdown(
                f'<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша · 📊 Аналіз</div>{safe_text(full_analysis)}</div>',
                unsafe_allow_html=True)
        st.session_state[analysis_key] = full_analysis
        st.session_state.messages = [{"role": "ai", "content": full_analysis}]
        st.rerun()

    for msg in st.session_state.messages:
        if msg["role"] == "ai":
            st.markdown(
                f'<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша</div>{safe_text(msg["content"])}</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div class="msg-user"><div class="msg-label msg-label-user">▸ Ти</div>{safe_text(msg["content"])}</div>',
                unsafe_allow_html=True)

    if st.session_state.generate_new:
        chat_prompt = build_chat_prompt(display_name, user_ticker, price, change_24h, cap, vol, change_7d,
                                        news_articles, st.session_state.messages[:-1],
                                        st.session_state.messages[-1]["content"])
        chat_placeholder = st.empty()
        chat_placeholder.markdown(
            '<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша</div><span class="thinking">Думаю...</span></div>',
            unsafe_allow_html=True)
        full_answer = ""
        for chunk in stream_ollama(chat_prompt):
            full_answer += chunk
            chat_placeholder.markdown(
                f'<div class="msg-ai"><div class="msg-label msg-label-ai">⬡ Міша</div>{safe_text(full_answer)}</div>',
                unsafe_allow_html=True)
        st.session_state.messages.append({"role": "ai", "content": full_answer})
        st.session_state.generate_new = False
        st.rerun()

    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    st.text_input("Питання", placeholder="Напиши щось Міші...", key="chat_input_widget", on_change=handle_chat_submit,
                  label_visibility="collapsed")

    with st.expander("📜 Останні прогнози в базі даних"):
        try:
            conn = get_db_connection()
            # ДОДАНО: Витягуємо stop_loss та take_profit для журналу
            query = "SELECT symbol, interval, signal, price, confidence, accuracy, stop_loss, take_profit, created_at FROM model_predictions ORDER BY created_at DESC LIMIT 10"
            df_logs = pd.read_sql_query(query, conn)
            if not df_logs.empty:
                df_logs['created_at'] = pd.to_datetime(df_logs['created_at']).dt.strftime('%Y-%m-%d %H:%M:%S')
                st.dataframe(df_logs, use_container_width=True, hide_index=True)
            else:
                st.write("База даних порожня.")
            conn.close()
        except Exception as e:
            st.error(f"Помилка підключення до БД: {e}")

else:
    st.markdown(
        f'<div class="card" style="text-align:center; padding: 3rem;"><div style="font-size:3rem;margin-bottom:1rem; color: var(--accent);">✦</div><div style="font-size:1.3rem; font-weight:700;">Введи тікер монети та натисни «Аналізувати»</div></div>',
        unsafe_allow_html=True)