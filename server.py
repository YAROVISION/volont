import asyncio
import collections
import json
import logging
import os
import sys
import time
from typing import Dict, Set
import httpx
import websockets

# --- НАЛАШТУВАННЯ / CONFIGURATION ---
# Telegram settings (можна також передати через змінні оточення)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")

COOLDOWN_MINUTES = 5
MIN_VOLUME_USD = 10000  # Ігнорувати монети з хвилинним об'ємом менше $10k
MIN_HISTORY_MINUTES = 5  # Мінімальна кількість хвилин історії для розрахунку базового рівня

LOCAL_WS_HOST = "localhost"
LOCAL_WS_PORT = 8765

# Налаштування кодування консолі для Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# --- НАЛАШТУВАННЯ ЛОГУВАННЯ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("VolumeMonitor")

# --- СТРУКТУРИ ДАНИХ ---
class CoinState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        # Дек для зберігання об'ємів останніх 14 ЗАВЕРШЕНИХ хвилин
        self.history = collections.deque(maxlen=14)
        self.current_volume = 0.0
        self.buyer_volume = 0.0
        self.current_minute = None  # В хвилинах від початку епохи
        self.cooldown_until = 0.0

# Пам'ять для збереження стану всіх монет
coins_data: Dict[str, CoinState] = {}
# Множина активних підключень локального веб-сервера
connected_clients: Set = set()

