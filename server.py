import asyncio
import collections
import json
import logging
import os
import sys
import time
from typing import Dict, Set
import hashlib
import hmac
import urllib.parse
import httpx
import websockets

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- НАЛАШТУВАННЯ / CONFIGURATION ---
# Telegram settings (можна також передати через змінні оточення)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")

COOLDOWN_MINUTES = 5
MIN_VOLUME_USD = 10000  # Ігнорувати монети з хвилинним об'ємом менше $10k
MIN_HISTORY_MINUTES = 5  # Мінімальна кількість хвилин історії для розрахунку базового рівня
MIN_INCREASE_PCT = 100.0  # Мінімальний відсоток зростання об'єму для аномалії (+100% / у 2+ рази)

# --- TIGERTRADE & TRADING BOT PARAMETERS ---
TIGERTRADE_API_KEY = os.getenv("TIGERTRADE_API_KEY", "YOUR_TIGERTRADE_API_KEY")
TIGERTRADE_SECRET_KEY = os.getenv("TIGERTRADE_SECRET_KEY", "YOUR_TIGERTRADE_SECRET_KEY")
TIGERTRADE_SERVER_URL = os.getenv("TIGERTRADE_SERVER_URL", "http://127.0.0.1:8989")

VOL_THRESHOLD_MULT = 5.0   # Сплеск об'єму в 5 разів вище середнього
DELTA_BUY_RATIO = 0.80     # 80%+ покупок
LOOKBACK_WINDOW_SEC = 10   # Вікно аналізу аномалії (сек)
AVG_WINDOW_SEC = 900       # Вікно розрахунку середнього об'єму (15 хв)
BOT_COOLDOWN_SEC = 180     # Таймаут після угоди (3 хвилини)

SL_PERCENT = 0.002         # Стоп-лосс: 0.2%
TP_PERCENT = 0.005         # Тейк-профіт: 0.5%
TRADE_QTY = 100            # Розмір позиції в монетах

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

