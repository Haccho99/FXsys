"""
walk_forward_analyzer.py (vFinal - Report Only & Profitable Export Mode v2 + Dynamic Regime)
"""
import sys
from pathlib import Path
import argparse
import subprocess
import polars as pl
from datetime import datetime, timedelta
from glob import glob
import re
import concurrent.futures
import multiprocessing as mp
import os
import asyncio
from core.backtest.optimize import run_full_optimization

from core.config_manager import ConfigManager
from core.logger import get_logger

project_root = Path(__file__).resolve().parent
cfg = ConfigManager(project_root)
logger = get_logger(cfg, "wfa")

def _run_single_optimizer_task_wrapper(kwargs: dict):
    """
    Wrapper to run the asyncio-based optimization function in a synchronous manner
    suitable for ProcessPoolExecutor.
    """
    pair = kwargs.get("pair")
    strategy_name = kwargs.get("strategy_name")
    try:
        logger.info(f"--- Starting parallel optimizer task for {pair}/{strategy_name} ---")
        asyncio.run(run_full_optimization(**kwargs))
        logger.info(f"--- Parallel optimizer task for {pair}/{strategy_name} completed successfully. ---")
        return (True, "")
    except BaseException:
        logger.exception(f"Optimizer task for {pair}/{strategy_name} FAILED with a fatal error.")
        return (False, f"Fatal error in {pair}/{strategy_name} optimizer task.")
    finally:
        # ▼ プロセス終了時のRedisコネクション明示的解放 ▼
        async def _cleanup_redis():
            try:
                # プロセス内のRedisクライアントを取得して閉じる
                from core.redis_client import get_redis
                client = await get_redis()
                if client:
                    await client.close()
            except Exception as e:
                logger.debug(f"Error during Redis cleanup for {pair}/{strategy_name}: {e}")
        
        try:
            asyncio.run(_cleanup_redis())
        except Exception:
            pass
        # ▲ 修正 ここまで ▲

