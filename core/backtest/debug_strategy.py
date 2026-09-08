import sys
import os
import asyncio
import polars as pl

# プロジェクトのルートディレクトリをPythonパスに追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# backtesterからデータ読み込み関数を、strategyから戦略クラスをインポート
from backtest.backtester import _load_candles
from backtest.strategies.bt_trend_strategy import BacktestTrendStrategy
from logger import setup_logging, get_logger

# ロギングの設定
setup_logging()
logger = get_logger("debug_strategy")

async def debug_signal_generation():
    """戦略のシグナル生成ロジックをデバッグする。"""
    pair = "GBP_JPY"
    strategy_name = "trend"
    start_date = "2020-01-01T00:00:00"
    end_date = "2024-12-31T23:59:59"

    logger.info(f"--- Starting Signal Generation Debug for {pair}/{strategy_name} ---")

    # 1. データの読み込み
    logger.info("Loading candle data...")
    price_data_pd = await _load_candles(
        pair=pair, 
        start_date_str=start_date, 
        end_date_str=end_date
    )

    if price_data_pd is None or price_data_pd.empty:
        logger.error("Failed to load candle data. Aborting debug.")
        return

    price_data_pl = pl.from_pandas(price_data_pd.reset_index())
    logger.info(f"Successfully loaded {len(price_data_pl)} data points.")

    # 2. 戦略のインスタンス化（パラメータなしで呼び出し）
    logger.info("Instantiating strategy...")
    strategy_instance = BacktestTrendStrategy() # params=Noneで呼び出す

    # 3. シグナルの生成
    logger.info("Generating signals...")
    entries, short_entries, sl_prices, tp_prices, df_with_features = await strategy_instance.generate_signal(price_data_pl)

    # 4. シグナル数の確認
    logger.info("--- Signal Generation Analysis ---")
    long_signals_count = entries.sum()
    short_signals_count = short_entries.sum()

    print(f"\n[RESULT] Long entry signals found: {long_signals_count}")
    print(f"[RESULT] Short entry signals found: {short_signals_count}\n")

    if long_signals_count == 0 and short_signals_count == 0:
        logger.warning("No entry signals were generated for the entire period.")
        logger.warning("This is the reason no trades occurred and the CSV is empty.")
        logger.warning("Possible causes: EMA periods are too large, or signal conditions are too strict for the given data.")
    else:
        logger.info("Signals were generated. The issue might be in the backtester portfolio simulation.")

    # 5. 計算されたインジケーターの最後の数行を表示して確認
    logger.info("--- Feature DataFrame Tail (last 5 rows) ---")
    print(df_with_features.tail(5))
    logger.info("--- Debug session finished. ---")

if __name__ == "__main__":
    asyncio.run(debug_signal_generation())