# --- ТЕЛЕГРАМ СПОВІЩЕННЯ ---
async def send_telegram_alert(symbol: str, price: float, current_vol: float, avg_vol: float, increase: float, buyer_pct: float):
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN" or not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "YOUR_CHAT_ID":
        logger.warning(f"Telegram API не налаштовано. Пропускаємо сповіщення для {symbol}.")
        return

    text = (
        f"🚨 <b>SPARK DETECTED!</b> 🚨\n\n"
        f"<b>Asset:</b> #{symbol}\n"
        f"<b>Price:</b> ${price:,.4f}\n"
        f"<b>Current 1m Vol:</b> ${current_vol:,.2f}\n"
        f"<b>Average 14m Vol:</b> ${avg_vol:,.2f}\n"
        f"<b>Increase:</b> +{increase:,.2f}%\n"
        f"<b>Market Action:</b> {buyer_pct:,.1f}% Buyers 🟢"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                logger.info(f"Telegram сповіщення успішно надіслано для {symbol}.")
            else:
                logger.error(f"Помилка надсилання Telegram сповіщення: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Виняток при надсиланні Telegram сповіщення для {symbol}: {e}")

# --- ОБРОБКА ДАНИХ УГОД ---
async def process_trade(trade: dict):
    symbol = trade.get("s")
    if not symbol:
        return

    # E: Event time (мілісекунди)
    event_time_ms = trade.get("E", int(time.time() * 1000))
    trade_minute = int(event_time_ms // 1000 // 60)

    p = float(trade.get("p", 0.0))
    q = float(trade.get("q", 0.0))
    is_buyer_maker = trade.get("m", False)  # True = Sell, False = Buy

    vol_usd = p * q

    if symbol not in coins_data:
        coins_data[symbol] = CoinState(symbol)

    state = coins_data[symbol]

    # Перевірка на зміну хвилини
    if state.current_minute is None:
        state.current_minute = trade_minute
    elif trade_minute > state.current_minute:
        # Хвилина завершилась, зберігаємо в історію
        state.history.append(state.current_volume)
        
        # Якщо була велика пауза між угодами, заповнюємо нулями
        gap = trade_minute - state.current_minute - 1
        if gap > 0:
            for _ in range(min(gap, 14)):
                state.history.append(0.0)

        # Логуємо перехід хвилини для ліквідних монет (для діагностики)
        if state.current_volume > MIN_VOLUME_USD:
            logger.debug(f"{symbol}: Completed minute {state.current_minute} with vol {state.current_volume:,.2f}")

        # Скидаємо об'єми для нової хвилини
        state.current_volume = 0.0
        state.buyer_volume = 0.0
        state.current_minute = trade_minute

    # Накопичуємо об'єм поточної хвилини
    state.current_volume += vol_usd
    if not is_buyer_maker:
        state.buyer_volume += vol_usd

    # Перевіряємо умови тригеру аномалії
    # Потрібно мати достатньо хвилин історії для базового рівня
    if len(state.history) >= MIN_HISTORY_MINUTES:
        v_avg = sum(state.history) / len(state.history)
        v_curr = state.current_volume

        # Розраховуємо збільшення
        if v_avg > 0:
            increase_ratio = v_curr / v_avg
            if increase_ratio >= 1.10 and v_curr >= MIN_VOLUME_USD:
                current_time = time.time()
                # Перевірка таймауту (cooldown)
                if current_time >= state.cooldown_until:
                    # Встановлюємо cooldown
                    state.cooldown_until = current_time + (COOLDOWN_MINUTES * 60)
                    
                    increase_pct = (increase_ratio - 1.0) * 100
                    buyer_pct = (state.buyer_volume / v_curr * 100) if v_curr > 0 else 50.0

                    logger.info(f"[SURGE] SURGE DETECTED! {symbol} | Current: {v_curr:,.1f} | Avg: {v_avg:,.1f} | +{increase_pct:.1f}%")

                    alert_payload = {
                        "symbol": symbol,
                        "price": p,
                        "current_volume": round(v_curr, 2),
                        "average_volume": round(v_avg, 2),
                        "increase_percent": round(increase_pct, 2),
                        "buyer_percentage": round(buyer_pct, 2),
                        "timestamp": current_time
                    }

                    # Транслюємо на локальні веб-клієнти
                    if connected_clients:
                        message = json.dumps({"type": "alert", "data": alert_payload})
                        websockets.broadcast(connected_clients, message)

                    # Надсилаємо в Telegram
                    asyncio.create_task(
                        send_telegram_alert(
                            symbol=symbol,
                            price=p,
                            current_vol=v_curr,
                            avg_vol=v_avg,
                            increase=increase_pct,
                            buyer_pct=buyer_pct
                        )
                    )

# --- BINANCE WEBSOCKET КЛІЄНТ ---
async def binance_ws_listener(streams_chunk: list, chunk_index: int):
    # Формуємо URL для Combined Streams
    streams_str = "/".join(streams_chunk)
    uri = f"wss://stream.binance.com:9443/stream?streams={streams_str}"
    
    retry_delay = 2
    max_retry_delay = 60

    while True:
        try:
            logger.info(f"[WS Chunk {chunk_index}] Підключення до Binance WS ({len(streams_chunk)} пар)...")
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as websocket:
                retry_delay = 2  # Скидаємо delay при успішному з'єднанні
                logger.info(f"[WS Chunk {chunk_index}] З'єднання з Binance встановлено!")
                
                async for message in websocket:
                    try:
                        msg_data = json.loads(message)
                        # Структура combined stream: {"stream": "...", "data": {...}}
                        trade_data = msg_data.get("data", {})
                        if trade_data:
                            await process_trade(trade_data)
                    except json.JSONDecodeError:
                        logger.error(f"[WS Chunk {chunk_index}] Помилка парсингу JSON!")
                    except Exception as e:
                        logger.error(f"[WS Chunk {chunk_index}] Помилка обробки події: {e}", exc_info=True)
                        
        except (websockets.exceptions.ConnectionClosed, Exception) as e:
            logger.error(f"[WS Chunk {chunk_index}] З'єднання розірвано: {e}. Перепідключення через {retry_delay} сек...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)

# --- ОТРИМАННЯ СПИСКУ ПАР З BINANCE ---
async def fetch_usdt_pairs() -> list:
    url = "https://api.binance.com/api/v3/exchangeInfo"
    logger.info("Отримання списку торгових пар з Binance Exchange Info...")
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise Exception(f"Не вдалося отримати ExchangeInfo: {response.status_code}")
        
        data = response.json()
        symbols = data.get("symbols", [])
        
        usdt_pairs = []
        for s in symbols:
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING" and s.get("isSpotTradingAllowed"):
                usdt_pairs.append(s["symbol"])
                
        logger.info(f"Знайдено активних USDT пар: {len(usdt_pairs)}")
        return sorted(usdt_pairs)

# --- ЛОКАЛЬНИЙ WEBSOCKET СЕРВЕР ---
async def local_ws_handler(websocket):
    logger.info(f"Новий веб-клієнт підключився: {websocket.remote_address}")
    connected_clients.add(websocket)
    try:
        # Надсилаємо статус-привітання
        await websocket.send(json.dumps({"type": "status", "data": "connected"}))
        # Тримаємо з'єднання відкритим
        async for message in websocket:
            # Нам не потрібно приймати повідомлення, але якщо вони прийдуть, логуємо
            logger.debug(f"Отримано від клієнта {websocket.remote_address}: {message}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.remove(websocket)
        logger.info(f"Клієнт відключився: {websocket.remote_address}")

# --- ГОЛОВНА АСИНХРОННА ФУНКЦІЯ ---
async def main():
    try:
        # 1. Отримуємо USDT пари
        usdt_symbols = await fetch_usdt_pairs()
    except Exception as e:
        logger.error(f"Помилка під час ініціалізації пар: {e}")
        logger.info("Не вдалося розпочати роботу без списку монет. Спробуємо ще раз за 10 секунд.")
        await asyncio.sleep(10)
        return await main()

    # Створюємо назви стрімів у нижньому регістрі: e.g. "btcusdt@aggTrade"
    streams = [f"{symbol.lower()}@aggTrade" for symbol in usdt_symbols]

    # Binance обмежує кількість підписок на один сокет (допустимий максимум ~200-300 для безпечної довжини URL)
    # Розділимо наші 400+ монет на чанки по 150 пар
    chunk_size = 150
    chunks = [streams[i:i + chunk_size] for i in range(0, len(streams), chunk_size)]
    logger.info(f"Розділено на {len(chunks)} підключень до Binance WebSocket.")

    # 2. Запускаємо локальний WebSocket сервер
    logger.info(f"Запуск локального WebSocket сервера на ws://{LOCAL_WS_HOST}:{LOCAL_WS_PORT}...")
    local_server = await websockets.serve(local_ws_handler, LOCAL_WS_HOST, LOCAL_WS_PORT)

    # 3. Запускаємо клієнти Binance WebSocket у фоні
    ws_tasks = []
    for idx, chunk in enumerate(chunks):
        task = asyncio.create_task(binance_ws_listener(chunk, idx + 1))
        ws_tasks.append(task)

    # Тримаємо сервер запущеним
    await asyncio.gather(*ws_tasks, local_server.wait_closed())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Сервер зупинено користувачем.")
