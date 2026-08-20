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

            await db.execute("""
            CREATE TABLE IF NOT EXISTS emergency_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                surge_pct REAL NOT NULL,
                price REAL NOT NULL,
                volume_usd REAL NOT NULL,
                avg_volume_usd REAL NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS alert_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS urgent_monitored_pairs (
                symbol TEXT PRIMARY KEY,
                base_minimum REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_alert_time TEXT,
                last_alert_price REAL,
                last_alert_timestamp REAL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS urgent_alert_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                base_minimum REAL NOT NULL,
                volume_coins REAL NOT NULL,
                surge_pct REAL NOT NULL,
                price REAL NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # Seed default ACEUSDT if empty
            async with db.execute("SELECT COUNT(*) FROM urgent_monitored_pairs") as cursor:
                row = await cursor.fetchone()
                if row and row[0] == 0:
                    await db.execute(
                        "INSERT INTO urgent_monitored_pairs (symbol, base_minimum, enabled) VALUES (?, ?, ?)",
                        ("ACEUSDT", 300000000.0, 1)
                    )

            await db.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_symbol ON anomaly_logs(symbol);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_score ON anomaly_logs(calculated_score DESC);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_time ON anomaly_logs(detected_at DESC);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_emergency_symbol ON emergency_alerts(symbol);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_urgent_log_symbol ON urgent_alert_logs(symbol);")

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

    async def log_emergency_alert(
        self,
        symbol: str,
        surge_pct: float,
        price: float,
        volume_usd: float,
        avg_volume_usd: float
    ) -> int:
        """Log emergency alert event into database."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO emergency_alerts (
                    symbol, surge_pct, price, volume_usd, avg_volume_usd
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (symbol, surge_pct, price, volume_usd, avg_volume_usd)
            )
            await db.commit()
            return cursor.lastrowid

    async def get_emergency_alerts(self, symbol: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieve recent emergency alerts from database."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if symbol:
                query = "SELECT * FROM emergency_alerts WHERE symbol = ? ORDER BY sent_at DESC LIMIT ?"
                params = (symbol, limit)
            else:
                query = "SELECT * FROM emergency_alerts ORDER BY sent_at DESC LIMIT ?"
                params = (limit,)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def save_alert_setting(self, key: str, value: str) -> None:
        """Save or update alert setting key-value pair."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO alert_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value))
            )
            await db.commit()

    async def get_alert_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get alert setting by key."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT value FROM alert_settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default

    async def get_all_alert_settings(self) -> Dict[str, str]:
        """Retrieve all alert settings as a dictionary."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT key, value FROM alert_settings") as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}

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

    # --- URGENT MONITORED PAIRS & ALERTS ---
    async def get_urgent_pairs(self) -> List[Dict[str, Any]]:
        """Retrieve all urgent monitored pairs."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM urgent_monitored_pairs ORDER BY symbol ASC") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def save_urgent_pair(self, symbol: str, base_minimum: float, enabled: bool = True) -> None:
        """Add or update an urgent monitored pair."""
        sym = symbol.strip().upper()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO urgent_monitored_pairs (symbol, base_minimum, enabled)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    base_minimum=excluded.base_minimum,
                    enabled=excluded.enabled,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (sym, float(base_minimum), 1 if enabled else 0)
            )
            await db.commit()

    async def update_urgent_pair_last_alert(
        self,
        symbol: str,
        last_alert_time: str,
        last_alert_price: float,
        last_alert_timestamp: float
    ) -> None:
        """Update last alert dispatch timestamp and price for a pair."""
        sym = symbol.strip().upper()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE urgent_monitored_pairs
                SET last_alert_time = ?, last_alert_price = ?, last_alert_timestamp = ?
                WHERE symbol = ?
                """,
                (last_alert_time, last_alert_price, last_alert_timestamp, sym)
            )
            await db.commit()

    async def delete_urgent_pair(self, symbol: str) -> None:
        """Remove a pair from urgent monitoring."""
        sym = symbol.strip().upper()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM urgent_monitored_pairs WHERE symbol = ?", (sym,))
            await db.commit()

    async def log_urgent_alert(
        self,
        symbol: str,
        base_minimum: float,
        volume_coins: float,
        surge_pct: float,
        price: float
    ) -> int:
        """Log an urgent alert event into database."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO urgent_alert_logs (
                    symbol, base_minimum, volume_coins, surge_pct, price
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (symbol.strip().upper(), base_minimum, volume_coins, surge_pct, price)
            )
            await db.commit()
            return cursor.lastrowid

    async def get_urgent_alert_logs(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent urgent alert logs."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if symbol:
                query = "SELECT * FROM urgent_alert_logs WHERE symbol = ? ORDER BY sent_at DESC LIMIT ?"
                params = (symbol.strip().upper(), limit)
            else:
                query = "SELECT * FROM urgent_alert_logs ORDER BY sent_at DESC LIMIT ?"
                params = (limit,)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

db_manager = DatabaseManager()

