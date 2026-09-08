"""
fetch_historical_data.py
OANDAから2025年以降のヒストリカルデータを一括取得し、年次Parquetファイルとして保存するスクリプト。
"""
import asyncio
import polars as pl
from datetime import datetime, timezone
from pathlib import Path
import sys

# プロジェクトルートをインポートパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core import cfg
from core.data import _fetch_from_oanda
from core.logger import get_logger

logger = get_logger(cfg, "historical_downloader")

async def download_all_historical():
    """
    USD_JPY, EUR_JPY, GBP_JPY, AUD_JPY の4通貨ペアについて、
    2025年1月1日から現在までのM15データをダウンロードし、保存する。
    """
    pairs = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"]
    start_date = "2025-01-01T00:00:00Z"
    end_date = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    data_dir = PROJECT_ROOT / cfg.get_sync("system.data_dir", "data")

    logger.info(f"--- Historical Data Download Started ---")
    logger.info(f"Target Period: {start_date} to {end_date}")

    for pair in pairs:
        try:
            logger.info(f"Processing currency pair: {pair}...")

            # OANDA APIからデータを一括取得 (core.dataの内部関数を利用)
            df = await _fetch_from_oanda(pair, gran="M15", start=start_date, end=end_date)

            if df is None or df.is_empty():
                logger.warning(f"No data fetched for {pair}.")
                continue

            # 年ごとにデータを分割して保存
            df_with_year = df.with_columns(pl.col("time").dt.year().alias("year"))

            pair_dir = data_dir / pair
            pair_dir.mkdir(parents=True, exist_ok=True)

            years = df_with_year["year"].unique().sort().to_list()
            for year in years:
                year_df = df_with_year.filter(pl.col("year") == year).select([
                    "time", "open", "high", "low", "close", "volume", "pair"
                ])
                save_path = pair_dir / f"{year}.parquet"
                year_df.write_parquet(save_path, use_pyarrow=True)
                logger.info(f"Successfully saved {len(year_df)} records for {pair} to {save_path}")

        except Exception as e:
            logger.error(f"Error processing {pair}: {e}", exc_info=True)

    logger.info(f"--- Historical Data Download Process Finished ---")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(download_all_historical())