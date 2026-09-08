import asyncio
import pandas as pd
from datetime import datetime, timezone

from core.config_manager import ConfigManager
from core.backtest.optimize import run_full_optimization
from core.logger import get_logger
from pathlib import Path

async def main():
    cfg = ConfigManager(Path(__file__).parent)
    logger = get_logger(cfg, "test")
    # This combination failed in the log: GBP_USD bb_squeeze
    # Let's force a run_full_optimization with a very short train and test period
    # and maybe it will produce an error. We want to see the error!
    
    # We will patch log_error to print the traceback to stdout
    import core.logger as cl
    original_log_error = cl.log_error
    async def patched_log_error(logger, context, error, **kwargs):
        import traceback
        print(f"PATCHED LOG ERROR CAUGHT EXCEPTION in {context}: {error}")
        traceback.print_exc()
        await original_log_error(logger, context, error, **kwargs)
    
    cl.log_error = patched_log_error
    
    print("Running optimization with small data...")
    cfg.config.setdefault("tasks", {}).setdefault("optimizer", {})["n_trials"] = 2
    
    await run_full_optimization(
        pair="GBP_USD",
        strategy_name="bb_squeeze",
        start_date=None,
        end_date=None,
        output_suffix="_test_crash",
        walk_number=1,
        train_start="2026-06-01T00:00:00Z",
        train_end="2026-06-15T00:00:00Z",
        test_start="2026-06-15T00:00:00Z",
        test_end="2026-06-16T00:00:00Z", # only 1 day, likely 0 trades!
        wfa_mode=True
    )
    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
