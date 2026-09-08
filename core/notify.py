"""
notify.py (v7.2 - NameError Hotfix)
"""
import os
import aiohttp
import smtplib
from email.mime.text import MIMEText
import json
import asyncio
import time
from typing import List, Optional, TYPE_CHECKING
from pathlib import Path

import discord
# ▼▼▼【修正】BotクラスのインポートをTYPE_CHECKINGブロックの外に移動 ▼▼▼
from discord.ext.commands import Bot
from core import cfg
from core.logger import get_logger, log_error

if TYPE_CHECKING:
    from discord.ui import View

logger = get_logger(cfg, "notify")

_bot_instance: Optional[Bot] = None

def set_bot_instance(bot: Bot):
    """
    bot.pyからボットのインスタンスを受け取り、このモジュールで使えるように設定する。
    """
    global _bot_instance
    _bot_instance = bot
    logger.info("Bot instance has been set for the notification module.")

_last_notify_times = {}

async def notify_all(
    message: str, 
    subject: str = "【EA通知】", 
    alert_type: str = "default", 
    embed: Optional[discord.Embed] = None, 
    file: Optional[str] = None,
    view: Optional["View"] = None # Forward reference using string
):
    """
    全ての通知チャネルに通知を送信する。
    ボタン(view)付きメッセージにも対応。
    """
    global _last_notify_times
    try:
        ncfg = cfg.get_sync("notifications", {})
        cooldown_seconds = ncfg.get("cooldown_seconds", 60)

        now = time.time()
        if (now - _last_notify_times.get(alert_type, 0)) < cooldown_seconds:
            logger.info(f"Notification for alert type '{alert_type}' skipped due to cooldown.")
            return

        awaitables = []
        discord_cfg = ncfg.get("discord", {})
        if discord_cfg.get("enabled", False):
            if view and _bot_instance:
                awaitables.append(notify_discord_with_bot(message, embed=embed, file=file, view=view))
            else:
                awaitables.append(notify_discord(message, discord_cfg.get("webhook_url", ""), embed=embed, file=file))
        
        email_cfg = ncfg.get("email", {})
        if email_cfg.get("enabled", False):
            awaitables.append(notify_email(subject, message, email_cfg.get("accounts", [])))

        slack_cfg = ncfg.get("slack", {})
        if slack_cfg.get("enabled", False):
            awaitables.append(notify_slack(message, slack_cfg.get("webhook_url", "")))

        line_cfg = ncfg.get("line", {})
        if line_cfg.get("enabled", False):
            awaitables.append(notify_line(message, line_cfg.get("notify_token", "")))
        
        if awaitables:
            results = await asyncio.gather(*awaitables, return_exceptions=True)
            if any(not isinstance(r, Exception) for r in results):
                _last_notify_times[alert_type] = now
    except Exception as e:
        await log_error(logger, "notify_all", error=e)

async def notify_discord_with_bot(message: str, embed: Optional[discord.Embed] = None, file: Optional[str] = None, view: Optional["View"] = None):
    if not _bot_instance:
        logger.error("Bot instance is not available for sending interactive message.")
        return
    channel_id_str = cfg.get_sync("notifications.discord.channel_id")
    if not channel_id_str:
        logger.error("Discord channel_id is not configured for interactive message.")
        return
    try:
        channel = _bot_instance.get_channel(int(channel_id_str))
        if not channel:
            logger.error(f"Cannot find channel with ID {channel_id_str}")
            return
        send_kwargs = {
            'content': str(message)[:2000] if message else None,
            'embed': embed,
            'view': view
        }
        if file and os.path.exists(file):
            send_kwargs['file'] = discord.File(file, filename=Path(file).name)
        await channel.send(**send_kwargs)
        logger.info("Discord notification sent via Bot.")
    except Exception as e:
        await log_error(logger, "notify_discord_with_bot", error=e)
        raise

async def notify_discord(message: str, webhook_url: str, embed: Optional[discord.Embed] = None, file: Optional[str] = None):
    try:
        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(webhook_url, session=session)
            send_kwargs = {
                'content': str(message)[:2000] if message else None,
                'embed': embed
            }
            if file and os.path.exists(file):
                send_kwargs['file'] = discord.File(file, filename=Path(file).name)
            await webhook.send(**send_kwargs)
        logger.info("Discord notification sent via Webhook")
    except Exception as e:
        await log_error(logger, "notify_discord", error=e)
        raise

async def notify_email(subject, body, emails):
    if not emails: return
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False, indent=2)
    loop = asyncio.get_running_loop()
    for email in emails:
        if not email.get("enabled", True): continue
        try:
            await loop.run_in_executor(None, lambda: send_email(email, subject, body))
            logger.info(f"Email sent to {email['to']}")
        except Exception as e:
            await log_error(logger, f"notify_email_to_{email['to']}", e)
            raise

def send_email(email, subject, body):
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = email["from"]
        msg["To"] = email["to"]
        with smtplib.SMTP(email["smtp"], email["port"]) as server:
            server.starttls()
            server.login(email["user"], email["pass"])
            server.send_message(msg)
    except Exception as e:
        raise Exception(f"Email send error: {e}")

async def notify_slack(message, webhook_url):
    if not webhook_url: return
    try:
        async with aiohttp.ClientSession() as session:
            data = {"text": str(message)[:3000]}
            async with session.post(webhook_url, json=data, timeout=10) as resp:
                resp.raise_for_status()
        logger.info("Slack notification sent")
    except Exception as e:
        await log_error(logger, "notify_slack", e)
        raise

async def notify_line(message, token):
    if not token: return
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {token}"}
            data = {"message": str(message)[:1000]}
            async with session.post("https://notify-api.line.me/api/notify", headers=headers, data=data, timeout=10) as resp:
                resp.raise_for_status()
        logger.info("LINE notification sent")
    except Exception as e:
        await log_error(logger, "notify_line", e)
        raise