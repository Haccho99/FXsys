"""
oanda_api.py (v3 - Final Sync with Core)
"""
from typing import Dict, Any
from core import cfg

def _get_active_profile_key() -> str:
    return cfg.get_sync("oanda.current_profile")

def _get_active_profile_config() -> Dict[str, Any]:
    profile_key = _get_active_profile_key()
    return cfg.get_sync(f"oanda.profiles.{profile_key}", {})

def get_active_account_id() -> str | None:
    profile_cfg = _get_active_profile_config()
    return profile_cfg.get("account_id")

def get_active_environment() -> str | None:
    profile_cfg = _get_active_profile_config()
    return profile_cfg.get("environment")

def get_active_token() -> str | None:
    profile_cfg = _get_active_profile_config()
    return profile_cfg.get("access_token")

def build_oanda_url(endpoint: str) -> str:
    profile_cfg = _get_active_profile_config()
    base_url = profile_cfg.get("api_url")
    if not base_url:
        raise ValueError("API base URL not found in config.json")
    clean_endpoint = endpoint.lstrip('/')
    return f"{base_url}/v3/{clean_endpoint}"

def build_stream_url(endpoint: str):
    profile_cfg = _get_active_profile_config()
    stream_base_url = profile_cfg.get("stream_url")
    if not stream_base_url:
        raise ValueError("Streaming URL not found in config.json")
    clean_endpoint = endpoint.lstrip('/')
    return f"{stream_base_url}/v3/{clean_endpoint}"

def get_mode_capabilities() -> Dict[str, bool]:
    profile_cfg = _get_active_profile_config()
    return {
        "has_orderbook": profile_cfg.get("has_orderbook", False),
        "has_positionbook": profile_cfg.get("has_positionbook", False),
        "has_gslo": profile_cfg.get("has_gslo", False)
    }

import httpx
import asyncio

async def check_oanda_connection() -> bool:
    """
    OANDA API（Practice/Live）との接続ヘルスチェックを実行する。
    アカウントサマリーを取得し、トークンの有効性と接続性を確認する。
    """
    from core.logger import get_logger
    logger = get_logger(cfg, "oanda_health")
    
    account_id = get_active_account_id()
    token = get_active_token()
    url = build_oanda_url(f"accounts/{account_id}/summary")
    
    if not account_id or not token:
        logger.error("OANDA configuration missing (Account ID or Token).")
        return False

    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=5.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                balance = data.get("account", {}).get("balance", "N/A")
                logger.info(f"OANDA Connection Verified. Account ID: {account_id}, Balance: {balance}")
                return True
            else:
                logger.error(f"OANDA Health Check Failed: HTTP {r.status_code} - {r.text}")
                return False
    except Exception as e:
        logger.error(f"OANDA Connection Error: {str(e)}")
        return False