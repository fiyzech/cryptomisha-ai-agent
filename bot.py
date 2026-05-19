import time
import requests
import threading
import os
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from ml_engine import get_ml_signal, get_db_connection, purge_data_cache

_PREDICT_LOCK = threading.Lock()
_PREDICTING_NOW: set = set()


def _run_single_prediction(symbol: str):
    """Запускається в окремому треді: генерує прогноз для символу по всіх 4 таймфреймах."""
    sym = symbol.upper().strip()
    print(f"🔔 On-demand прогноз: {sym} (всі 4 TF)...")
    for interval in ["4h", "1d", "1w", "1M"]:
        try:
            result = get_ml_signal(sym, interval)
            status = result.get("status")
            if status == "success" and result.get("db_logged"):
                print(f"  ✅ {sym}/{interval}: {result.get('signal')}")
            else:
                print(f"  ⚠️ {sym}/{interval}: {result.get('reason', status)}")
        except Exception as e:
            print(f"  ❌ {sym}/{interval}: {e}")
        time.sleep(1.5)  # пауза між запитами до Binance

    with _PREDICT_LOCK:
        _PREDICTING_NOW.discard(sym)
    print(f"✔️ On-demand завершено для {sym}")


class DummyHandler(BaseHTTPRequestHandler):

    # ── CORS helper ──────────────────────────────────────────────
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        # ── /predict?symbol=XRP ──────────────────────────────────
        if path == "/predict":
            symbol = (params.get("symbol", [""])[0] or "").upper().strip()
            if not symbol:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "symbol required"}).encode())
                return

            with _PREDICT_LOCK:
                already = symbol in _PREDICTING_NOW
                if not already:
                    _PREDICTING_NOW.add(symbol)

            if already:
                self.send_response(202)
                self._send_cors_headers()
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "already_running", "symbol": symbol}).encode())
                return

            # Запускаємо в окремому треді щоб не блокувати HTTP-сервер
            threading.Thread(
                target=_run_single_prediction,
                args=(symbol,),
                daemon=True
            ).start()

            self.send_response(202)
            self._send_cors_headers()
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started", "symbol": symbol}).encode())
            return

        # ── / (status page) ─────────────────────────────────────
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            f"CryptoMisha Bot is running! Last cycle: {BOT_LAST_CYCLE}".encode("utf-8")
        )

    def log_message(self, *args):
        pass  # мовчимо в логах, щоб не засмічувати


def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    server.serve_forever()


# ===============================================================

INTERVALS = ["4h", "1d", "1w", "1M"]
BOT_LAST_CYCLE = "не запускався"  # для статус-ендпоінту
IGNORED_STABLECOINS = ['USDT', 'USDC', 'DAI', 'FDUSD', 'TUSD', 'USDD', 'USDS']
MARKETS_LIMIT = int(os.environ.get("MARKETS_LIMIT", "125"))
PREDICTION_FRESH_HOURS = float(os.environ.get("PREDICTION_FRESH_HOURS", "4"))  # оновлюємо кожні 4г
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


def self_ping_loop():
    """
    Пінгує власний веб-сервер кожні 10 хвилин щоб Render Free не усипляв процес.
    Render усипляє web service після 15 хв без трафіку — цей цикл запобігає цьому.
    """
    port = int(os.environ.get("PORT", 10000))
    url  = f"http://localhost:{port}/"
    while True:
        try:
            requests.get(url, timeout=5)
        except Exception:
            pass
        time.sleep(600)


if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("🌐 Мікро-сервер для Render запущено!")

    threading.Thread(target=self_ping_loop, daemon=True).start()
    print("📡 Self-ping запущено (кожні 10 хв, захист від сну Render)")

    print("🤖 Автономний AI-бот CryptoMisha успішно запущений!")
    cycle = 0
    while True:
        cycle += 1
        BOT_LAST_CYCLE = f"Цикл {cycle} — {time.strftime('%Y-%m-%d %H:%M:%S')}"
        try:
            run_bot()
        except Exception as exc:
            print(f"💥 [Цикл {cycle}] Критична помилка run_bot(): {exc}. Перезапуск через 5 хвилин...")
            time.sleep(300)
            continue

        try:
            purge_data_cache()
        except Exception as e:
            print(f"⚠️ purge_data_cache() failed: {e}")

        sleep_total = 7200  # 2 год
        sleep_chunk = 60    # прокидаємось кожну хвилину
        elapsed = 0
        print(f"💤 [Цикл {cycle}] Наступний цикл через {sleep_total//3600} год...")
        while elapsed < sleep_total:
            time.sleep(sleep_chunk)
            elapsed += sleep_chunk
