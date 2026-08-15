import logging
from typing import List, Dict, Any, Optional
import aiosqlite

import config

logger = logging.getLogger("Database")

class DatabaseManager:
    def __init__(self, db_path: str = config.DB_PATH):
        self.db_path = db_path

    async def init_db(self) -> None:
        """Initialize SQLite database, apply WAL mode, create tables and indexes."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA synchronous = NORMAL;")
            
            await db.execute("""
            CREATE TABLE IF NOT EXISTS anomaly_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                volume_surge_pct REAL NOT NULL,
                volatility_pct REAL NOT NULL,
                trades_count INTEGER NOT NULL,
                orderbook_density REAL NOT NULL,
                calculated_score REAL NOT NULL,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            await db.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_symbol ON anomaly_logs(symbol);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_score ON anomaly_logs(calculated_score DESC);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_time ON anomaly_logs(detected_at DESC);")

            await db.commit()
            logger.info(f"Database initialized cleanly at '{self.db_path}' (PRAGMA journal_mode = WAL).")

    async def log_anomaly(
        self,
        symbol: str,
        price: float,
        volume_surge_pct: float,
        volatility_pct: float,
        trades_count: int,
        orderbook_density: float,
        calculated_score: float
    ) -> int:
        """Insert anomaly event record into database asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO anomaly_logs (
                    symbol, price, volume_surge_pct, volatility_pct,
                    trades_count, orderbook_density, calculated_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, price, volume_surge_pct, volatility_pct,
                    trades_count, orderbook_density, calculated_score
                )
            )
            await db.commit()
            return cursor.lastrowid

    async def get_recent_anomalies(self, symbol: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieve recent anomaly logs, optionally filtered by symbol."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if symbol:
                query = "SELECT * FROM anomaly_logs WHERE symbol = ? ORDER BY detected_at DESC LIMIT ?"
                params = (symbol, limit)
            else:
                query = "SELECT * FROM anomaly_logs ORDER BY detected_at DESC LIMIT ?"
                params = (limit,)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_total_anomalies_count(self) -> int:
        """Get total count of recorded anomalies in database."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM anomaly_logs") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

db_manager = DatabaseManager()
