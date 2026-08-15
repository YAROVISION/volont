from typing import Dict, Any, Tuple
import config

def check_hard_filters(volume_surge_pct: float, volatility_pct: float, current_vol_usd: float) -> bool:
    """
    Check mandatory Hard Filters:
    1. Volume Surge >= 150%
    2. Price Volatility >= 10%
    3. Current 1m Volume >= $10,000
    """
    if volume_surge_pct < config.MIN_SURGE_PCT:
        return False
    if volatility_pct < config.MIN_VOLATILITY_PCT:
        return False
    if current_vol_usd < config.MIN_VOLUME_USD:
        return False
    return True


def calculate_score(
    volume_surge_pct: float,
    volatility_pct: float,
    trades_per_min: int,
    orderbook_density_usd: float,
    price: float
) -> Tuple[float, Dict[str, float]]:
    """
    Calculate 5-metric scoring score (0 - 100 scale):
    1. S_vol: Normalized volume surge (150% -> ~30pts, 500%+ -> 100pts)
    2. S_vola: Normalized volatility (10% -> ~33pts, 30%+ -> 100pts)
    3. S_trades: Normalized trade count per minute (3000 trades/m -> 100pts)
    4. S_depth: Orderbook liquidity density ($1M depth -> 100pts)
    5. S_price: Priority boost for <= 2 USDT tokens (100pts for <=$2, drops gracefully above $2)
    """
    # 1. Volume Surge score
    s_vol = min(100.0, max(0.0, (volume_surge_pct / 500.0) * 100.0))

    # 2. Volatility score
    s_vola = min(100.0, max(0.0, (volatility_pct / 30.0) * 100.0))

    # 3. Trades count score
    s_trades = min(100.0, max(0.0, (trades_per_min / 3000.0) * 100.0))

    # 4. Orderbook depth density score
    s_depth = min(100.0, max(0.0, (orderbook_density_usd / 1000000.0) * 100.0))

    # 5. Price boost score (<= 2 USDT priority)
    if price <= config.PRICE_PRIORITY_THRESHOLD:
        s_price = 100.0
    else:
        s_price = min(100.0, max(0.0, (config.PRICE_PRIORITY_THRESHOLD / price) * 100.0))

    # Integrated Score calculation
    total_score = (
        (config.WEIGHT_VOL * s_vol) +
        (config.WEIGHT_VOLA * s_vola) +
        (config.WEIGHT_TRADES * s_trades) +
        (config.WEIGHT_DEPTH * s_depth) +
        (config.WEIGHT_PRICE * s_price)
    )

    metrics = {
        "s_vol": round(s_vol, 1),
        "s_vola": round(s_vola, 1),
        "s_trades": round(s_trades, 1),
        "s_depth": round(s_depth, 1),
        "s_price": round(s_price, 1)
    }

    return round(total_score, 1), metrics
