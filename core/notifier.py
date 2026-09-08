"""
core/notifier.py - システム通知モジュール (Discord Webhook / メール等)
"""
from __future__ import annotations
import json
import logging
import urllib.request
import urllib.error
import aiohttp
from typing import Optional, Dict, Any
from pathlib import Path
from core import cfg as default_cfg
from core.logger import get_logger

logger = get_logger(default_cfg, "notifier")

def send_discord_message(
    content: str, 
    embed: Optional[Dict[str, Any]] = None, 
    cfg: Optional[Any] = None
) -> bool:
    """
    Discord Webhookにメッセージを送信する（同期版）
    """
    target_cfg = cfg or default_cfg
    discord_cfg = target_cfg.get_sync("notifications.discord", {})
    if not discord_cfg.get("enabled", True):
        logger.debug("Discord notifications are disabled in config.")
        return False
        
    webhook_url = discord_cfg.get("webhook_url", "").strip()
    if not webhook_url or "YOUR_WEBHOOK" in webhook_url:
        logger.warning("Discord webhook URL is not configured.")
        return False

    payload: Dict[str, Any] = {"content": content}
    if embed:
        payload["embeds"] = [embed]

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "WealthSystem-FXsys/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                logger.info("Discord notification sent successfully.")
                return True
            else:
                logger.warning(f"Discord webhook returned HTTP status {resp.status}")
                return False
    except Exception as e:
        logger.error(f"Failed to send Discord message: {e}")
        return False

async def send_discord_message_async(
    content: str, 
    embed: Optional[Dict[str, Any]] = None, 
    cfg: Optional[Any] = None
) -> bool:
    """
    Discord Webhookにメッセージを非同期送信する
    """
    target_cfg = cfg or default_cfg
    discord_cfg = target_cfg.get_sync("notifications.discord", {})
    if not discord_cfg.get("enabled", True):
        return False
        
    webhook_url = discord_cfg.get("webhook_url", "").strip()
    if not webhook_url or "YOUR_WEBHOOK" in webhook_url:
        return False

    payload: Dict[str, Any] = {"content": content}
    if embed:
        payload["embeds"] = [embed]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url, 
                json=payload, 
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status in (200, 204):
                    logger.info("Discord async notification sent successfully.")
                    return True
                else:
                    logger.warning(f"Discord async webhook returned status {resp.status}")
                    return False
    except Exception as e:
        logger.error(f"Failed to send Discord async message: {e}")
        return False
