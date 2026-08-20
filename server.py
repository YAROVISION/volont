import asyncio
import json
import logging
import os
import sys
import time
from typing import Set

from aiohttp import web
import httpx

import config
from database import db_manager
from binance_stream import BinanceStreamManager
from telegram_bot import telegram_manager

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("AnomalyRadar")

connected_clients: Set[web.WebSocketResponse] = set()
stream_manager: BinanceStreamManager = None


routes = web.RouteTableDef()


async def broadcast_anomaly(anomaly_data: dict):
    """Broadcast newly detected anomaly to all active WebSocket clients."""
    if not connected_clients:
        return
    msg = json.dumps({"type": "anomaly", "data": anomaly_data})
    for ws in list(connected_clients):
        try:
            await ws.send_str(msg)
        except Exception:
            connected_clients.discard(ws)


async def periodic_snapshot_broadcast():
    """Broadcast completed 1-minute summary leaderboard snapshots to clients once per minute (every 60 seconds)."""
    while True:
        await asyncio.sleep(60.0)
        if connected_clients and stream_manager:
            try:
                snapshot = stream_manager.get_leaderboard_snapshot()
                db_count = await db_manager.get_total_anomalies_count()
                msg = json.dumps({
                    "type": "snapshot",
                    "total_pairs": len(stream_manager.coins),
                    "db_records": db_count,
                    "data": snapshot
                })
                for ws in list(connected_clients):
                    try:
                        await ws.send_str(msg)
                    except Exception:
                        connected_clients.discard(ws)
            except Exception as e:
                logger.error(f"Error broadcasting snapshot: {e}")





