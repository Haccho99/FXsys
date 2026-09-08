"""
data_downloader.py (vFinal - Schema Aligned with Archiver)
"""
import asyncio
import polars as pl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles
import sys

# プロジェクトルートを設定し、coreパッケージをインポート可能にする
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from core.config_manager import ConfigManager
from core.logger import get_logger, log_error

# --- 初期設定 ---
cfg = ConfigManager(project_root)
logger = get_logger(cfg, "data_downloader")

class DataDownloader:
    """OANDAから長期間の価格データを一括ダウンロードし、Parquetファイルとして保存するツール。"""

    def __init__(self):
        try:
            self.data_dir = Path(cfg.get_sync("system.data_dir", "data"))
            self.data_dir.mkdir(parents=True, exist_ok=True)
            
            active_profile_name = cfg.get_sync("oanda.current_profile")
            all_profiles = cfg.get_sync("oanda.profiles", {})
            oanda_cfg = all_profiles.get(active_profile_name, {})
            
            if not oanda_cfg or "access_token" not in oanda_cfg:
                raise ValueError(f"Profile '{active_profile_name}' not found or is missing 'access_token'")
            
            self.api = API(
                access_token=oanda_cfg["access_token"],
                environment="practice" if "practice" in oanda_cfg.get("api_url", "") else "live"
            )
        except Exception as e:
            logger.critical("DataDownloader failed to initialize.", exc_info=True)
            raise

    def _get_latest_local_time(self, pair: str) -> datetime | None:
        """【新規追加】ローカルに保存されているParquetファイルから最新のデータ日時を取得する"""
        save_dir = self.data_dir / pair
        if not save_dir.exists():
            return None
            
        parquet_files = list(save_dir.glob("*.parquet"))
        if not parquet_files:
            return None
            
        # ファイル名（年）でソートして最新の年を取得
        latest_file = sorted(parquet_files)[-1]
        try:
            df = pl.scan_parquet(latest_file)
            latest_time = df.select(pl.col("time").max()).collect()[0, 0]
            if latest_time is not None:
                # PolarsのDatetimeをPythonのdatetimeに変換し、UTCタイムゾーンを設定
                return latest_time.replace(tzinfo=timezone.utc)
        except Exception as e:
            logger.warning(f"Failed to read latest time from {latest_file}: {e}")
        return None

    async def _fetch_single_chunk(self, pair: str, gran: str, start: datetime, end: datetime) -> pl.DataFrame | None:
        """APIの制限に合わせて単一のデータチャンクを取得し、DataFrameとして返す"""
        params = {"granularity": gran, "from": start.isoformat(), "to": end.isoformat(), "price": "M"}
        r = InstrumentsCandles(instrument=pair, params=params)
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, self.api.request, r)
            
            candles = [
                {"time": c["time"], "open": float(c["mid"]["o"]), "high": float(c["mid"]["h"]),
                 "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]), "volume": int(c["volume"])}
                for c in response.get("candles", []) if c.get("complete")
            ]

            if not candles:
                return None

            df = pl.from_dicts(candles)
            df = df.with_columns(pl.col("time").str.to_datetime().dt.replace_time_zone("UTC"))
            return df
        except Exception as e:
            await log_error(logger, "_fetch_single_chunk", error=e, pair=pair)
            return None

    async def _fetch_data_in_chunks(self, pair: str, gran: str, start_date_str: str, end_date_str: str) -> pl.DataFrame | None:
        """指定された期間のデータを5000件ずつのチャンクで取得し、結合する。"""
        start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        
        all_chunks = []
        current_start = start_date
        
        while current_start < end_date:
            chunk_end = current_start + timedelta(days=34) # 5000件制限を確実にクリアするため、約1ヶ月単位で取得
            if chunk_end > end_date:
                chunk_end = end_date
            
            logger.info(f"Fetching chunk for {pair} from {current_start.date()} to {chunk_end.date()}")
            chunk_df = await self._fetch_single_chunk(pair, gran, current_start, chunk_end)
            if chunk_df is not None and not chunk_df.is_empty():
                all_chunks.append(chunk_df)
            
            await asyncio.sleep(0.5) # APIレート制限のための短い待機
            current_start = chunk_end

        return pl.concat(all_chunks) if all_chunks else None
        
    async def download_historical_data(self, pair: str, gran: str, start_date_str: str, end_date_str: str):
        """指定されたペアと粒度の過去データをダウンロードし、年ごとのParquetファイルに追記・保存する。"""
        save_dir = self.data_dir / pair
        save_dir.mkdir(parents=True, exist_ok=True)
        
        # ▼▼▼ 差分取得（空白期間の穴埋め）ロジック ▼▼▼
        latest_time = self._get_latest_local_time(pair)
        if latest_time:
            fetch_start = latest_time
            logger.info(f"[{pair}] 既存データを検出しました。空白期間（{fetch_start.strftime('%Y-%m-%d %H:%M')} 以降）を差分ダウンロードします。")
            fetch_start_str = fetch_start.isoformat()
        else:
            logger.info(f"[{pair}] 既存データがありません。設定値（{start_date_str}）から新規フルダウンロードします。")
            fetch_start_str = start_date_str
            
        end_date_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        fetch_start_dt = datetime.fromisoformat(fetch_start_str.replace("Z", "+00:00"))
        
        # 既に最新の場合はダウンロードしない
        if fetch_start_dt >= end_date_dt:
            logger.info(f"[{pair}] データは既に最新です。ダウンロードをスキップします。")
            return

        all_data_df = await self._fetch_data_in_chunks(pair, gran, fetch_start_str, end_date_str)

        if all_data_df is not None and not all_data_df.is_empty():
            final_df = all_data_df.with_columns(
                pl.lit(pair).alias("pair"),
                pl.col("time").dt.year().alias("year") # 年ごとの分割のため
            )
            
            final_columns = ["time", "open", "high", "low", "close", "volume", "pair"]
            
            for year, data in final_df.group_by("year"):
                year_str = str(year[0])
                save_path = save_dir / f"{year_str}.parquet"
                
                data_to_save = data.select(final_columns)
                
                # ▼▼▼ 既存ファイルへの安全な結合（マージ）ロジック ▼▼▼
                if save_path.exists():
                    try:
                        existing_df = pl.read_parquet(save_path)
                        # 既存データと新規データを結合し、時間で重複排除とソートを行う
                        data_to_save = pl.concat([existing_df, data_to_save]).unique(subset=["time"], keep="last").sort("time")
                    except Exception as e:
                        logger.error(f"Failed to merge existing data for {pair} ({year_str}): {e}")
                
                data_to_save.write_parquet(save_path, use_pyarrow=True)
                logger.info(f"Saved {len(data_to_save)} records for {pair} ({year_str}) to {save_path}")
        else:
            logger.warning(f"No new data was downloaded for {pair} ({gran}) in the specified range.")


async def main():
    """スクリプトのメイン実行関数。"""
    downloader_config = cfg.get_sync("tasks.data_downloader", {})
    pairs = downloader_config.get("pairs", [])
    granularities = downloader_config.get("granularities", [])
    start_date = downloader_config.get("start_date")
    end_date = downloader_config.get("end_date", datetime.now(timezone.utc).isoformat())

    if not all([pairs, granularities, start_date]):
        logger.critical("Config for 'data_downloader' is incomplete. Please check 'pairs', 'granularities', and 'start_date'.")
        return

    downloader = DataDownloader()
    
    tasks = []
    for pair in pairs:
        for gran in granularities:
            tasks.append(downloader.download_historical_data(pair, gran, start_date, end_date))
    
    await asyncio.gather(*tasks)
    logger.info("--- Historical data download process finished. ---")


if __name__ == "__main__":
    asyncio.run(main())