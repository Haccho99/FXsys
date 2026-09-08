import asyncio
from pathlib import Path

from .backtester import run_backtest
from ..config_manager import ConfigManager
from ..logger import get_logger

# Instantiate ConfigManager first
project_root = Path(__file__).resolve().parent.parent.parent
cfg = ConfigManager(project_root)

# get_logger also handles setup
logger = get_logger(cfg, "ai_data_generator")

async def main():
    logger.info("--- Starting AI Training Data Generation ---")

    log_dir = Path(cfg.get_sync("system.log_dir", "logs"))
    pairs = cfg.get_sync("system.pairs_for_trading", ["USD_JPY", "EUR_JPY"])
    enabled_strategies = cfg.get_sync("trading.enabled_strategies", {"trend": True})
    strategies = [k for k, v in enabled_strategies.items() if v]

    for strategy in strategies:
        output_filename = f"training_data_{strategy}.csv"
        output_file_path = log_dir / output_filename
        
        try:
            if output_file_path.exists():
                output_file_path.unlink()
                logger.info(f"Removed old training data file: {output_file_path}")
        except OSError as e:
            logger.error(f"Error removing old training data file: {e}")
            continue

        logger.info(f"--- Generating data for {strategy} strategy ---")
        for pair in pairs:
            logger.info(f"Running backtest for {pair} with {strategy} strategy...")
            
            params = cfg.get_sync(f"best_params.{pair}.{strategy}")
            if not params:
                logger.warning(f"No best_params found for {pair} with {strategy}. Skipping.")
                continue

            await run_backtest(
                pair=pair, 
                strategy_name=strategy,
                params=params,
                output_log_filename=output_filename,
                start_date_str="2022-01-01T00:00:00Z",
                end_date_str="2025-06-30T23:59:59Z"
            )

    logger.info("--- AI Training Data Generation Completed. ---")

if __name__ == "__main__":
    asyncio.run(main())