async def urgent_emergency_monitor_loop():
    """Background task running once per minute (60 seconds) checking 1-minute volume against base minimum for all urgent monitored pairs."""
    logger.info("Started Urgent Emergency 1-Minute Volume Monitoring Loop (Check Frequency: 1 min).")
    while True:
        try:
            pairs = await db_manager.get_urgent_pairs()
            if pairs:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    for pair in pairs:
                        if not pair.get("enabled"):
                            continue
                        symbol = pair["symbol"]
                        base_minimum = float(pair["base_minimum"])
                        base_currency = symbol.replace("USDT", "")

                        resp = await client.get(f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&limit=2")
                        if resp.status_code == 200:
                            klines = resp.json()
                            if klines:
                                latest_1m = klines[-1]
                                vol_1m_coins = float(latest_1m[5])
                                vol_1m_usdt = float(latest_1m[7])
                                price = float(latest_1m[4])

                                # Check if 1-minute volume in base currency exceeds threshold
                                if vol_1m_coins >= base_minimum:
                                    now = time.time()
                                    last_alert_ts = pair.get("last_alert_timestamp", 0) or 0
                                    # 5 minutes cooldown per pair
                                    if now - last_alert_ts >= 300:
                                        surge_pct = ((vol_1m_coins - base_minimum) / base_minimum * 100.0) if base_minimum > 0 else 0.0
                                        sent_at_str = time.strftime("%Y-%m-%d %H:%M:%S")

                                        res = await telegram_manager.send_urgent_alert(
                                            symbol=symbol,
                                            base_currency=base_currency,
                                            vol_1m_coins=vol_1m_coins,
                                            vol_1m_usdt=vol_1m_usdt,
                                            base_minimum=base_minimum,
                                            surge_pct=surge_pct,
                                            price=price,
                                            sent_at=sent_at_str
                                        )

                                        if res.get("ok"):
                                            await db_manager.log_urgent_alert(
                                                symbol=symbol,
                                                base_minimum=base_minimum,
                                                volume_coins=vol_1m_coins,
                                                surge_pct=surge_pct,
                                                price=price
                                            )
                                            await db_manager.update_urgent_pair_last_alert(
                                                symbol=symbol,
                                                last_alert_time=sent_at_str,
                                                last_alert_price=price,
                                                last_alert_timestamp=now
                                            )

                                            # Broadcast to WS clients
                                            msg = json.dumps({
                                                "type": "urgent_alert",
                                                "data": {
                                                    "symbol": symbol,
                                                    "base_currency": base_currency,
                                                    "vol_1m_coins": vol_1m_coins,
                                                    "base_minimum": base_minimum,
                                                    "surge_pct": round(surge_pct, 1),
                                                    "price": price,
                                                    "sent_at": sent_at_str
                                                }
                                            })
                                            for ws in list(connected_clients):
                                                try:
                                                    await ws.send_str(msg)
                                                except Exception:
                                                    pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in urgent emergency monitor loop: {e}")

        # Wait exactly 60 seconds (1 minute check frequency)
        await asyncio.sleep(60.0)


@routes.get('/ws')
@routes.get('/ws/')
async def websocket_handler(request):
    """WebSocket endpoint for frontend screener client."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    connected_clients.add(ws)
    logger.info(f"Client connected to WS: {request.remote}")

    # Send immediate initial snapshot to newly connected client
    if stream_manager:
        try:
            snapshot = stream_manager.get_leaderboard_snapshot()
            db_count = await db_manager.get_total_anomalies_count()
            msg = json.dumps({
                "type": "snapshot",
                "total_pairs": len(stream_manager.coins),
                "db_records": db_count,
                "data": snapshot
            })
            await ws.send_str(msg)
        except Exception:
            pass

    try:
        async for msg in ws:
            pass
    finally:
        connected_clients.discard(ws)
        logger.info(f"Client disconnected from WS: {request.remote}")
    return ws


@routes.get('/api/anomalies')
@routes.get('/api/anomalies/')
async def get_anomalies(request):
    """REST API endpoint to retrieve anomaly logs from SQLite."""
    symbol = request.query.get('symbol')
    limit = int(request.query.get('limit', 10))
    logs = await db_manager.get_recent_anomalies(symbol=symbol, limit=limit)
    return web.json_response(logs)





@routes.get('/api/urgent/pairs')
async def get_urgent_pairs_route(request):
    """REST API endpoint to retrieve all urgent monitored pairs enriched with live Binance 1m volume & price."""
    try:
        pairs = await db_manager.get_urgent_pairs()
        enriched_pairs = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for pair in pairs:
                symbol = pair["symbol"]
                base_minimum = float(pair["base_minimum"])
                base_currency = symbol.replace("USDT", "")

                vol_1m_coins = 0.0
                vol_1m_usdt = 0.0
                price = 0.0
                price_change_24h = 0.0

                try:
                    k_resp = await client.get(f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&limit=2")
                    t_resp = await client.get(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}")
                    if k_resp.status_code == 200 and k_resp.json():
                        latest_1m = k_resp.json()[-1]
                        vol_1m_coins = float(latest_1m[5])
                        vol_1m_usdt = float(latest_1m[7])
                        price = float(latest_1m[4])
                    if t_resp.status_code == 200 and t_resp.json():
                        price_change_24h = float(t_resp.json().get("priceChangePercent", 0.0))
                except Exception as ex:
                    logger.warning(f"Failed to fetch live klines for {symbol}: {ex}")

                surge_pct = ((vol_1m_coins - base_minimum) / base_minimum * 100.0) if base_minimum > 0 else 0.0
                progress_pct = (vol_1m_coins / base_minimum * 100.0) if base_minimum > 0 else 0.0

                enriched_pairs.append({
                    "symbol": symbol,
                    "base_currency": base_currency,
                    "base_minimum": base_minimum,
                    "enabled": bool(pair.get("enabled", 1)),
                    "last_alert_time": pair.get("last_alert_time") or "Не надсилалося",
                    "last_alert_price": pair.get("last_alert_price"),
                    "vol_1m_coins": vol_1m_coins,
                    "vol_1m_usdt": vol_1m_usdt,
                    "price": price,
                    "price_change_24h": price_change_24h,
                    "surge_pct": round(surge_pct, 1),
                    "progress_pct": round(progress_pct, 1)
                })

        return web.json_response({"ok": True, "pairs": enriched_pairs})
    except Exception as e:
        logger.error(f"Error getting urgent pairs: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.post('/api/urgent/pairs')
async def save_urgent_pair_route(request):
    """Add or update an urgent monitored pair configuration."""
    try:
        data = await request.json()
        symbol = data.get("symbol", "").strip().upper()
        base_minimum = float(data.get("base_minimum", 0.0))
        enabled = bool(data.get("enabled", True))

        if not symbol:
            return web.json_response({"ok": False, "error": "Не вказано символ пари."}, status=400)
        if base_minimum <= 0:
            return web.json_response({"ok": False, "error": "Базовий мінімум повинен бути більшим за 0."}, status=400)

        if not symbol.endswith("USDT"):
            symbol += "USDT"

        await db_manager.save_urgent_pair(symbol, base_minimum, enabled)
        return web.json_response({"ok": True, "message": f"Пару {symbol} успішно збережено!"})
    except Exception as e:
        logger.error(f"Error saving urgent pair: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=400)


@routes.delete('/api/urgent/pairs/{symbol}')
async def delete_urgent_pair_route(request):
    """Delete an urgent monitored pair."""
    try:
        symbol = request.match_info.get("symbol", "").strip().upper()
        if symbol:
            await db_manager.delete_urgent_pair(symbol)
        return web.json_response({"ok": True, "message": f"Пару {symbol} видалено з моніторингу."})
    except Exception as e:
        logger.error(f"Error deleting urgent pair: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=400)


@routes.get('/api/urgent/logs')
async def get_urgent_logs_route(request):
    """Get history of dispatched urgent alerts."""
    try:
        symbol = request.query.get("symbol")
        limit = int(request.query.get("limit", 50))
        logs = await db_manager.get_urgent_alert_logs(symbol=symbol, limit=limit)
        return web.json_response({"ok": True, "logs": logs})
    except Exception as e:
        logger.error(f"Error getting urgent logs: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.post('/api/urgent/test')
async def trigger_urgent_test_route(request):
    """Trigger a test Telegram alert for a specific urgent pair."""
    try:
        data = await request.json()
        symbol = data.get("symbol", "ACEUSDT").strip().upper()
        res = await telegram_manager.send_urgent_test_alert(symbol)
        if res.get("ok"):
            return web.json_response({"ok": True, "message": f"Тестове сповіщення для {symbol} надіслано в Telegram!"})
        else:
            return web.json_response({"ok": False, "error": res.get("error", "Не вдалося надіслати тестовий сигнал.")}, status=400)
    except Exception as e:
        logger.error(f"Error triggering urgent test alert: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.get('/urgent')
@routes.get('/urgent.html')
async def urgent_page_handler(request):
    """Serve Urgent Monitoring page (urgent.html)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(base_dir, "static")
    return web.FileResponse(os.path.join(static_dir, "urgent.html"))


@routes.get('/')
async def index_handler(request):
    """Serve SPA index.html."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(base_dir, "static")
    return web.FileResponse(os.path.join(static_dir, "index.html"))


# Static files route
static_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
routes.static('/static', static_folder)


async def start_background_tasks(app):
    # 1. Initialize SQLite Database WAL
    await db_manager.init_db()

    # 2. Initialize Binance Combined Stream Manager
    global stream_manager
    stream_manager = BinanceStreamManager(on_anomaly_callback=broadcast_anomaly)
    await stream_manager.start()

    # 3. Start snapshot broadcast loop & Urgent monitor
    app['snapshot_task'] = asyncio.create_task(periodic_snapshot_broadcast())
    app['urgent_monitor_task'] = asyncio.create_task(urgent_emergency_monitor_loop())


async def cleanup_background_tasks(app):
    if stream_manager:
        await stream_manager.stop()
    if 'snapshot_task' in app:
        app['snapshot_task'].cancel()
    if 'urgent_monitor_task' in app:
        app['urgent_monitor_task'].cancel()


def main():
    app = web.Application()
    app.add_routes(routes)

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)

    logger.info(f"🚀 BINANCE ANOMALY RADAR running at http://localhost:{config.LOCAL_WS_PORT}/")
    web.run_app(app, host=config.LOCAL_WS_HOST, port=config.LOCAL_WS_PORT, print=None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Server stopped by user.")

