import logging
import os
import httpx
from typing import Optional, Dict, Any

import config

logger = logging.getLogger("TelegramBot")


class TelegramBotManager:
    def __init__(self):
        self.token: str = config.TELEGRAM_TOKEN
        self.chat_id: str = config.TELEGRAM_CHAT_ID

    def set_credentials(self, token: str, chat_id: str):
        """Update Telegram bot credentials in memory."""
        self.token = token.strip()
        self.chat_id = chat_id.strip()

    async def send_message(self, text: str, parse_mode: str = "HTML") -> Dict[str, Any]:
        """Send asynchronous Telegram message via Bot API."""
        token = self.token or config.TELEGRAM_TOKEN or os.getenv("TELEGRAM_TOKEN", "")
        chat_id = self.chat_id or config.TELEGRAM_CHAT_ID or os.getenv("TELEGRAM_CHAT_ID", "")

        if not token or not chat_id:
            logger.warning("Telegram Bot Token or Chat ID not configured in .env. Message suppressed.")
            return {"ok": False, "error": "Telegram Bot Token або Chat ID відсутні у файлі .env."}

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if resp.status_code == 200 and data.get("ok"):
                    logger.info(f"Telegram alert sent successfully to Chat ID: {chat_id}")
                    return {"ok": True, "result": data.get("result")}
                else:
                    err_msg = data.get("description", f"HTTP {resp.status_code}")
                    logger.error(f"Failed to send Telegram message: {err_msg}")
                    return {"ok": False, "error": err_msg}
        except Exception as e:
            logger.error(f"Exception during Telegram API request: {e}")
            return {"ok": False, "error": str(e)}



    async def send_urgent_alert(
        self,
        symbol: str,
        base_currency: str,
        vol_1m_coins: float,
        vol_1m_usdt: float,
        base_minimum: float,
        surge_pct: float,
        price: float,
        sent_at: str
    ) -> Dict[str, Any]:
        """Dispatch Urgent 1-Minute Volume Surge alert to Telegram."""
        text = (
            f"🚨 <b>ТЕРМІНОВЕ СПОВІЩЕННЯ: {symbol}</b> 🚨\n\n"
            f"📊 <b>Пара:</b> <code>{symbol}</code>\n"
            f"🪙 <b>1-хв Обсяг:</b> <code>{vol_1m_coins:,.0f} {base_currency}</code> (<code>${vol_1m_usdt:,.2f} USDT</code>)\n"
            f"⚓ <b>Базовий мінімум:</b> <code>{base_minimum:,.0f} {base_currency}</code>\n"
            f"📈 <b>Перевищення порогу:</b> <code>+{surge_pct:.1f}%</code>\n"
            f"💵 <b>Ціна при відправці:</b> <code>${price:.4f} USDT</code>\n"
            f"⏰ <b>Час спрацювання:</b> <code>{sent_at}</code>\n\n"
            f"⚠️ <i>Перевищено 1-хвилинний базовий мінімум на Binance Futures!</i>"
        )
        return await self.send_message(text)

    async def send_urgent_test_alert(self, symbol: str) -> Dict[str, Any]:
        """Send test message for urgent pair monitoring."""
        text = (
            f"🧪 <b>ТЕСТОВЕ ТЕРМІНОВЕ СПОВІЩЕННЯ</b> 🧪\n\n"
            f"✅ Система моніторингу для пари <b>{symbol}</b> успішно налаштована!\n"
            f"🔔 Перевірка 1-хвилинного обсягу здійснюється кожні 60 секунд.\n\n"
            f"<i>Тест виконано успішно.</i>"
        )
        return await self.send_message(text)

    async def send_test_alert(self) -> Dict[str, Any]:
        """Send test verification message to Telegram."""
        text = (
            f"🧪 <b>ТЕСТОВЕ СПОВІЩЕННЯ BINANCE ANOMALY RADAR</b> 🧪\n\n"
            f"✅ Система аварійних сповіщень для <b>ACEUSDT Futures</b> успішно налаштована!\n"
            f"🔔 Ви отримуватимете алярми у разі зростання проторгованого обсягу монети.\n\n"
            f"<i>Тест виконано успішно.</i>"
        )
        return await self.send_message(text)


telegram_manager = TelegramBotManager()
