"""
core/status_manager.py - パイプラインおよびジョブの実行ステータス管理モジュール (Redis + JSONフォールバック)
"""
from __future__ import annotations
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
from core import cfg as default_cfg
from core.logger import get_logger
from core.redis_client import create_redis_client

logger = get_logger(default_cfg, "status_manager")

FALLBACK_STATUS_FILE = default_cfg.project_root / "data" / "job_status.json"

async def set_pipeline_status(
    status: str, 
    phase: str = "", 
    progress: float = 0.0, 
    details: Optional[Dict[str, Any]] = None,
    cfg: Optional[Any] = None
) -> None:
    """
    パイプライン（WFA）の実行状態を更新してRedisおよびローカルファイルに保存する
    status: 'running' | 'idle' | 'failed' | 'completed'
    """
    target_cfg = cfg or default_cfg
    now_iso = datetime.now(timezone.utc).isoformat()
    
    state_payload = {
        "status": status,
        "is_running": status == "running",
        "phase": phase,
        "progress": float(progress),
        "updated_at": now_iso,
        "details": details or {}
    }
    
    # 1. Redisに保存
    try:
        redis_client = await create_redis_client(target_cfg)
        if redis_client:
            # 個別キーと一括JSONの両方を保存
            await redis_client.set("job_status:wfa", status)
            await redis_client.set("job_status:pipeline_state", json.dumps(state_payload, ensure_ascii=False))
            await redis_client.close()
    except Exception as e:
        logger.debug(f"Redis status update skipped/failed: {e}")

    # 2. ローカルJSONファイルに保存 (フォールバック & 永続化)
    try:
        FALLBACK_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FALLBACK_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(state_payload, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to write fallback status file: {e}")

async def get_pipeline_status(cfg: Optional[Any] = None) -> Dict[str, Any]:
    """
    現在のパイプライン実行状態を取得する
    """
    target_cfg = cfg or default_cfg
    
    # 1. Redisから取得を試みる
    try:
        redis_client = await create_redis_client(target_cfg)
        if redis_client:
            raw_state = await redis_client.get("job_status:pipeline_state")
            await redis_client.close()
            if raw_state:
                return json.loads(raw_state)
    except Exception as e:
        logger.debug(f"Redis get status failed: {e}")

    # 2. ローカルJSONから読み込み
    if FALLBACK_STATUS_FILE.exists():
        try:
            with open(FALLBACK_STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read fallback status file: {e}")

    return {
        "status": "idle",
        "is_running": False,
        "phase": "",
        "progress": 0.0,
        "updated_at": None,
        "details": {}
    }