class WalkForwardAnalyzer:
    def __init__(self, mode, start_date=None, end_date=None):
        self.mode = mode
        self.wfa_config = cfg.get_sync("tasks.walk_forward_analyzer", {})
        self.log_dir = project_root / cfg.get_sync("system.log_dir", "logs")
        if mode in ['manual', 'scheduled']:
            self.start_date = start_date
            self.end_date = end_date
            self._determine_analysis_period()

    def _determine_analysis_period(self):
        if self.mode == 'manual':
            if not self.start_date or not self.end_date:
                logger.critical("Manual mode requires --start-date and --end-date.")
                sys.exit(1)
        elif self.mode == 'scheduled':
            self.end_date = datetime.now()
            duration_days = self.wfa_config.get("scheduled_duration_days", 365)
            self.start_date = self.end_date - timedelta(days=duration_days)
        
        if isinstance(self.start_date, str): self.start_date = datetime.fromisoformat(self.start_date.replace("Z", "+00:00"))
        if isinstance(self.end_date, str): self.end_date = datetime.fromisoformat(self.end_date.replace("Z", "+00:00"))
        
        logger.info(f"Analysis period set from {self.start_date.date()} to {self.end_date.date()}")

    def _cleanup_trial_logs(self):
        logger.info("--- Cleaning up temporary trial logs ---")
        count = 0
        for f in self.log_dir.glob("optimize_backtest_log_pid_*.parquet"):
            try:
                f.unlink()
                count += 1
            except OSError as e:
                logger.warning(f"Could not delete temporary trial log {f.name}: {e}")
        if count > 0:
            logger.info(f"Removed {count} temporary trial log files.")

    def _cleanup_walk_logs(self, max_walk_num: int):
        logger.info(f"--- Cleaning up intermediate walk result files up to walk {max_walk_num} ---")
        count = 0
        for i in range(1, max_walk_num + 1):
            for f in self.log_dir.glob(f"final_optimized_backtest_*_walk{i}.parquet"):
                try:
                    f.unlink()
                    count += 1
                except OSError as e:
                    logger.warning(f"Could not delete intermediate walk log {f.name}: {e}")
        if count > 0:
            logger.info(f"Removed {count} intermediate walk result files.")

    def _run_optimizer(self, train_start, train_end, test_start, test_end, walk_num):
        logger.info(f"--- Starting Walk {walk_num}: Opt Phase ({train_start.date()} to {train_end.date()}) | Test Phase ({test_start.date()} to {test_end.date()}) ---")
        
        pairs = self.wfa_config.get("pairs_to_analyze", [])
        strategies = self.wfa_config.get("strategies_to_analyze", [])

        if not pairs or not strategies:
            logger.error("No pairs or strategies are defined in the optimizer configuration. Halting walk.")
            return False

        tasks_to_run = []
        for pair in pairs:
            for strategy_name in strategies:
                task_kwargs = {
                    "pair": pair,
                    "strategy_name": strategy_name,
                    "start_date": train_start.isoformat(),
                    "end_date": train_end.isoformat(),
                    "output_suffix": f"_walk{walk_num}",
                    "walk_number": walk_num,
                    "train_start": train_start.isoformat(),
                    "train_end": train_end.isoformat(),
                    "test_start": test_start.isoformat(),
                    "test_end": test_end.isoformat(),
                    "wfa_mode": True
                }
                tasks_to_run.append(task_kwargs)

        if not tasks_to_run:
            logger.warning("No optimizer tasks were generated for this walk.")
            return True

        # メモリ枯渇（OOM）を防ぐため、__init__で定義された self.wfa_config から設定を取得
        default_workers = max(1, os.cpu_count() // 2)
        max_workers = self.wfa_config.get("max_workers", default_workers)
            
        logger.info(f"Starting parallel optimization for {len(tasks_to_run)} tasks using up to {max_workers} workers.")
        
        all_success = True
        spawn_ctx = mp.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers, mp_context=spawn_ctx) as executor:
            try:
                results = executor.map(_run_single_optimizer_task_wrapper, tasks_to_run)
                for success, error_message in results:
                    if not success:
                        all_success = False
                        logger.critical(f"A parallel optimizer task failed. Halting WFA for Walk {walk_num}. Error: {error_message}")
                        break 
            except KeyboardInterrupt:
                logger.warning(f"WFA process interrupted by user during Walk {walk_num}. Stopping analysis.")
                return False

        self._cleanup_trial_logs()

        if not all_success:
            return False

        logger.info(f"--- All parallel optimizations for Walk {walk_num} completed. ---")
        return True

    def _clear_redis_cache(self):
        logger.info("--- Clearing Redis Cache for next walk ---")
        try:
            import sys
            venv_python_path = sys.executable
            command = [str(venv_python_path), str(project_root / "clear_redis_cache.py")]
            result = subprocess.run(
                command, check=True, timeout=60, capture_output=True, text=True, encoding='utf-8'
            )
            logger.info("Redis cache cleared successfully.", stdout=result.stdout.strip())
        except subprocess.CalledProcessError as e:
            logger.error(
                "The 'clear_redis_cache.py' script failed to execute.",
                return_code=e.returncode, stdout=e.stdout, stderr=e.stderr
            )
        except Exception as e:
            logger.error("An unexpected error occurred while clearing Redis cache.", exc_info=True)

    # ＝＝＝ ▼ 追加：レジーム判定ヘルパー関数 ▼ ＝＝＝
    def _evaluate_regime_and_get_days_sync(self, pair: str, target_date: datetime) -> int:
        """
        直近30日のParquetデータを同期的に読み込み、ADX(14)を計算して相場レジームを判定する。
        """
        try:
            import polars as pl
            import pandas as pd
            from datetime import timedelta
            # 既存の同期インジケーター計算ロジックを流用（非同期ラッパーをバイパス）
            from core.indicators import _adx

            data_dir = project_root / "data" / pair
            if not data_dir.exists():
                logger.warning(f"[{pair}] データディレクトリが存在しません。デフォルトの90日を使用します。")
                return 90

            # 対象日時から過去30日分をスライスするための開始日時
            start_date = target_date - timedelta(days=30)

            # scan_parquetで全ファイルを仮想的に読み込み、必要な期間だけを実メモリに展開（超高速）
            df = pl.scan_parquet(str(data_dir / "*.parquet")) \
                   .filter((pl.col("time") >= start_date) & (pl.col("time") < target_date)) \
                   .collect()

            # ADX(14)の計算には最低28本のバーが必要
            if len(df) < 30:
                logger.warning(f"[{pair}] レジーム判定用のデータが不足しています（{len(df)}行）。デフォルトの90日を使用します。")
                return 90

            # ADXの計算（_adx は adx, plus_di, minus_di を返す）
            adx_val, _, _ = _adx(df["high"].to_pandas(), df["low"].to_pandas(), df["close"].to_pandas(), 14)

            # 最新のADX値を取得
            latest_adx = adx_val.iloc[-1]

            if pd.isna(latest_adx):
                return 90

            # レジームに応じた日数の返却
            if latest_adx > 25:
                return 28  # 短期トレンド相場（直近の傾向を重視）
            elif latest_adx < 20:
                return 120 # 長期レンジ相場（長期間のノイズを平均化）
            else:
                return 90  # 標準相場

        except Exception as e:
            logger.error(f"レジーム判定中にエラーが発生しました: {e}。デフォルトの90日を使用します。")
            return 90
    # ＝＝＝ ▲ 追加 ここまで ▲ ＝＝＝

    def run_analysis(self, export_profitable: bool):
        """ウォークフォワード分析のメインループを実行する"""
        wfa_success = True
        max_completed_walk = 0
        try:
            # ▼ 変更: train_daysは動的決定されるため固定取得を削除 ▼
            test_days = self.wfa_config.get("testing_period_days", 30)
            pairs = self.wfa_config.get("pairs_to_analyze", [])
            base_pair = pairs[0] if pairs else "USD_JPY"
            
            current_date = self.start_date
            walk_num = 1
            all_walk_results = []

            logger.info("--- Cleaning up old trial logs before starting WFA ---")
            self._cleanup_trial_logs()

            # ▼ 変更: ループ構造を無限ループによるBreak方式に書き換え ▼
            while True:
                # 各Walkの先頭でレジーム判定を行い、インサンプル日数を動的に決定
                train_days = self._evaluate_regime_and_get_days_sync(base_pair, current_date)

                # 終了条件の判定（テスト期間の末尾がend_dateを超える場合は終了）
                if current_date + timedelta(days=train_days + test_days) > self.end_date:
                    break

                # 動的に決定された日数をログ出力
                logger.info(f"Walk {walk_num}: レジーム判定によりインサンプル期間を {train_days} 日に変更して実行します。")

                train_start = current_date
                train_end = train_start + timedelta(days=train_days)
                test_start = train_end
                test_end = test_start + timedelta(days=test_days)
                
                if not self._run_optimizer(train_start, train_end, test_start, test_end, walk_num):
                    logger.error(f"Stopping WFA due to optimizer failure in Walk {walk_num}.")
                    wfa_success = False
                    break
                
                for f in self.log_dir.glob(f"final_optimized_backtest_*_walk{walk_num}.parquet"):
                    try:
                        df_walk = pl.read_parquet(f)
                        all_walk_results.append(df_walk)
                    except Exception as e:
                        logger.warning(f"Could not read or process result file {f.name}: {e}")

                self._clear_redis_cache()
                max_completed_walk = walk_num
                current_date += timedelta(days=test_days)
                walk_num += 1

        except KeyboardInterrupt:
            logger.warning("\n--- Analysis interrupted by user. Shutting down gracefully. ---")
            wfa_success = False

        if not wfa_success:
            logger.error("WFA did not complete successfully. Intermediate results are preserved. Final report was not generated.")
            return

        if not all_walk_results:
            logger.error("WFA finished but no results were collected. Check optimizer logs.")
            return

        logger.info("--- WFA successfully completed. Generating final reports... ---")
        final_report_df = pl.concat(all_walk_results, how="diagonal")
        self._save_reports(final_report_df, export_profitable)
        
        self._cleanup_walk_logs(max_completed_walk)

    def run_report_aggregation(self, input_pattern: str, export_profitable: bool):
        """
        指定されたパターンの中間ファイルを読み込み、最終レポートのみを生成する。
        """
        logger.info(f"--- Starting WFA Report Aggregation Mode ---")
        logger.info(f"Searching for files matching pattern: {input_pattern}")

        file_list = glob(input_pattern)
        if not file_list:
            logger.error(f"No intermediate result files found for pattern: {input_pattern}")
            return

        logger.info(f"Found {len(file_list)} intermediate result files to aggregate.")
        all_walk_results = []
        
        for f_path in file_list:
            try:
                f = Path(f_path)
                df_walk = pl.read_parquet(f)
                
                match = re.search(r"_walk(\d+)", f.name)
                if match:
                    walk_num = int(match.group(1))
                    df_with_context = df_walk.with_columns(pl.lit(walk_num).alias("Walk Number"))
                    all_walk_results.append(df_with_context)
                else:
                    logger.warning(f"Could not extract walk number from filename: {f.name}. Skipping this file.")
                
            except Exception as e:
                logger.warning(f"Could not read or process result file {f.name}: {e}")
        
        if not all_walk_results:
            logger.error("Aggregation finished but no results were collected.")
            return

        final_report_df = pl.concat(all_walk_results, how="diagonal")
        self._save_reports(final_report_df, export_profitable)

    def _save_reports(self, final_df: pl.DataFrame, export_profitable: bool):
        """最終レポートと、オプションで利益が出たWalkのデータを保存する。"""
        report_path = self.log_dir / f"wfa_final_report_{datetime.now():%Y%m%d_%H%M%S}.parquet"
        final_df.write_parquet(report_path)
        logger.info("--- WFA Report Generation Completed ---")
        logger.info(f"Final report saved to: {report_path}")

        if export_profitable:
            logger.info("Exporting profitable trades for AI 'elite education'...")
            
            if "Walk Number" not in final_df.columns:
                logger.error("'Walk Number' column not found in the data. Cannot export profitable walks.")
                return

            walk_summary = final_df.group_by("Walk Number").agg(pl.col("PnL").sum().alias("PnL"))
            profitable_walk_numbers = walk_summary.filter(pl.col("PnL") > 0)["Walk Number"]
            
            if profitable_walk_numbers.len() > 0:
                df_profitable_only = final_df.filter(pl.col("Walk Number").is_in(profitable_walk_numbers))
                
                profitable_path = self.log_dir / f"wfa_profitable_trades_{datetime.now():%Y%m%d_%H%M%S}.parquet"
                df_profitable_only.write_parquet(profitable_path)
                logger.info(f"Successfully exported {len(df_profitable_only)} profitable trades to: {profitable_path}")
            else:
                logger.warning("No profitable walks found. 'Profitable trades' file was not created.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-Forward Analyzer for trading strategies.")
    parser.add_argument("--mode", type=str, required=True, choices=["manual", "scheduled", "report_only"], help="Execution mode.")
    parser.add_argument("--start-date", type=str, help="Start date for analysis (ISO format). Required for manual mode.")
    parser.add_argument("--end-date", type=str, help="End date for analysis (ISO format). Required for manual mode.")
    parser.add_argument("--input-pattern", type=str, help="Glob pattern for input files in report_only mode (e.g., 'logs/final_*.parquet').")
    parser.add_argument("--export-profitable", action="store_true", help="Export a separate parquet file containing trades from profitable walks only.")
    
    args = parser.parse_args()

    analyzer = WalkForwardAnalyzer(mode=args.mode, start_date=args.start_date, end_date=args.end_date)
    
    if args.mode == 'report_only':
        if not args.input_pattern:
            logger.critical("--input-pattern is required for 'report_only' mode.")
            sys.exit(1)
        analyzer.run_report_aggregation(args.input_pattern, args.export_profitable)
    else:
        analyzer.run_analysis(args.export_profitable)