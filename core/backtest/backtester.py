# core/backtest/backtester.py (vFinal - The Real Final Fix)
import sys
from pathlib import Path
import asyncio
import polars as pl
import pandas as pd
import vectorbt as vbt
from typing import Dict, Any, Optional
import importlib
import redis.asyncio as aioredis
import traceback
import numpy as np

from core import cfg
from core.logger import get_logger, log_error
from core.data import fetch_candles
from core.indicators import add_all_indicators

logger = get_logger(cfg, "backtester")

from numba import njit

@njit
def compute_stepped_tsl_exits(close_arr, high_arr, low_arr, entries_arr, short_entries_arr, sl_price_arr, tp_price_arr, act_pct_arr, trail_pct_arr):
    n = len(close_arr)
    exits = np.zeros(n, dtype=np.bool_)
    short_exits = np.zeros(n, dtype=np.bool_)
    
    in_long = False
    in_short = False
    entry_p = 0.0
    current_sl = 0.0
    tp_p = 0.0
    act_pct = 0.0
    trl_pct = 0.0
    
    for i in range(n):
        if in_long:
            if low_arr[i] <= current_sl or high_arr[i] >= tp_p:
                exits[i] = True
                in_long = False
            else:
                profit_dist = close_arr[i] - entry_p
                tp_dist = tp_p - entry_p
                if tp_dist > 0 and profit_dist >= tp_dist * act_pct:
                    min_allowed_sl = entry_p + (tp_dist * 0.10) # 10%建値バリア
                    profit_pct = profit_dist / tp_dist
                    if profit_pct >= 0.80: trail_dist = tp_dist * 0.10
                    elif profit_pct >= 0.60: trail_dist = tp_dist * 0.15
                    else: trail_dist = tp_dist * trl_pct
                    
                    new_sl = close_arr[i] - trail_dist
                    if new_sl > current_sl and new_sl >= min_allowed_sl:
                        current_sl = new_sl

        elif in_short:
            if high_arr[i] >= current_sl or low_arr[i] <= tp_p:
                short_exits[i] = True
                in_short = False
            else:
                profit_dist = entry_p - close_arr[i]
                tp_dist = entry_p - tp_p
                if tp_dist > 0 and profit_dist >= tp_dist * act_pct:
                    min_allowed_sl = entry_p - (tp_dist * 0.10)
                    profit_pct = profit_dist / tp_dist
                    if profit_pct >= 0.80: trail_dist = tp_dist * 0.10
                    elif profit_pct >= 0.60: trail_dist = tp_dist * 0.15
                    else: trail_dist = tp_dist * trl_pct
                    
                    new_sl = close_arr[i] + trail_dist
                    if new_sl < current_sl and new_sl <= min_allowed_sl:
                        current_sl = new_sl

        if not in_long and not in_short:
            if entries_arr[i]:
                in_long, entry_p, current_sl, tp_p, act_pct, trl_pct = True, close_arr[i], sl_price_arr[i], tp_price_arr[i], act_pct_arr[i], trail_pct_arr[i]
            elif short_entries_arr[i]:
                in_short, entry_p, current_sl, tp_p, act_pct, trl_pct = True, close_arr[i], sl_price_arr[i], tp_price_arr[i], act_pct_arr[i], trail_pct_arr[i]
                
    return exits, short_exits

