import time
import requests
import threading
import os
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from ml_engine import get_ml_signal, get_db_connection

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write("CryptoMisha Bot is running and analyzing markets! 🚀".encode('utf-8'))


def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    server.serve_forever()


# ===============================================================

INTERVALS = ["4h", "1d", "1w", "1M"]
IGNORED_STABLECOINS = ['USDT', 'USDC', 'DAI', 'FDUSD', 'TUSD', 'USDD', 'USDS']
MARKETS_LIMIT = int(os.environ.get("MARKETS_LIMIT", "125"))
PREDICTION_FRESH_HOURS = float(os.environ.get("PREDICTION_FRESH_HOURS", "6"))
JOB_SLEEP_SECONDS = float(os.environ.get("JOB_SLEEP_SECONDS", "2.5"))
COIN_CACHE_FILE = os.environ.get("COIN_CACHE_FILE", "coin_universe_cache.json")
DEFAULT_FALLBACK_COINS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "TRX", "AVAX", "LINK",
    "TON", "SUI", "HBAR", "DOT", "BCH", "LTC", "NEAR", "UNI", "APT", "ICP",
    "ETC", "POL", "RENDER", "ATOM", "FIL", "ARB", "OP", "ALGO", "VET", "INJ",
    "FET", "WLD", "AAVE", "MKR", "CRV", "LDO", "XLM", "PEPE", "SHIB", "FLOKI",
    "BONK", "WIF", "TIA", "SEI", "RUNE", "GRT", "JUP", "PYTH", "ENA", "PENDLE",
]


def load_cached_coin_universe(max_age_hours=24):
    try:
        if not os.path.exists(COIN_CACHE_FILE):
            return None

        with open(COIN_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)

        saved_at = datetime.fromisoformat(payload.get("saved_at", ""))
        age_hours = (datetime.now(timezone.utc) - saved_at).total_seconds() / 3600
        coins = payload.get("coins", [])

        if coins and age_hours <= max_age_hours:
            print(f"♻️ Використовую кеш списку MarketsPage: {len(coins)} монет, вік {age_hours:.1f} год")
            return coins[:MARKETS_LIMIT]
    except Exception as e:
        print(f"⚠️ Не вдалося прочитати кеш монет: {e}")

    return None


def save_coin_universe_cache(coins):
    try:
        with open(COIN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"saved_at": datetime.now(timezone.utc).isoformat(), "coins": coins},
                f,
                ensure_ascii=False,
                indent=2
            )
    except Exception as e:
        print(f"⚠️ Не вдалося зберегти кеш монет: {e}")


def get_binance_volume_fallback(limit=125):
    try:
        print("⚠️ CoinGecko недоступний. Беру fallback-список з Binance за обсягом торгів...")
        res = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10)
        if res.status_code != 200:
            raise Exception(f"Binance 24hr API Error: {res.status_code}")

        tickers = res.json()
        coins = []
        seen = set()

        usdt_tickers = sorted(
            [item for item in tickers if item.get("symbol", "").endswith("USDT")],
            key=lambda item: float(item.get("quoteVolume", 0) or 0),
            reverse=True
        )

        for item in usdt_tickers:
            symbol = item["symbol"].replace("USDT", "")
            if symbol in IGNORED_STABLECOINS or symbol in seen:
                continue
            coins.append(symbol)
            seen.add(symbol)
            if len(coins) >= limit:
                break

        if coins:
            print(f"✅ Binance fallback дав {len(coins)} монет")
            return coins
    except Exception as e:
        print(f"❌ Binance fallback теж впав: {e}")

    print(f"⚠️ Використовую статичний fallback: {len(DEFAULT_FALLBACK_COINS)} монет")
    return DEFAULT_FALLBACK_COINS[:limit]


def get_top_125_coins():
    print("🔄 Оновлюю список ТОП-125 монет так само, як у MarketsPage...")

    try:
        binance_res = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            timeout=10
        )

        if binance_res.status_code != 200:
            raise Exception(f"Binance API Error: {binance_res.status_code}")

        coingecko_res = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 250,
                "page": 1,
                "sparkline": "false"
            },
            timeout=10
        )

        if coingecko_res.status_code != 200:
            raise Exception(f"CoinGecko API Error: {coingecko_res.status_code}")

        binance_data = binance_res.json()
        cg_data = coingecko_res.json()

        valid_binance_pairs = {
            item["symbol"]
            for item in binance_data
            if item["symbol"].endswith("USDT")
        }

        final_coins = []

        for coin in cg_data:
            symbol = coin["symbol"].upper()

            if symbol in IGNORED_STABLECOINS:
                continue

            if f"{symbol}USDT" in valid_binance_pairs:
                final_coins.append(symbol)

            if len(final_coins) == MARKETS_LIMIT:
                break

        print(f"✅ Бот отримав ті самі монети, що й MarketsPage: {len(final_coins)}")
        save_coin_universe_cache(final_coins)

        return final_coins

    except Exception as e:
        print(f"❌ Помилка отримання списку монет: {e}")
        cached = load_cached_coin_universe()
        if cached:
            return cached
        return get_binance_volume_fallback(MARKETS_LIMIT)


