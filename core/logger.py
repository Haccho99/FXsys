'''
core/logger.py (v5.2 - Process-safe logging)
'''
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
import logging
from concurrent_log_handler import ConcurrentRotatingFileHandler
from loguru import logger as loguru_logger
import pandas as pd
import polars as pl

if TYPE_CHECKING:
    from core.config_manager import ConfigManager

# --- Loguru (メインロガー) の設定 ---
_loguru_configured_pids = set()

def get_logger(cfg: "ConfigManager", name: str):
    '''
    メインのloguruロガーを取得する。コンソールとプロセス固有のログファイルに出力。
    '''
    pid = os.getpid()
    if pid not in _loguru_configured_pids:
        log_dir = cfg.project_root / cfg.get_sync("system.log_dir", "logs")
        log_dir.mkdir(exist_ok=True)
        
        loguru_logger.remove()
        # コンソール出力
        loguru_logger.add(
            sys.stderr,
            level="INFO",
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> [<level>{level: <8}</level>] <cyan>{extra[name]}</cyan> - <level>{message}</level>",
            colorize=True,
        )
        # プロセス固有のログファイルへの出力
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"system_{timestamp}_{pid}.log"
        loguru_logger.add(
            log_dir / log_filename,
            rotation="10 MB", retention="14 days", level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss} [{level: <8}] [{extra[name]}] {message}",
            encoding="utf-8", enqueue=True
        )
        _loguru_configured_pids.add(pid)
    
    return loguru_logger.bind(name=name)

# --- 標準logging (デバッグ専用ロガー) の設定 ---
_signal_debug_loggers = {}

def get_signal_debug_logger(cfg: "ConfigManager"):
    '''
    signal_debug.log専用の、プロセス固有のPython標準loggingロガーを取得する。
    '''
    pid = os.getpid()
    if pid in _signal_debug_loggers:
        return _signal_debug_loggers[pid]

    log_dir = cfg.project_root / cfg.get_sync("system.log_dir", "logs")
    log_dir.mkdir(exist_ok=True)
    
    logger_name = f"signal_debug_logger_{pid}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    
    if not logger.handlers:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"signal_debug_{timestamp}_{pid}.log"
        signal_log_path = log_dir / log_filename
        
        # ConcurrentRotatingFileHandlerはプロセスセーフだが、ファイル名をユニークにすることでさらに競合を避ける
        handler = ConcurrentRotatingFileHandler(signal_log_path, "a", maxBytes=10 * 1024 * 1024, backupCount=7, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s | %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    _signal_debug_loggers[pid] = logger
    return logger

# --- 既存のヘルパー関数 ---
async def log_error(logger, context: str, error: Exception, **kwargs):
    error_type = type(error).__name__
    error_message = str(error)
    logger.error(f"function_error in {context}", error_type=error_type, error_message=error_message, **kwargs, exc_info=True)
    
    try:
        from core import cfg
        from core.redis_client import create_redis_client
        import json
        from datetime import datetime, timezone
        
        redis_client = await create_redis_client(cfg, logger)
        if redis_client:
            error_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": context,
                "type": error_type,
                "message": error_message
            }
            await redis_client.rpush("system_errors", json.dumps(error_data))
            # 古いログを削除し、最新の1000件のみ保持する
            await redis_client.ltrim("system_errors", -1000, -1)
            await redis_client.close()
    except Exception as e:
        logger.error(f"Failed to push error to Redis system_errors: {e}")

async def append_csv(cfg: "ConfigManager", filename: str, data: pd.DataFrame):
    logger = get_logger(cfg, "append_csv")
    try:
        log_dir = Path(cfg.project_root) / cfg.get_sync("system.log_dir", "logs")
        log_dir.mkdir(exist_ok=True)
        filepath = log_dir / filename
        is_new_file = not filepath.exists() or filepath.stat().st_size == 0
        data.to_csv(filepath, mode='a', header=is_new_file, index=False)
    except Exception as e:
        await log_error(logger, "append_csv", e, filename=filename)

async def save_or_append_parquet(cfg_manager: "ConfigManager", file_name: str, df_to_append: pl.DataFrame):
    logger = get_logger(cfg_manager, "parquet_writer")
    log_dir = Path(cfg_manager.get_sync("system.log_dir", "logs"))
    file_path = log_dir / file_name
    
    try:
        if df_to_append.is_empty():
            logger.warning("Dataframe to append is empty. Skipping.", file_name=file_name)
            return
            
        # ▼ 修正：ファイルロックによるプロセス間競合の防止 ▼
        from filelock import FileLock
        lock_path = f"{file_path}.lock"
        
        # 最大60秒間ロックの取得を待機する
        with FileLock(lock_path, timeout=60):
            if file_path.exists():
                try:
                    existing_df = pl.read_parquet(file_path)
                    combined_df = pl.concat([existing_df, df_to_append], how="diagonal")
                    combined_df.write_parquet(file_path, use_pyarrow=True)
                    logger.info(f"Appended {len(df_to_append)} rows to {file_path}")
                except Exception as e:
                    logger.warning(f"Schema mismatch or append error for {file_path}: {e}. Overwriting file.")
                    df_to_append.write_parquet(file_path, use_pyarrow=True)
            else:
                df_to_append.write_parquet(file_path, use_pyarrow=True)
                logger.info(f"Created new parquet file at {file_path} with {len(df_to_append)} rows.")
        # ▲ 修正 ここまで ▲
        
    except Exception as e:
        await log_error(logger, "save_or_append_parquet", error=e, file_path=str(file_path))