import os
from typing import Dict

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- NETWORK & SERVER CONFIGURATION ---
LOCAL_WS_HOST: str = "0.0.0.0"
LOCAL_WS_PORT: int = int(os.getenv("LOCAL_WS_PORT", 8765))
HTTP_PORT: int = int(os.getenv("HTTP_PORT", 8765))

# --- DATABASE CONFIGURATION ---
DB_PATH: str = os.getenv("DB_PATH", "anomalies.db")

# --- TELEGRAM CONFIGURATION ---
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# --- HARD FILTER THRESHOLDS ---
MIN_SURGE_PCT: float = 150.0       # Volume surge >= 150% over moving baseline
MIN_VOLATILITY_PCT: float = 10.0   # Price amplitude volatility >= 10%
PRICE_PRIORITY_THRESHOLD: float = 2.0  # Tokens <= 2.0 USDT get price boost
MIN_VOLUME_USD: float = 10000.0    # Ignore low liquidity spikes (<$10k)
COOLDOWN_MINUTES: float = 5.0      # Timeout between repeated anomaly alerts per symbol
SLIDING_WINDOW_MINUTES: int = 14   # Baseline history window (14 minutes)

# --- SCORING ENGINE WEIGHTS (Sum = 1.0) ---
WEIGHT_VOL: float = 0.35      # Volume surge component weight
WEIGHT_VOLA: float = 0.25     # Volatility component weight
WEIGHT_TRADES: float = 0.15   # Trade density component weight
WEIGHT_DEPTH: float = 0.15    # Orderbook depth component weight
WEIGHT_PRICE: float = 0.10    # Sub-2$ price priority weight