def get_prediction_age_map():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                symbol,
                interval,
                EXTRACT(EPOCH FROM (NOW() - MAX(created_at))) / 3600.0 AS age_hours
            FROM model_predictions
            GROUP BY symbol, interval
        """)
        rows = cur.fetchall()
        cur.close()

        return {
            (str(symbol).upper(), str(interval)): float(age_hours)
            for symbol, interval, age_hours in rows
            if symbol and interval and age_hours is not None
        }
    except Exception as e:
        print(f"⚠️ Не вдалося перевірити свіжість прогнозів у БД: {e}")
        return {}
    finally:
        if conn is not None:
            conn.close()


def build_smart_jobs(coins):
    age_map = get_prediction_age_map()
    jobs = []
    fresh_skipped = 0

    for coin_index, coin in enumerate(coins):
        for interval_index, interval in enumerate(INTERVALS):
            age = age_map.get((coin.upper(), interval))

            if age is not None and age < PREDICTION_FRESH_HOURS:
                fresh_skipped += 1
                continue

            is_missing = age is None
            jobs.append({
                "coin": coin,
                "interval": interval,
                "coin_index": coin_index,
                "interval_index": interval_index,
                "age": 999999 if age is None else age,
                "missing": is_missing,
            })

    jobs.sort(key=lambda job: (
        0 if job["missing"] else 1,
        -job["age"],
        job["coin_index"],
        job["interval_index"],
    ))

    print(
        f"🧠 Smart-план: активних завдань {len(jobs)}, "
        f"свіжих пропущено {fresh_skipped}, fresh window {PREDICTION_FRESH_HOURS:g} год."
    )

    return jobs

def run_bot():
    print(f"\n🚀 [{time.strftime('%Y-%m-%d %H:%M:%S')}] Початок нового циклу аналізу...")
    coins_to_analyze = get_top_125_coins()
    jobs = build_smart_jobs(coins_to_analyze)
    total_jobs = len(jobs)
    written = 0
    skipped = 0
    failed_db = 0

    if not jobs:
        print("✅ Усі прогнози ще свіжі. Немає що оновлювати в цьому циклі.")
        return

    for job_index, job in enumerate(jobs, 1):
        coin = job["coin"]
        interval = job["interval"]
        age_label = "немає в БД" if job["missing"] else f"{job['age']:.1f} год"
        print(f"⏳ [{job_index}/{total_jobs}] Аналізую {coin} на {interval} ({age_label})...")
        try:
            result = get_ml_signal(coin, interval)

            if result.get("status") != "success":
                skipped += 1
                print(f"⚠️ {coin} ({interval}) не записано: {result.get('reason', 'невідома причина')}")
            elif not result.get("db_logged"):
                failed_db += 1
                print(f"❌ {coin} ({interval}) сигнал згенеровано, але запис у БД не підтверджено!")
            else:
                written += 1
                feedback = result.get("feedback_winrate")
                feedback_label = f", self-check {feedback}%" if feedback is not None else ""
                print(f"✅ {coin} ({interval}) реально записано в БД: {result.get('signal')}{feedback_label}")
        except Exception as e:
            skipped += 1
            print(f"❌ Помилка з {coin} ({interval}): {e}")
        time.sleep(JOB_SLEEP_SECONDS)  # Захист від бану Binance

    print(
        f"\n🏁 [{time.strftime('%Y-%m-%d %H:%M:%S')}] Цикл завершено. "
        f"Завдань: {total_jobs}, записано в БД: {written}, пропущено: {skipped}, помилки БД: {failed_db}."
    )


if __name__ == "__main__":
    # 1. Запускаємо веб-сервер у фоні для Render
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("🌐 Мікро-сервер для Render запущено!")

    # 2. Запускаємо безкінечний цикл бота
    print("🤖 Автономний AI-бот CryptoMisha успішно запущений!")
    while True:
        run_bot()
        time.sleep(7200)  # Спимо 2 години
