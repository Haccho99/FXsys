"""
log_cleaner.py
指定した日数（デフォルト1日）を過ぎた古いログファイルや一時ファイルを自動削除するツール。
"""
import os
import time
from pathlib import Path
import sys

# プロジェクトルートのパス設定
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from core.config_manager import ConfigManager
from core.logger import get_logger

cfg = ConfigManager(project_root)
logger = get_logger(cfg, "log_cleaner")

def clean_old_files(days_to_keep: float = 1.0):
    """
    days_to_keep 日以上前の古いログや一時ファイルを削除する。
    デフォルトは 1.0日 (24時間)。
    """
    logger.info(f"Starting log cleanup. Target: files older than {days_to_keep} days.")
    
    log_dir = project_root / cfg.get_sync("system.log_dir", "logs")
    if not log_dir.exists():
        logger.warning(f"Log directory not found at {log_dir}")
        return

    # 現在時刻から、指定日数前のタイムスタンプ（ボーダーライン）を計算
    current_time = time.time()
    cutoff_time = current_time - (days_to_keep * 86400) # 86400秒 = 24時間
    deleted_count = 0
    freed_space_bytes = 0

    # 削除対象となるファイルのパターン（増えやすいものを指定）
    patterns = [
        "*.log",                          # 通常のシステムログ
        "*.jsonlog",                      # アプリケーションログ
        "optimize_backtest_log_*.parquet" # WFAの最適化トライアル一時ファイル
    ]

    for pattern in patterns:
        for file_path in log_dir.glob(pattern):
            try:
                # 最終更新日時がボーダーラインより古い場合、削除
                if file_path.stat().st_mtime < cutoff_time:
                    file_size = file_path.stat().st_size
                    file_path.unlink()
                    deleted_count += 1
                    freed_space_bytes += file_size
            except Exception as e:
                logger.error(f"Failed to delete {file_path.name}: {e}")
    
    freed_mb = freed_space_bytes / (1024 * 1024)
    logger.info(f"Log cleanup finished. Deleted {deleted_count} files. Freed {freed_mb:.2f} MB of disk space.")

if __name__ == "__main__":
    # 手動実行時はすぐに掃除を行う（1日以上前のものを削除）
    clean_old_files(days_to_keep=1.0)