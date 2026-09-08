"""
run_wfa.py - 軽量版ウォークフォワード分析（ローリングウィンドウ方式）
"""
import argparse
import asyncio
import sys
import pandas as pd
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.config_manager import ConfigManager
from core.logger import get_logger

try:
    from walk_forward_analyzer import WalkForwardAnalyzer
except ImportError as e:
    print(f"Error: {e}")
    print("必ず 'walk_forward_analyzer.py' をプロジェクトのルートディレクトリに配置してください。")
    sys.exit(1)

cfg = ConfigManager(PROJECT_ROOT)
logger = get_logger(cfg, "run_wfa")

class LightweightWFA:
    def __init__(self, start_date: str, end_date: str):
        self.start_date = start_date
        self.end_date = end_date
        self.log_dir = PROJECT_ROOT / cfg.get_sync("system.log_dir", "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def run(self):
        logger.info("=== Starting Lightweight WFA (Rolling Window) ===")
        logger.info(f"Target Period: {self.start_date} to {self.end_date}")
        
        # 1. WFAループの実行 (WalkForwardAnalyzerを使用)
        analyzer = WalkForwardAnalyzer(
            mode="manual", 
            start_date=self.start_date, 
            end_date=self.end_date
        )
        
        logger.info("Running Walk-Forward Analysis...")
        
        # AI再学習用データの抽出(export_profitable)は、過学習防止のためFalseで実行
        analyzer.run_analysis(export_profitable=False)
        
        logger.info("=== Lightweight WFA Successfully Completed ===")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lightweight WFA Script (Rolling Window)")
    parser.add_argument("--months", type=int, default=6, help="直近何ヶ月分のデータでWFAを行うか")
    parser.add_argument("--end", type=str, default=None, help="終了日 (YYYY-MM-DD) 指定しない場合はローカルデータの最新日時")
    
    args = parser.parse_args()
    
    # 終了日の決定
    if args.end:
        end_dt = pd.to_datetime(args.end, utc=True)
    else:
        # 【新規追加】ローカルデータの最新時刻を自動取得してAPI通信を完全ブロック
        try:
            import polars as pl
            # 基準としてUSD_JPYのディレクトリを確認
            data_dir = PROJECT_ROOT / "data" / "USD_JPY"
            if data_dir.exists():
                parquet_files = sorted(list(data_dir.glob("*.parquet")))
                if parquet_files:
                    latest_file = parquet_files[-1]
                    # ファイルから一番新しい時刻を取得
                    df = pl.scan_parquet(latest_file)
                    latest_time = df.select(pl.col("time").max()).collect()[0, 0]
                    end_dt = pd.to_datetime(latest_time, utc=True)
                    logger.info(f"ローカルデータの最新時刻 ({end_dt}) をWFAの終了日として自動設定しました。（API通信を防止）")
                else:
                    end_dt = pd.Timestamp.now(tz='UTC')
            else:
                end_dt = pd.Timestamp.now(tz='UTC')
        except Exception as e:
            logger.warning(f"最新時刻の自動取得に失敗したため、現在時刻を使用します: {e}")
            end_dt = pd.Timestamp.now(tz='UTC')
        
    # 指定された月数だけ遡った時刻を開始日とする
    start_dt = end_dt - pd.DateOffset(months=args.months)
    
    # 時刻をOANDA互換のISOフォーマット(Z付き)に変換
    start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Windows特有のスレッド競合エラー（Set changed size during iteration）を防止
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    wfa_system = LightweightWFA(start_date=start_iso, end_date=end_iso)
    
    try:
        asyncio.run(wfa_system.run())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user.")