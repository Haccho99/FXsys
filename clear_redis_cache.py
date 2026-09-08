# clear_redis_cache.py
from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートをsys.pathに追加して、coreモジュールをインポート可能にする
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.config_manager import ConfigManager
from core.redis_client import create_redis_client_sync

def main():
    """
    設定ファイルからRedisの接続情報を読み込み、キャッシュをクリアする。
    """
    print("--- Self-Contained Redis Cache Clearer ---")
    try:
        cfg = ConfigManager(project_root)
        redis_client = create_redis_client_sync(cfg)

        if not redis_client:
            print("FATAL ERROR: Failed to create Redis client. Check your config and Redis server.", file=sys.stderr)
            sys.exit(1)

        print("Clearing Redis cache (db)...")
        redis_client.flushdb()
        print("SUCCESS: Redis cache has been cleared.")

    except FileNotFoundError:
        print("FATAL ERROR: config.json not found in the project root.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"FATAL ERROR: An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()