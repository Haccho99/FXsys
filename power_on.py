"""
power_on.py (v10 - Supervisor/Watchdog & Auto-Recovery) - 仮想フォワードシステム用 統合オーケストレーター
"""
from __future__ import annotations
import asyncio
import subprocess
import sys
import os
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any

# プロジェクトルートの設定とConfig初期化
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from core.config_manager import ConfigManager
cfg = ConfigManager(project_root)

from core.logger import log_error, get_logger
from core.redis_client import create_redis_client

logger = get_logger(cfg, "power_on")

async def trim_error_logs():
    """古いエラーログを自動削除し、Redisのメモリを節約する"""
    logger.info("Starting old error log trimming process...")
    try:
        await asyncio.sleep(2) 
        redis_client = await create_redis_client(cfg)
        if not redis_client:
            logger.error("Cannot trim logs, Redis connection not available.")
            return

        key = "system_errors"
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
        
        all_errors_json = await redis_client.lrange(key, 0, -1)
        if not all_errors_json:
            logger.info("No error logs to trim.")
            await redis_client.close()
            return

        valid_errors = []
        for error_str in all_errors_json:
            try:
                error_data = json.loads(error_str)
                timestamp_str = error_data.get("timestamp")
                if timestamp_str:
                    if timestamp_str.endswith('Z'):
                        timestamp_str = timestamp_str[:-1] + '+00:00'
                    
                    error_date = datetime.fromisoformat(timestamp_str)
                    if error_date.tzinfo is None:
                        error_date = error_date.replace(tzinfo=timezone.utc)
                    
                    if error_date >= cutoff_date:
                        valid_errors.append(error_str)
            except (json.JSONDecodeError, TypeError, ValueError):
                valid_errors.append(error_str)

        if len(valid_errors) < len(all_errors_json):
            async with redis_client.pipeline() as pipe:
                pipe.delete(key)
                if valid_errors:
                    pipe.rpush(key, *valid_errors)
                await pipe.execute()
            trimmed_count = len(all_errors_json) - len(valid_errors)
            logger.info(f"Successfully trimmed {trimmed_count} old error logs.")
        else:
            logger.info("No logs were old enough to be trimmed.")
            
        await redis_client.close()
    except Exception as e:
        logger.error(f"An error occurred during log trimming: {e}", exc_info=True)


def start_process(name: str, cmd: list[str]) -> subprocess.Popen:
    """プロセスを起動してロギングする"""
    proc = subprocess.Popen(cmd, cwd=str(project_root))
    logger.info(f"🚀 [{name}] Started successfully (PID: {proc.pid}).")
    return proc


async def main():
    """3つの主要コンポーネントを起動し、死活監視（Watchdog）および自動再起動を行う"""
    asyncio.create_task(trim_error_logs())

    # 管理対象コンポーネントの定義
    components: Dict[str, Dict[str, Any]] = {
        "Trading Engine": {
            "cmd": [sys.executable, str(project_root / "virtual_main_loop.py")],
            "proc": None,
            "restart_count": 0,
            "last_crash_time": 0
        },
        "Scheduler": {
            "cmd": [sys.executable, str(project_root / "scheduler.py")],
            "proc": None,
            "restart_count": 0,
            "last_crash_time": 0
        }
    }

    is_shutting_down = False

    try:
        logger.info("=" * 60)
        logger.info("  POWER ON: Starting System Components with Supervisor/Watchdog")
        logger.info("=" * 60)

        # 初期一括起動
        for name, info in components.items():
            info["proc"] = start_process(name, info["cmd"])
            await asyncio.sleep(1) # 順次起動でリソース競合を防ぐ

        logger.info(f"\n✅ All {len(components)} components are running under Supervisor protection.")
        logger.info("Press [Ctrl + C] in this console to safely shut down everything.\n")

        # ＝＝＝ 死活監視 & 自動復旧（Watchdog）ループ ＝＝＝
        while not is_shutting_down:
            await asyncio.sleep(2)

            for name, info in components.items():
                proc: subprocess.Popen = info["proc"]
                if proc is None:
                    continue

                poll_result = proc.poll()
                if poll_result is not None:
                    # プロセスが終了・クラッシュしているのを検知
                    now_ts = time.time()
                    info["restart_count"] += 1
                    logger.warning(
                        f"⚠️ [Watchdog Alert] Component '{name}' (PID: {proc.pid}) has exited unexpectedly "
                        f"(Exit Code: {poll_result}). Attempting automatic recovery (Restart #{info['restart_count']})..."
                    )
                    
                    # 連続クラッシュ対策（1秒待機）
                    await asyncio.sleep(1)
                    info["proc"] = start_process(name, info["cmd"])
                    info["last_crash_time"] = now_ts
                    logger.info(f"🔄 [Watchdog Recovery] Component '{name}' has been successfully restored (New PID: {info['proc'].pid}).")

    except asyncio.CancelledError:
        logger.info("Main task cancelled. Initiating shutdown...")
    except KeyboardInterrupt:
        logger.info("System shutdown initiated by user (Ctrl+C).")
    except Exception as e:
        logger.critical("An unexpected error occurred in power_on supervisor.", exc_info=True)
    finally:
        is_shutting_down = True
        logger.info("\n" + "=" * 60)
        logger.info("  POWER OFF: Shutting down all components gracefully...")
        logger.info("=" * 60)

        for name, info in components.items():
            proc = info.get("proc")
            if proc and proc.poll() is None:
                logger.info(f"Terminating {name} (PID: {proc.pid})...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning(f"{name} did not terminate in time. Forcing kill...")
                    proc.kill()

        logger.info("✅ System shutdown complete.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass