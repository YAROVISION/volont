import asyncio
import json
import logging
import os
import sys
import time
from typing import Set

from aiohttp import web

import config
from database import db_manager
from binance_stream import BinanceStreamManager

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
    """Broadcast leaderboard snapshots to clients 3 times per second."""
    while True:
        await asyncio.sleep(0.3)
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


@routes.get('/ws')
async def websocket_handler(request):
    """WebSocket endpoint for frontend screener client."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    connected_clients.add(ws)
    logger.info(f"Client connected to WS: {request.remote}")
    try:
        async for msg in ws:
            pass
    finally:
        connected_clients.discard(ws)
        logger.info(f"Client disconnected from WS: {request.remote}")
    return ws


@routes.get('/api/anomalies')
async def get_anomalies(request):
    """REST API endpoint to retrieve anomaly logs from SQLite."""
    symbol = request.query.get('symbol')
    limit = int(request.query.get('limit', 10))
    logs = await db_manager.get_recent_anomalies(symbol=symbol, limit=limit)
    return web.json_response(logs)


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

    # 3. Start snapshot broadcast loop
    app['snapshot_task'] = asyncio.create_task(periodic_snapshot_broadcast())


async def cleanup_background_tasks(app):
    if stream_manager:
        await stream_manager.stop()
    if 'snapshot_task' in app:
        app['snapshot_task'].cancel()


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
