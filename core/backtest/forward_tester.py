"""backtest/forward_tester.py (v4 - Comprehensive Diagnostic Tool)

設定された全通貨ペア・全戦略のバックテストとフォワードテストを
一括実行し、結果を集約して出力する総合診断ツール。
"""
import argparse
import os
import sys
from datetime import datetime

# Ensure the project root is in the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)



from core.logger import get_logger
import asyncio
import polars as pl
import joblib
import pandas as pd
import vectorbt as vbt
from pathlib import Path
import json
import numpy as np
import redis.asyncio as aioredis
from typing import Dict, Any, Optional

from core.data import fetch_candles
from core.backtest.backtester import _load_strategy_module


from core.config_manager import ConfigManager
from core.logger import get_logger
from core.ai_config import SELECTED_FEATURES

# Instantiate ConfigManager first
cfg = ConfigManager(Path(project_root))

# get_logger also handles setup
logger = get_logger(cfg, __name__)


# --- 検証設定 ---
AI_CONFIDENCE_THRESHOLD = 0.55

# --- 期間設定 ---
BACKTEST_START_DATE = "2023-01-01T00:00:00"
BACKTEST_END_DATE = "2025-06-30T23:59:59"
FORWARD_START_DATE = "2025-07-01T00:00:00"
FORWARD_END_DATE = "2025-07-31T23:59:59"
# ----------------

def translate_and_format_stats(stats: pd.Series) -> Dict[str, Any]:
    """vectorbtの統計結果を日本語の辞書に変換し、ダッシュボードで使いやすい形式にする"""
    # (この関数は変更ありません)
    translation_map = {
        "Start": "テスト期間開始日", "End": "テスト期間終了日",
        "Period": "テスト期間", "Start Value": "初期資産", "End Value": "最終資産",
        "Total Return [%]": "総リターン (%)", "Benchmark Return [%]": "ベンチマークリターン (%)",
        "Max Drawdown [%]": "最大ドローダウン (%)", "Max Drawdown Duration": "最大ドローダウン期間",
        "Total Trades": "総トレード数", "Win Rate [%]": "勝率 (%)",
        "Best Trade [%]": "ベストトレード (%)", "Worst Trade [%]": "ワーストトレード (%)",
        "Avg Winning Trade [%]": "平均勝ちトレード (%)", "Avg Losing Trade [%]": "平均負けトレード (%)",
        "Profit Factor": "プロフィットファクター", "Expectancy": "期待値",
        "Sharpe Ratio": "シャープレシオ", "Sortino Ratio": "ソルティノレシオ",
    }
    
    formatted = {}
    for key, value in stats.items():
        jp_key = translation_map.get(key)
        if jp_key:
            if isinstance(value, np.integer): value = int(value)
            elif isinstance(value, np.floating): value = float(value)

            if isinstance(value, (pd.Timestamp, pd.Timedelta)):
                formatted[jp_key] = str(value)
            elif isinstance(value, (int, float)):
                formatted[jp_key] = round(value, 4)
            else:
                formatted[jp_key] = value
                
    return formatted

