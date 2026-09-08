"""
monitor.py (v16 - Final Core Architecture Alignment)
"""
import pandas as pd
import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import httpx
import google.generativeai as genai
from typing import Tuple, Optional, TYPE_CHECKING
import sys

# ▼▼▼ 修正: 全てのインポートと設定をcoreパッケージの作法に統一 ▼▼▼
from core import cfg
from core.data import fetch_candles, get_spread, fetch_econ_calendar
from core.indicators import calculate_atr
from core.logger import get_logger, append_csv, log_error
from core.oanda_api import get_active_account_id, get_active_token, build_stream_url

logger = get_logger(cfg, "monitor")

if TYPE_CHECKING:
    from core.trade import TradeExecutor
# ▲▲▲ 修正ここまで ▲▲▲

class Rolling:
    def __init__(self, size: int):
        self.size, self.data = size, []
    def push(self, value: float):
        if value is not None and np.isfinite(value):
            self.data.append(value)
            if len(self.data) > self.size: self.data.pop(0)
    def mean(self) -> float:
        return np.mean(self.data) if self.data else 0.0
    def std(self) -> float:
        return np.std(self.data) if len(self.data) > 1 else 0.0

class GuardCenter:
    def __init__(self):
        self.symbols = cfg.get_sync("trading.symbols", ["USD_JPY"])
        self._atr_m5_hist = {pair: Rolling(size=720) for pair in self.symbols}
        self._spread_z_hist = {pair: Rolling(size=720) for pair in self.symbols}
        self._executor: Optional[TradeExecutor] = None
        self._econ_events_cache, self._last_econ_fetch = [], None
        logger.info("GuardCenter initialized.")

    # GuardCenterクラス内の _atr_m5_block メソッドを上書き
    async def _atr_m5_block(self, pair: str) -> bool:
        try:
            df = await fetch_candles(pair, gran="M5") 
            if df.is_empty(): return False
            atr_series = await calculate_atr(df["high"], df["low"], df["close"], period=14)
            if atr_series is None or len(atr_series) == 0: return False
            atr_now = float(atr_series[-1])
            self._atr_m5_hist[pair].push(atr_now)
            
            # ▼ 新規追加: M5 ATRをRedisへプッシュ (ダッシュボード用) ▼
            from core.redis_client import create_redis_client
            redis_client = await create_redis_client(cfg, logger)
            if redis_client:
                key = f"atr_m5_hist:{pair}"
                await redis_client.rpush(key, atr_now)
                # 過去720件（約60時間分）を維持
                await redis_client.ltrim(key, -720, -1)
                await redis_client.close()
            # ▲ 新規追加 ここまで ▲

            atr_mean = self._atr_m5_hist[pair].mean()
            if atr_mean == 0: return False
            atr_threshold_mult = cfg.get_sync("risk_management.atr_block_threshold", 2.0)
            atr_threshold = atr_mean * atr_threshold_mult
            if atr_now >= atr_threshold:
                logger.warning("High M5 ATR detected, trade blocked.", pair=pair, atr=f"{atr_now:.4f}", threshold=f"{atr_threshold:.4f}")
                return True
            return False
        except Exception as e:
            await log_error(logger, "_atr_m5_block", error=e, pair=pair)
            return False

    async def _atr_force_close(self, pair: str) -> bool:
        from core.trade import get_executor # 循環インポートを避ける
        try:
            df = await fetch_candles(pair, gran="M5") # 修正: 正しい関数名を使用
            if df.is_empty(): return False
            atr_series = await calculate_atr(df["high"], df["low"], df["close"], period=14)
            if atr_series is None or len(atr_series) == 0: return False
            atr_now = float(atr_series[-1])
            atr_mean = self._atr_m5_hist[pair].mean()
            if atr_mean == 0: return False
            atr_threshold_mult = cfg.get_sync("risk_management.atr_force_close_threshold", 3.0)
            atr_threshold = atr_mean * atr_threshold_mult
            if atr_now >= atr_threshold:
                logger.critical("ATR force close triggered.", pair=pair, atr=f"{atr_now:.4f}", threshold=f"{atr_threshold:.4f}")
                if self._executor is None: self._executor = await get_executor()
                # (強制決済の実行ロジックはtrade.pyに依存)
                pass
                return True
            return False
        except Exception as e:
            await log_error(logger, "_atr_force_close", error=e, pair=pair)
            return False

    async def _spread_z_block(self, pair: str) -> bool:
        try:
            spread = await get_spread(pair)
            if spread is None: return False
            self._spread_z_hist[pair].push(spread)
            spread_mean, spread_std = self._spread_z_hist[pair].mean(), self._spread_z_hist[pair].std()
            if spread_std == 0: return False
            z_score = (spread - spread_mean) / spread_std
            z_threshold = cfg.get_sync("risk_management.spread_zscore_threshold", 3.0)
            if z_score > z_threshold:
                logger.warning("High spread Z-score detected.", pair=pair, spread=f"{spread:.5f}", z_score=f"{z_score:.2f}")
                return True
            return False
        except Exception as e:
            await log_error(logger, "_spread_z_block", error=e, pair=pair)
            return False
            
    async def _econ_event_block(self, pair: str) -> bool:
        now = datetime.now(timezone.utc)
        if self._last_econ_fetch is None or (now - self._last_econ_fetch) > timedelta(hours=6):
            self._econ_events_cache = await fetch_econ_calendar(now.strftime("%Y-%m-%d"), (now + timedelta(days=7)).strftime("%Y-%m-%d"))
            self._last_econ_fetch = now
        
        if not self._econ_events_cache: return False
        
        before_mins = cfg.get_sync("economic_events.exclude_before_min", 30)
        after_mins = cfg.get_sync("economic_events.exclude_after_min", 60)

        for event in self._econ_events_cache:
            if event.get('impact') == 'high' and event.get('currency') in pair:
                try:
                    event_time = datetime.fromisoformat(event.get('time'))
                    if (event_time - timedelta(minutes=before_mins)) <= now <= (event_time + timedelta(minutes=after_mins)):
                        logger.warning("Trade blocked by HIGH impact event.", event=event.get('event'))
                        return True
                except (TypeError, ValueError): continue
        return False

    async def is_blocked(self, pair: str) -> Tuple[bool, Optional[str]]:
        if cfg.get_sync("features.news_filter.enabled", True) and await self._econ_event_block(pair):
            return True, "Upcoming economic event"
        if cfg.get_sync("features.volatility_filter.enabled", True) and await self._atr_m5_block(pair):
            return True, "High M5 ATR volatility"
        if cfg.get_sync("features.spread_filter.enabled", True) and await self._spread_z_block(pair):
            return True, "High spread Z-score"
        return False, None

    async def get_latest_sentiment(self, pair: str) -> float:
        """最新のニュースからセンチメントスコアを算出する。"""
        # 単純化のため、最新の経済指標スケジュールから重要度の高いニュースを1つ選んで解析
        now = datetime.now(timezone.utc)
        if not self._econ_events_cache:
            self._econ_events_cache = await fetch_econ_calendar(now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d"))
        
        relevant_news = [e for e in self._econ_events_cache if e.get('currency') in pair and e.get('impact') in ['high', 'medium']]
        if not relevant_news:
            return 0.0
            
        # 最新のニューステキストを解析（キャッシュ等の実装は将来の課題）
        latest_event = relevant_news[0].get('event', '')
        return await analyze_sentiment_llm(latest_event)

class TransactionMonitor:
    def __init__(self):
        self.account_id = get_active_account_id()
        self.token = get_active_token()
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.stream_url = build_stream_url(f"/accounts/{self.account_id}/transactions/stream")
        logger.info("TransactionMonitor initialized.", stream_url=self.stream_url)
    
    async def _log_transaction(self, event: dict):
        log_file = cfg.project_root / cfg.get_sync("system.log_dir", "logs") / f"transaction_log_{datetime.now(timezone.utc):%Y%m%d}.csv"
        line_parts = [event.get(k, "") for k in ["time", "type", "id", "reason", "instrument", "units", "price"]]
        await append_csv(cfg, str(log_file), pd.DataFrame([line_parts]))
        logger.info(f"Transaction logged: {event.get('type')}, ID: {event.get('id')}")

    async def run_monitoring(self):
        if not self.stream_url or not self.token:
            logger.error("Stream URL or Token is not configured. Transaction monitor cannot start.")
            return
            
        while True:
            try:
                async with httpx.AsyncClient(headers=self.headers, timeout=None) as client:
                    async with client.stream("GET", self.stream_url) as response:
                        response.raise_for_status()
                        logger.info("Successfully connected to transaction stream.")
                        async for line in response.aiter_lines():
                            if line.startswith('{"type":"PRICE"'):
                                try:
                                    transaction = json.loads(line)
                                    if transaction.get("type") in ["ORDER_FILL", "ORDER_CANCEL", "STOP_LOSS_ORDER", "TAKE_PROFIT_ORDER"]:
                                        await self._log_transaction(transaction)
                                except Exception as e:
                                    await log_error(logger, "transaction_processing", error=e)
            except Exception as e:
                await log_error(logger, "run_monitoring", error=e)
                await asyncio.sleep(15)

async def analyze_sentiment_llm(text: str) -> float:
    """
    LLMを使用してニュースのセンチメントを分析し、-1.0(Dovish)から+1.0(Hawkish)のスコアを返す。
    """
    api_key = cfg.get_sync("api_keys.gemini.api_key")
    if not api_key:
        logger.warning("Gemini API key not found. Skipping sentiment analysis.")
        return 0.0

    try:
        genai.configure(api_key=api_key)
        # 修正: google-generativeaiの非同期対応を確認しつつ、スレッドで実行
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        Analyze the following economic news for its impact on the USD/JPY currency pair.
        Classify the sentiment on a scale from -1.0 (extremely Dovish / JPY strength) to +1.0 (extremely Hawkish / USD strength).
        Return ONLY a single float number between -1.0 and 1.0.
        
        News text: "{text}"
        """
        
        response = await asyncio.to_thread(lambda: model.generate_content(prompt))
        
        score_str = response.text.strip()
        try:
            # 正規表現などで数字だけ抽出する方が安全だが、まずはシンプルに
            import re
            match = re.search(r"[-+]?\d*\.\d+|\d+", score_str)
            if match:
                score = float(match.group())
                return max(-1.0, min(1.0, score))
            else:
                logger.error(f"LLM did not return a valid score: {score_str}")
                return 0.0
        except ValueError:
            logger.error(f"LLM returned non-float response: {score_str}")
            return 0.0

    except Exception as e:
        await log_error(logger, "analyze_sentiment_llm", error=e)
        return 0.0

async def update_news_indicators_cache():
    """ニュースと指標を取得し、Redis(news_indicators)へキャッシュする"""
    try:
        from core.redis_client import create_redis_client
        import json
        
        now = datetime.now(timezone.utc)
        start_date = now.strftime("%Y-%m-%d")
        end_date = (now + timedelta(days=2)).strftime("%Y-%m-%d")
        
        events = await fetch_econ_calendar(start_date, end_date)
        if events:
            redis_client = await create_redis_client(cfg, logger)
            if redis_client:
                await redis_client.setex("news_indicators", 3600, json.dumps(events))
                await redis_client.close()
                logger.info(f"Updated news_indicators cache with {len(events)} events.")
    except Exception as e:
        await log_error(logger, "update_news_indicators_cache", error=e)

async def news_update_loop():
    """5分間隔でニュースキャッシュを更新するバックグラウンドタスク"""
    logger.info("Starting background news update loop...")
    while True:
        await update_news_indicators_cache()
        await asyncio.sleep(300)