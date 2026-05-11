import time
import requests
import threading
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from ml_engine import get_ml_signal

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


def get_top_125_coins():
    print("🔄 Оновлюю список ТОП-125 монет так само, як у MarketsPage...")

    try:
        binance_res = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
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

            if len(final_coins) == 125:
                break

        print(f"✅ Бот отримав ті самі монети, що й MarketsPage: {len(final_coins)}")

        return final_coins

    except Exception as e:
        print(f"❌ Помилка отримання списку монет: {e}")
        return ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "SUI", "PEPE", "AVAX"]

def run_bot():
    print(f"\n🚀 [{time.strftime('%Y-%m-%d %H:%M:%S')}] Початок нового циклу аналізу...")
    coins_to_analyze = get_top_125_coins()

    for i, coin in enumerate(coins_to_analyze, 1):
        for interval in INTERVALS:
            print(f"⏳ [{i}/{len(coins_to_analyze)}] Аналізую {coin} на {interval}...")
            try:
                get_ml_signal(coin, interval)
                print(f"✅ {coin} ({interval}) успішно записано в БД!")
            except Exception as e:
                print(f"❌ Помилка з {coin} ({interval}): {e}")
            time.sleep(4)  # Захист від бану Binance

    print(f"\n🏁 [{time.strftime('%Y-%m-%d %H:%M:%S')}] Цикл завершено. БД оновлена.")


if __name__ == "__main__":
    # 1. Запускаємо веб-сервер у фоні для Render
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("🌐 Мікро-сервер для Render запущено!")

    # 2. Запускаємо безкінечний цикл бота
    print("🤖 Автономний AI-бот CryptoMisha успішно запущений!")
    while True:
        run_bot()
        time.sleep(7200)  # Спимо 2 години