async def _load_strategy_module(strategy_name: str):
    path = Path(f"core/backtest/strategies/bt_{strategy_name}_strategy.py")
    if not path.exists(): raise ImportError(f"Strategy file not found: {path}")
    name = f"core.backtest.strategies.bt_{strategy_name}_strategy"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None: raise ImportError(f"Could not create module spec for {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

async def run_backtest(
    pair: str, strategy_name: str, params: dict,
    output_log_filename: str,
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None,
    candle_count: Optional[int] = None,
    redis: Optional[aioredis.Redis] = None,
    walk_number: Optional[int] = None,
    train_start: Optional[str] = None,
    train_end: Optional[str] = None,
    test_start: Optional[str] = None,
    test_end: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    try:
        if redis is None:
            from core.redis_client import create_redis_client
            redis = await create_redis_client(cfg)
            if redis is None: return None

        price_data_pl = await fetch_candles(pair=pair, start=start_date_str, end=end_date_str) if start_date_str and end_date_str else await fetch_candles(pair=pair, count=candle_count)
        if price_data_pl is None or price_data_pl.is_empty():
            logger.warning(f"No data for {pair}, skipping backtest.")
            return None
        
        price_data_pl_with_indicators = await add_all_indicators(price_data_pl, params)
        
        strategy_module = await _load_strategy_module(strategy_name)
        StrategyClass = getattr(strategy_module, f"Backtest{''.join([part.capitalize() for part in strategy_name.split('_')])}Strategy")
        strategy = StrategyClass(params, redis, cfg)
        
        signals_and_features_df = await strategy.generate_signal(price_data_pl_with_indicators)

        # ▼▼▼ TSLのバックテスト反映用ロジック追加 ▼▼▼
        vbt_input_cols = ["time", "open", "high", "low", "close", "entries", "short_entries", "sl_price", "tp_price", "signal_exits", "short_signal_exits", "lot_ratio", "tsl_activation_pct", "tsl_trail_pct"]
        vbt_input_df = signals_and_features_df.select(
            [col for col in vbt_input_cols if col in signals_and_features_df.columns]
        ).to_pandas().set_index("time")
        
        if 'tsl_activation_pct' in vbt_input_df.columns and 'tsl_trail_pct' in vbt_input_df.columns:
            tsl_exits, tsl_short_exits = compute_stepped_tsl_exits(
                vbt_input_df['close'].to_numpy(), vbt_input_df['high'].to_numpy(), vbt_input_df['low'].to_numpy(),
                vbt_input_df.get('entries', pd.Series(False, index=vbt_input_df.index)).to_numpy(dtype=bool),
                vbt_input_df.get('short_entries', pd.Series(False, index=vbt_input_df.index)).to_numpy(dtype=bool),
                vbt_input_df.get('sl_price', pd.Series(0.0, index=vbt_input_df.index)).to_numpy(),
                vbt_input_df.get('tp_price', pd.Series(0.0, index=vbt_input_df.index)).to_numpy(),
                vbt_input_df['tsl_activation_pct'].to_numpy(), vbt_input_df['tsl_trail_pct'].to_numpy()
            )
            vbt_input_df['signal_exits'] = vbt_input_df.get('signal_exits', False) | tsl_exits
            vbt_input_df['short_signal_exits'] = vbt_input_df.get('short_signal_exits', False) | tsl_short_exits

        portfolio = vbt.Portfolio.from_signals(
            close=vbt_input_df['close'],
            entries=vbt_input_df.get('entries'),
            exits=vbt_input_df.get('signal_exits'),
            sl_stop=vbt_input_df.get('sl_price'),
            tp_stop=vbt_input_df.get('tp_price'),
            short_entries=vbt_input_df.get('short_entries'),
            short_exits=vbt_input_df.get('short_signal_exits'),
            size=vbt_input_df.get('lot_ratio', 1.0),
            freq="15min", 
            init_cash=1_000_000
        )
        
        if portfolio.trades.records.empty:
            logger.warning(f"Backtest for {pair}/{strategy_name} produced ZERO TRADES.")
            return None

        records_readable = portfolio.trades.records_readable.copy()
        exit_reason_map = {0: 'End of Backtest', 1: 'Stop Loss', 2: 'Take Profit', 3: 'Signal Exit'}
        raw_exit_reasons = portfolio.trades.records['status']
        records_readable['Exit Reason Detail'] = [exit_reason_map.get(reason, 'Unknown') for reason in raw_exit_reasons]
        records_readable['Pair'] = pair
        entry_timestamps = pd.to_datetime(records_readable['Entry Timestamp'], utc=True)
        
        features_df_pd = signals_and_features_df.to_pandas().set_index('time')
        features_df_pd.index = pd.to_datetime(features_df_pd.index, utc=True)
        all_feature_columns = [col for col in features_df_pd.columns if col not in ['open', 'high', 'low', 'close', 'entries', 'short_entries', 'signal_exits', 'short_signal_exits', 'sl_price', 'tp_price']]
        for feature in all_feature_columns:
            # Reindex the feature series with the entry timestamps to get the value at the time of each trade.
            # This is more robust than .map() and avoids the PanicException with empty series.
            feature_values_at_entry = features_df_pd[feature].reindex(entry_timestamps)
            records_readable[feature] = feature_values_at_entry.values
        from core.logger import save_or_append_parquet
        records_pl = pl.from_pandas(records_readable)
        if walk_number is not None:
            ts_start = pd.to_datetime(train_start, utc=True).to_pydatetime() if train_start else None
            ts_end = pd.to_datetime(train_end, utc=True).to_pydatetime() if train_end else None
            te_start = pd.to_datetime(test_start, utc=True).to_pydatetime() if test_start else None
            te_end = pd.to_datetime(test_end, utc=True).to_pydatetime() if test_end else None
            
            records_pl = records_pl.with_columns(
                pl.lit(walk_number).alias("Walk Number"),
                pl.lit(ts_start).alias("Train Start"),
                pl.lit(ts_end).alias("Train End"),
                pl.lit(te_start).alias("Test Start"),
                pl.lit(te_end).alias("Test End")
            )

        
        if records_pl.is_empty():
            logger.warning(f"Backtest for {pair}/{strategy_name} produced records, but the resulting DataFrame is empty. Skipping save.")
        else:
            await save_or_append_parquet(cfg, output_log_filename, records_pl) 
        
        stats = portfolio.stats()
        if isinstance(stats, pd.Series):
            stats_dict = stats.to_dict()
            total_trades = len(portfolio.trades.records)
            stats_dict['Total Trades'] = total_trades
            if total_trades > 0 and 'Exit Reason Detail' in records_readable.columns:
                exit_reasons = records_readable['Exit Reason Detail'].value_counts()
                stats_dict['SL Hit Count'] = exit_reasons.get('Stop Loss', 0)
                stats_dict['TP Hit Count'] = exit_reasons.get('Take Profit', 0)
                stats_dict['Signal Exit Count'] = exit_reasons.get('Signal Exit', 0)
                stats_dict['SL Hit Rate'] = stats_dict['SL Hit Count'] / total_trades
                stats_dict['TP Hit Rate'] = stats_dict['TP Hit Count'] / total_trades
            else:
                stats_dict['SL Hit Count'] = 0; stats_dict['TP Hit Count'] = 0; stats_dict['Signal Exit Count'] = 0;
                stats_dict['SL Hit Rate'] = 0.0; stats_dict['TP Hit Rate'] = 0.0
            return stats_dict
        return stats
    except Exception as e:
        await log_error(logger, "run_backtest", error=e, pair=pair, strategy=strategy_name)
        return None