async def run_ai_filtered_test(
    model: Any, 
    pair: str, 
    strategy_name: str, 
    start_date: str, 
    end_date: str,
    params: dict,
    redis: Optional[aioredis.Redis] = None
) -> pd.Series | None:
    """指定された期間でAIフィルターを適用したバックテストを実行する"""
    # (この関数は変更ありません)
    price_data_pl = await fetch_candles(
        pair, start=start_date, end=end_date
    )
    if price_data_pl is None or price_data_pl.is_empty():
        logger.error(f"Failed to load candle data for period: {start_date} to {end_date}")
        return None
    price_data_pd = price_data_pl.to_pandas().set_index("time")
    
    strategy_module = await _load_strategy_module(strategy_name)
    strategy_class_name = "Backtest" + "".join(word.capitalize() for word in strategy_name.split('_')) + "Strategy"

    if redis is None:
        from core.redis_client import create_redis_client
        redis = create_redis_client(cfg)
        if redis is None:
            logger.error("Failed to create redis client.")
            return None

    strategy_instance = getattr(strategy_module, strategy_class_name)(params, redis, cfg)
    
    price_data_pl = pl.from_pandas(price_data_pd.reset_index())
    df_with_features = await strategy_instance.generate_signal(price_data_pl)
    
    features_for_prediction = model.feature_name_
    missing_features = set(features_for_prediction) - set(df_with_features.columns)
    if missing_features:
        logger.error(f"Features mismatch! Missing in prediction data: {missing_features}")
        return None
        
    X_forward = df_with_features[features_for_prediction]
    probabilities = model.predict_proba(X_forward)[:, 1]
    
    df_with_features["ai_prob"] = probabilities
    
    ai_long_cond_pd = df_with_features["entries"] & (df_with_features["ai_prob"] > AI_CONFIDENCE_THRESHOLD)
    ai_short_cond_pd = df_with_features["short_entries"]

    conditions = [ai_long_cond_pd, ai_short_cond_pd]
    choices = ["long", "short"]
    default_value = None

    df_with_features["ai_signal"] = np.select(conditions, choices, default=default_value)

    ai_filtered_signals = df_with_features["ai_signal"]
    entries = ai_filtered_signals == "long"
    short_entries = ai_filtered_signals == "short"
    
    exits = short_entries
    short_exits = entries
    
    entries.index = price_data_pd.index
    exits.index = price_data_pd.index
    short_entries.index = price_data_pd.index
    short_exits.index = price_data_pd.index

    if not entries.any() and not short_entries.any():
        logger.warning(f"No entry signals remained after AI filtering for period: {start_date} to {end_date}")
        return None

    portfolio = vbt.Portfolio.from_signals(
        price_data_pd.close, entries=entries, exits=exits,
        short_entries=short_entries, short_exits=short_exits,
        freq="15T", init_cash=1_000_000
    )
    return portfolio.stats()

async def main():
    logger.info("--- Starting Comprehensive System Diagnostics ---")
    
    # config.jsonからテスト対象を読み込む
    pairs = cfg.get_sync("system.pairs_for_trading", ["USD_JPY"])
    enabled_strategies = cfg.get_sync("trading.enabled_strategies", {"trend": True})
    strategies = [k for k, v in enabled_strategies.items() if v]
    
    all_results = []

    for strategy in strategies:
        model_path = Path(f"model_{strategy}.joblib")
        if not model_path.exists():
            logger.warning(f"Model for {strategy} not found at {model_path}. Skipping.")
            continue
        model = joblib.load(model_path)
        logger.info(f"Loaded model for {strategy} from {model_path}")

        for pair in pairs:
            logger.info("\n" + "="*50)
            logger.info(f"  TESTING: {pair} / {strategy}")
            logger.info("="*50)

            # --- 1. バックテスト（学習期間の成績）を実行 ---
            logger.info(f"Running Backtest (Period: {BACKTEST_START_DATE} to {BACKTEST_END_DATE})")
            backtest_stats = await run_ai_filtered_test(
                model, pair, strategy, BACKTEST_START_DATE, BACKTEST_END_DATE,
                {},
                None
            )
            if backtest_stats is not None:
                jp_stats = translate_and_format_stats(backtest_stats)
                all_results.append({
                    "test_type": "backtest", "pair": pair, "strategy": strategy, "results": jp_stats
                })
            
            # --- 2. フォワードテスト（未知の期間の成績）を実行 ---
            logger.info(f"Running Forward Test (Period: {FORWARD_START_DATE} to {FORWARD_END_DATE})")
            forward_test_stats = await run_ai_filtered_test(
                model, pair, strategy, FORWARD_START_DATE, FORWARD_END_DATE,
                {},
                None
            )
            if forward_test_stats is not None:
                jp_stats = translate_and_format_stats(forward_test_stats)
                all_results.append({
                    "test_type": "forwardtest", "pair": pair, "strategy": strategy, "results": jp_stats
                })

    logger.info("\n" + "#"*60)
    logger.info("  COMPREHENSIVE DIAGNOSTICS COMPLETED")
    logger.info("#"*60)
    
    # 最終的な結果をJSON形式でまとめて出力（ダッシュボード連携用）
    print(json.dumps(all_results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())