"""train_ai_model.py (v5 - Direct Elite Training)"""
from __future__ import annotations
import asyncio
from pathlib import Path
import polars as pl
from datetime import datetime, timezone
import sys
import traceback

project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from core.ai_interface import optimize_and_train_model
from core.config_manager import ConfigManager
from core.logger import get_logger, append_csv, log_error
from core.ai_config import SELECTED_FEATURES

cfg = ConfigManager(project_root)
logger = get_logger(cfg, "model_trainer")

async def train_strategy_model(strategy_name: str, elite_training_data_path: Path):
    """
    指定された戦略のAIモデルを、指定された「英才教育用」データセットを使って学習させる。
    """
    logger.info(f"--- Starting AI model training for {strategy_name} strategy ---")
    logger.info(f"Using elite training data: {elite_training_data_path.name}")
    
    model_save_path = cfg.project_root / f"model_{strategy_name}.joblib"
    
    try:
        df_elite_full = pl.read_parquet(elite_training_data_path)

        # 特定の戦略のデータのみをフィルタリング
        # 'strategy'列がない可能性があるため、存在チェックを追加
        if "strategy" in df_elite_full.columns:
            df_elite = df_elite_full.filter(pl.col("strategy") == strategy_name)
        else:
            logger.warning("'strategy' column not found in elite data, using all data for training.")
            df_elite = df_elite_full

        if df_elite.is_empty():
            logger.warning(f"No trade data found for strategy '{strategy_name}' in the elite dataset. Skipping training.")
            return

        logger.info(f"Elite data for '{strategy_name}': {len(df_elite)} trades.")
        df_elite = df_elite.with_columns(label=(pl.col("PnL") > 0).cast(pl.Int8))
        
        # 使用する特徴量がデータ内に存在するか確認
        features_in_data = [f for f in SELECTED_FEATURES if f in df_elite.columns]
        missing_features = set(SELECTED_FEATURES) - set(features_in_data)
        if missing_features:
            logger.warning(f"Following features from ai_config are missing in the data and will be ignored: {missing_features}")
        
        if not features_in_data:
            logger.error("No usable features found in the elite data for training.")
            return

        model, log_data = optimize_and_train_model(df_elite, model_save_path, features_in_data)

        if model and log_data:
            logger.info(f"AI model elite training completed. Model saved to {model_save_path}.")
            log_data["strategy"] = strategy_name
            log_df = pl.DataFrame([log_data])
            await append_csv(cfg, f"model_training_log_{datetime.now(timezone.utc):%Y%Ym%d}.csv", log_df.to_pandas())
        else:
            logger.error(f"AI model elite training failed for {strategy_name}.")

    except Exception as e:
        print(f"\n---!!! DETAILED ERROR in train_strategy_model for {strategy_name} !!!---")
        traceback.print_exc()
        print(f"---!!! END OF ERROR DETAILS !!!---\n")
        await log_error(logger, "train_strategy_model", error=e, strategy=strategy_name)

async def main():
    log_dir = Path(cfg.get_sync("system.log_dir", "logs"))
    
    profitable_files = sorted(log_dir.glob("wfa_profitable_trades_*.parquet"))
    if not profitable_files:
        logger.error("Profitable trades data file (wfa_profitable_trades_...parquet) not found. Cannot perform elite training.")
        return
        
    elite_data_path = profitable_files[-1]
    
    enabled_strategies = cfg.get_sync("trading.enabled_strategies", {"trend": True})
    strategies = [k for k, v in enabled_strategies.items() if v]
    for strategy in strategies:
        await train_strategy_model(strategy, elite_data_path)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())