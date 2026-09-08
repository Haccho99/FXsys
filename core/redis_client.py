"""
core/redis_client.py (v2.5 - Added Sync Client Support)
"""
import redis  # 同期ライブラリをインポート
import redis.asyncio as aioredis
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.config_manager import ConfigManager
    import structlog

class _AwaitableRedis:
    """非同期Redisクライアントのラッパー。直接利用（.get()等）と await の両方に対応"""
    def __init__(self, client: aioredis.Redis):
        self._client = client
    def __getattr__(self, name):
        return getattr(self._client, name)
    def __await__(self):
        async def _ret():
            return self._client
        return _ret().__await__()
    def __bool__(self):
        return self._client is not None

def create_redis_client(cfg: Optional["ConfigManager"] = None, logger: Optional["structlog.stdlib.BoundLogger"] = None):
    """
    非同期Redisクライアント作成関数（同期/非同期呼び出し両対応）
    """
    if cfg is None:
        from core import cfg as default_cfg
        cfg = default_cfg
    if logger is None:
        from .logger import get_logger
        logger = get_logger(cfg, "redis_client")
    try:
        redis_url = cfg.get_sync("system.redis_url")
        if not redis_url:
            logger.error("Redis URL not found in config.")
            return None
        # 💡 IPv6とIPv4のすれ違いを防ぐため、localhostを明示的にIPv4(127.0.0.1)へ強制変換
        redis_url = redis_url.replace("localhost", "127.0.0.1")
        redis_client = aioredis.from_url(redis_url, decode_responses=True)
        logger.info(f"Successfully configured Redis client (async).")
        return _AwaitableRedis(redis_client)
    except Exception as e:
        logger.error(f"Could not configure Redis client (async): {e}")
        return None

def create_redis_client_sync(cfg: "ConfigManager") -> Optional[redis.Redis]:
    """
    【新規追加】同期のRedisクライアントを生成・提供します。
    主に、単純な同期ユーティリティスクリプトからの利用を想定しています。
    """
    try:
        redis_url = cfg.get_sync("system.redis_url")
        if not redis_url:
            # この関数はロガーを持たないスクリプトから呼ばれるため、printでエラー出力します。
            print("Error: Redis URL not found in config.")
            return None
        
        # 💡 同期版も同様にIPv4(127.0.0.1)へ強制変換
        redis_url = redis_url.replace("localhost", "127.0.0.1")
        # 同期クライアントを生成
        client = redis.from_url(redis_url, decode_responses=True)
        # 接続をテスト
        client.ping()
        return client
    except Exception as e:
        print(f"Error: Could not configure Redis client (sync): {e}")
        return None