# --- ORDER FLOW TRADING BOT (TIGERTRADE FADE STRATEGY) ---
class OrderFlowBot:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.trades = collections.deque()  # (timestamp, volume_usd, is_buy, price)
        self.last_trade_time = 0
        self.is_in_position = False
        self.is_active = False

    def add_trade(self, price: float, qty: float, is_buyer_maker: bool):
        now = time.time()
        volume_usd = price * qty
        is_buy = not is_buyer_maker  # False = Market Buy
        self.trades.append((now, volume_usd, is_buy, price))
        self._cleanup(now)

    def _cleanup(self, now: float):
        while self.trades and self.trades[0][0] < now - AVG_WINDOW_SEC:
            self.trades.popleft()

    def get_metrics(self):
        now = time.time()
        trades_15m = list(self.trades)
        if not trades_15m:
            return 0, 0, 0, 0

        total_vol_15m = sum(t[1] for t in trades_15m)
        avg_10s_vol = (total_vol_15m / AVG_WINDOW_SEC) * LOOKBACK_WINDOW_SEC

        recent_trades = [t for t in trades_15m if t[0] >= now - LOOKBACK_WINDOW_SEC]
        recent_vol = sum(t[1] for t in recent_trades)
        buy_vol = sum(t[1] for t in recent_trades if t[2])

        buy_ratio = (buy_vol / recent_vol) if recent_vol > 0 else 0
        latest_price = trades_15m[-1][3] if trades_15m else 0

        return recent_vol, avg_10s_vol, buy_ratio, latest_price

    async def send_tigertrade_order(self, side: str, order_type: str, qty: float, price: float = None):
        server_url = TIGERTRADE_SERVER_URL.rstrip("/")

        # Створюємо базові сповіщення для UI
        exec_event = {
            "type": "bot_execution",
            "data": {
                "symbol": self.symbol,
                "side": side,
                "type": order_type,
                "qty": qty,
                "price": price or "MARKET",
                "timestamp": time.time()
            }
        }
        if connected_clients:
            websockets.broadcast(connected_clients, json.dumps(exec_event))

        # Перевірка: Якщо вказано прямий Binance REST API (Futures, Spot чи Testnet)
        is_binance_direct = "binance" in server_url.lower()

        if is_binance_direct:
            # Формуємо запит до Binance API з підписом HMAC SHA256
            is_futures = "fapi" in server_url.lower() or "binancefuture" in server_url.lower()
            endpoint_path = "/fapi/v1/order" if is_futures else "/api/v3/order"
            url = f"{server_url}{endpoint_path}"

            headers = {
                "X-MBX-APIKEY": TIGERTRADE_API_KEY
            }

            params = {
                "symbol": self.symbol,
                "side": side,
                "type": order_type,
                "quantity": qty,
                "timestamp": int(time.time() * 1000)
            }
            if price:
                params["price"] = price
                params["timeInForce"] = "GTC"

            query_string = urllib.parse.urlencode(params)
            signature = hmac.new(
                TIGERTRADE_SECRET_KEY.encode("utf-8"),
                query_string.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()

            full_url = f"{url}?{query_string}&signature={signature}"
            logger.info(f"[BINANCE REST EXECUTION] {self.symbol} | {side} {order_type} Qty: {qty} Price: {price if price else 'MARKET'}")

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.post(full_url, headers=headers)
                    res_json = response.json()
                    logger.info(f"[BINANCE RESPONSE] Status: {response.status_code} | {res_json}")
                    return res_json
            except Exception as e:
                logger.warning(f"[BINANCE API ERROR] {e}. Ордер зареєстровано локально.")
                return {"status": "executed_locally", "error": str(e)}

        else:
            # Стандартне надсилання в TigerTrade Gateway API
            endpoint = f"{server_url}/api/v1/order"
            headers = {
                "Content-Type": "application/json",
                "X-API-KEY": TIGERTRADE_API_KEY
            }
            payload = {
                "symbol": self.symbol,
                "side": side,
                "type": order_type,
                "quantity": qty,
                "timestamp": int(time.time() * 1000)
            }
            if price:
                payload["price"] = price
                payload["timeInForce"] = "GTC"

            logger.info(f"[TIGERTRADE BOT EXECUTION] {self.symbol} | {side} {order_type} Qty: {qty} Price: {price if price else 'MARKET'}")

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.post(endpoint, headers=headers, json=payload)
                    return response.json()
            except Exception as e:
                logger.warning(f"[TIGERTRADE GATEWAY ERROR] {e}. Ордер зареєстровано локально.")
                return {"status": "executed_locally", "error": str(e)}

    async def execute_fade_strategy(self, current_price: float):
        self.is_in_position = True
        self.last_trade_time = time.time()
        
        sl_price = round(current_price * (1 + SL_PERCENT), 6)
        tp_price = round(current_price * (1 - TP_PERCENT), 6)

        logger.info(f" >>> [FADE STRATEGY TRIGGERED] {self.symbol} | Short Entry: ${current_price} | Stop-Loss: ${sl_price} | Take-Profit: ${tp_price}")

        # 1. Вхід у ШОРТ по ринку
        await self.send_tigertrade_order("SELL", "MARKET", TRADE_QTY)

        # 2. Захисний Stop-Loss
        await self.send_tigertrade_order("BUY", "STOP_MARKET", TRADE_QTY, price=sl_price)

        # 3. Take-Profit
        await self.send_tigertrade_order("BUY", "TAKE_PROFIT_MARKET", TRADE_QTY, price=tp_price)

        # Сповіщення в Telegram
        if TELEGRAM_TOKEN and TELEGRAM_TOKEN != "YOUR_TELEGRAM_BOT_TOKEN":
            tg_text = (
                f"🤖 <b>TIGERTRADE BOT EXECUTION (FADE)</b>\n\n"
                f"<b>Symbol:</b> #{self.symbol}\n"
                f"<b>Entry (Short):</b> ${current_price:,.4f}\n"
                f"<b>Stop-Loss:</b> ${sl_price:,.4f} (+{SL_PERCENT*100:.1f}%)\n"
                f"<b>Take-Profit:</b> ${tp_price:,.4f} (-{TP_PERCENT*100:.1f}%)\n"
                f"<b>Qty:</b> {TRADE_QTY}"
            )
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": tg_text, "parse_mode": "HTML"})
            except Exception:
                pass

    async def process_trade_tick(self, price: float, qty: float, is_buyer_maker: bool):
        if not self.is_active:
            return

        self.add_trade(price, qty, is_buyer_maker)

        now = time.time()
        if self.is_in_position and (now - self.last_trade_time > BOT_COOLDOWN_SEC):
            self.is_in_position = False
            logger.info(f"[BOT] {self.symbol} Cooldown вийшов. Бот знову готовий до пошуку сигналів.")

        if self.is_in_position:
            return

        recent_vol, avg_10s_vol, buy_ratio, latest_price = self.get_metrics()

        if avg_10s_vol > 0 and recent_vol > (avg_10s_vol * VOL_THRESHOLD_MULT):
            if buy_ratio >= DELTA_BUY_RATIO:
                logger.info(f"[BOT SIGNAL DETECTED] {self.symbol} | 10s Vol: ${recent_vol:,.2f} | Avg 10s: ${avg_10s_vol:,.2f} | Buyers: {buy_ratio*100:.1f}%")
                await self.execute_fade_strategy(latest_price)

# Словник активних екземплярів ботів для кожної монети
active_bots: Dict[str, OrderFlowBot] = {}

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

    # Передаємо угоду активному OrderFlowBot для монети (якщо активовано з інтерфейсу)
    if symbol in active_bots:
        await active_bots[symbol].process_trade_tick(p, q, is_buyer_maker)

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
            min_ratio = 1.0 + (MIN_INCREASE_PCT / 100.0)
            if increase_ratio >= min_ratio and v_curr >= MIN_VOLUME_USD:
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
        # Тримаємо з'єднання відкритим та приймаємо команду управління ботом від клієнта
        async for message in websocket:
            try:
                msg_obj = json.loads(message)
                msg_type = msg_obj.get("type")
                symbol = msg_obj.get("symbol", "").upper()

                if msg_type == "toggle_bot" and symbol:
                    if symbol not in active_bots:
                        active_bots[symbol] = OrderFlowBot(symbol)
                    
                    bot = active_bots[symbol]
                    bot.is_active = not bot.is_active

                    logger.info(f"[TIGERTRADE BOT] {symbol}: {'АКТИВОВАНО 🟢' if bot.is_active else 'ЗУПИНЕНО 🔴'}")

                    status_event = {
                        "type": "bot_status",
                        "symbol": symbol,
                        "is_active": bot.is_active
                    }
                    if connected_clients:
                        websockets.broadcast(connected_clients, json.dumps(status_event))

                elif msg_type == "get_bot_status" and symbol:
                    is_act = active_bots[symbol].is_active if symbol in active_bots else False
                    await websocket.send(json.dumps({
                        "type": "bot_status",
                        "symbol": symbol,
                        "is_active": is_act
                    }))

            except Exception as ex:
                logger.error(f"Помилка обробки команд сокета: {ex}")
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
