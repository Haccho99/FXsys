import asyncio
from pathlib import Path
from core.config_manager import ConfigManager
import core.backtest.backtester as bt
from core.logger import get_logger

async def main():
    cfg = ConfigManager(Path(__file__).parent)
    logger = get_logger(cfg, "test_fast")
    
    # Patch the correct reference in backtester module
    original_log_error = bt.log_error
    async def patched_log_error(logger, context, error, **kwargs):
        import traceback
        print(f"PATCHED LOG ERROR CAUGHT EXCEPTION in {context}: {error}")
        traceback.print_exc()
        await original_log_error(logger, context, error, **kwargs)
    bt.log_error = patched_log_error
    
    # Run a backtest directly with arbitrary params that produce very few trades
    print("Running backtest directly...")
    
    # Load some params
    params = {'bb_period': 24, 'bb_std': 1.871020938847804, 'bb_squeeze_pctl': 0.7764614039620837, 'rsi_long_th': 65, 'rsi_short_th': 46, 'min_sl_pips': 10.33816637907288}
    
    await bt.run_backtest(
        pair="GBP_USD",
        strategy_name="bb_squeeze",
        params=params,
        output_log_filename="test_crash.parquet",
        start_date_str="2026-06-15T00:00:00Z",
        end_date_str="2026-06-17T00:00:00Z",
        walk_number=1,
        train_start="2026-06-01T00:00:00Z",
        train_end="2026-06-15T00:00:00Z",
        test_start="2026-06-15T00:00:00Z",
        test_end="2026-06-17T00:00:00Z"
    )
    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
