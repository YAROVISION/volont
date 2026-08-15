import asyncio
import collections
import json
import logging
import time
from typing import Dict, List, Optional, Callable, Awaitable, Set
import httpx
import websockets

import config
import scoring
from database import db_manager

logger = logging.getLogger("BinanceStream")


class CoinState:
    def __init__(self, symbol: str):
        self.symbol: str = symbol
        self.history: collections.deque = collections.deque(maxlen=config.SLIDING_WINDOW_MINUTES)
        self.current_volume: float = 0.0
        self.buyer_volume: float = 0.0
        self.trades_count_1m: int = 0
        self.last_price: float = 0.0
        self.price_min_1m: float = 0.0
        self.price_max_1m: float = 0.0
        self.current_minute: Optional[int] = None
        self.cooldown_until: float = 0.0
        self.current_score: float = 0.0
        self.orderbook_density_usd: float = 0.0
        self.last_surge_pct: float = 0.0
        self.last_volatility_pct: float = 0.0


class BinanceStreamManager:
    def __init__(self, on_anomaly_callback: Optional[Callable[[Dict], Awaitable[None]]] = None):
        self.coins: Dict[str, CoinState] = {}
        self.on_anomaly_callback = on_anomaly_callback
        self.running: bool = False
        self._tasks: List[asyncio.Task] = []

    async def fetch_usdt_symbols(self) -> List[str]:
        """Fetch active USDT spot trading symbols from Binance REST API."""
        url = "https://api.binance.com/api/v3/exchangeInfo"
        symbols = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("symbols", []):
                        if (
                            item.get("status") == "TRADING"
                            and item.get("quoteAsset") == "USDT"
                            and item.get("isSpotTradingAllowed", True)
                        ):
                            symbols.append(item.get("symbol"))
            logger.info(f"Successfully fetched {len(symbols)} active USDT trading pairs from Binance.")
        except Exception as e:
            logger.error(f"Failed to fetch Binance symbols via REST API: {e}")
            # Fallback to major tokens if API request fails
            symbols = [
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                "PEPEUSDT", "ADAUSDT", "AVAXUSDT", "SHIBUSDT", "NEARUSDT", "LINKUSDT"
            ]
        return symbols

    def process_trade(self, symbol: str, price: float, qty: float, is_buyer_maker: bool, event_time_ms: int):
        """Process incoming trade event from Binance @aggTrade stream."""
        volume_usd = price * qty
        current_min = event_time_ms // 60000

        if symbol not in self.coins:
            self.coins[symbol] = CoinState(symbol)

        coin = self.coins[symbol]

        # Minute rollover logic
        if coin.current_minute is not None and current_min > coin.current_minute:
            asyncio.create_task(self._process_minute_rollover(coin, current_min))

        coin.current_minute = current_min
        coin.current_volume += volume_usd
        if not is_buyer_maker:
            coin.buyer_volume += volume_usd

        coin.trades_count_1m += 1
        coin.last_price = price

        if coin.price_min_1m == 0.0 or price < coin.price_min_1m:
            coin.price_min_1m = price
        if price > coin.price_max_1m:
            coin.price_max_1m = price

    async def _process_minute_rollover(self, coin: CoinState, new_minute: int):
        """Process minute rollover, calculate baseline surge, volatility & evaluate filters."""
        if len(coin.history) >= 1:
            avg_vol = sum(coin.history) / len(coin.history)
            surge_pct = ((coin.current_volume - avg_vol) / avg_vol * 100.0) if avg_vol > 0 else 0.0
            
            p_min = coin.price_min_1m if coin.price_min_1m > 0 else coin.last_price
            p_max = coin.price_max_1m if coin.price_max_1m > 0 else coin.last_price
            volatility_pct = ((p_max - p_min) / p_min * 100.0) if p_min > 0 else 0.0

            coin.last_surge_pct = surge_pct
            coin.last_volatility_pct = volatility_pct

            # Estimate orderbook density heuristics if live depth not subscribed
            if coin.orderbook_density_usd == 0.0:
                coin.orderbook_density_usd = coin.current_volume * 0.15

            # Check Hard Filters
            passes_filters = scoring.check_hard_filters(surge_pct, volatility_pct, coin.current_volume)
            
            if passes_filters:
                score, metrics = scoring.calculate_score(
                    surge_pct,
                    volatility_pct,
                    coin.trades_count_1m,
                    coin.orderbook_density_usd,
                    coin.last_price
                )
                coin.current_score = score

                now = time.time()
                if now >= coin.cooldown_until:
                    coin.cooldown_until = now + (config.COOLDOWN_MINUTES * 60.0)

                    # Log to database WAL
                    row_id = await db_manager.log_anomaly(
                        symbol=coin.symbol,
                        price=coin.last_price,
                        volume_surge_pct=round(surge_pct, 2),
                        volatility_pct=round(volatility_pct, 2),
                        trades_count=coin.trades_count_1m,
                        orderbook_density=round(coin.orderbook_density_usd, 2),
                        calculated_score=score
                    )

                    anomaly_data = {
                        "id": row_id,
                        "symbol": coin.symbol,
                        "price": coin.last_price,
                        "volume_surge_pct": round(surge_pct, 2),
                        "volatility_pct": round(volatility_pct, 2),
                        "trades_count": coin.trades_count_1m,
                        "orderbook_density": round(coin.orderbook_density_usd, 2),
                        "score": score,
                        "current_volume": round(coin.current_volume, 2),
                        "avg_volume": round(avg_vol, 2),
                        "metrics": metrics,
                        "timestamp": time.strftime("%H:%M:%S")
                    }

                    logger.info(f"🚨 [ANOMALY] {coin.symbol} | Score: {score} | Surge: +{surge_pct:.1f}% | Volatility: {volatility_pct:.1f}%")

                    if self.on_anomaly_callback:
                        await self.on_anomaly_callback(anomaly_data)

        # Push completed minute volume to history deque
        coin.history.append(coin.current_volume)

        # Reset minute counters
        coin.current_volume = 0.0
        coin.buyer_volume = 0.0
        coin.trades_count_1m = 0
        coin.price_min_1m = coin.last_price
        coin.price_max_1m = coin.last_price

    async def _connect_ws_chunk(self, symbols_chunk: List[str]):
        """Connect to Binance WebSocket Combined Stream pool for a chunk of symbols."""
        streams = "/".join([f"{s.lower()}@aggTrade" for s in symbols_chunk])
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info(f"Connected to Binance WS pool ({len(symbols_chunk)} symbols).")
                    while self.running:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        stream_data = data.get("data", {})
                        if stream_data and stream_data.get("e") == "aggTrade":
                            symbol = stream_data.get("s")
                            price = float(stream_data.get("p", 0))
                            qty = float(stream_data.get("q", 0))
                            is_buyer_maker = stream_data.get("m", False)
                            event_time = stream_data.get("E", int(time.time() * 1000))
                            self.process_trade(symbol, price, qty, is_buyer_maker, event_time)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Binance WS pool error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def start(self):
        """Fetch symbols and launch WebSocket connection tasks in chunks."""
        self.running = True
        symbols = await self.fetch_usdt_symbols()
        
        # Initialize CoinState objects
        for s in symbols:
            self.coins[s] = CoinState(s)

        # Chunk symbols into groups of 100 to avoid URL length issues
        chunk_size = 100
        chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]

        for chunk in chunks:
            task = asyncio.create_task(self._connect_ws_chunk(chunk))
            self._tasks.append(task)

    async def stop(self):
        """Stop all Binance WebSocket connections."""
        self.running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def get_leaderboard_snapshot(self) -> List[Dict]:
        """Return leaderboard snapshot of all coins sorted by Score."""
        snapshot = []
        for s, coin in self.coins.items():
            if coin.last_price > 0:
                # Instant scoring calculation for live view
                avg_vol = (sum(coin.history) / len(coin.history)) if coin.history else coin.current_volume
                surge_pct = ((coin.current_volume - avg_vol) / avg_vol * 100.0) if avg_vol > 0 else 0.0
                
                p_min = coin.price_min_1m if coin.price_min_1m > 0 else coin.last_price
                p_max = coin.price_max_1m if coin.price_max_1m > 0 else coin.last_price
                volatility_pct = ((p_max - p_min) / p_min * 100.0) if p_min > 0 else 0.0

                density = coin.orderbook_density_usd if coin.orderbook_density_usd > 0 else coin.current_volume * 0.15

                score, metrics = scoring.calculate_score(
                    surge_pct,
                    volatility_pct,
                    coin.trades_count_1m,
                    density,
                    coin.last_price
                )

                snapshot.append({
                    "symbol": s,
                    "price": coin.last_price,
                    "volume_surge_pct": round(surge_pct, 1),
                    "volatility_pct": round(volatility_pct, 1),
                    "trades_count": coin.trades_count_1m,
                    "orderbook_density": round(density, 1),
                    "score": score,
                    "is_under_2usd": coin.last_price <= config.PRICE_PRIORITY_THRESHOLD
                })

        snapshot.sort(key=lambda x: x["score"], reverse=True)
        return snapshot
