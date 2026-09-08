"""test_backtester.py (v2 - Corrected mock return value)"""
# 修正日: 2025-07-08 (Gemini)
# 修正内容:
# - generate_signalのモックが、実装に合わせて2つの値のタプルを返すように修正。
#   これによりValueErrorを解消。

import pytest
import pandas as pd
from unittest.mock import patch, AsyncMock, MagicMock
import polars as pl

# run_backtestをインポート
from backtester import run_backtest

pytestmark = pytest.mark.asyncio

@pytest.fixture
def sample_price_data():
    """バックテスト用のサンプル価格データを準備"""
    index = pd.to_datetime(pd.date_range(start="2025-01-01", periods=100, freq="15T"))
    return pd.DataFrame({"close": range(100, 200), "high": range(101, 201), "low": range(99, 199)}, index=index)

async def test_run_backtest_with_dynamic_strategy(sample_price_data):
    """指定された戦略モジュールを動的に読み込み、バックテストが実行されることをテスト"""
    mock_strategy_module = AsyncMock()
    
    # --- MODIFIED: generate_signalの戻り値を5つの値のタプルに修正 ---
signals = pl.Series("signal", ["long", None, "short"] * 33 + ["long"])
scores = pl.Series("score", [0.7, 0.0, 0.7] * 33 + [0.7])
sl_prices = pl.Series("sl_prices", [100.0, None, 90.0] * 33 + [100.0]) # 仮のSL価格
tp_prices = pl.Series("tp_prices", [110.0, None, 120.0] * 33 + [110.0]) # 仮のTP価格
# price_data_with_featuresは、run_backtest内で生成されるため、ここではモックしない
# ただし、generate_signalの戻り値の3番目の要素としてダミーのPolars DataFrameを返す
dummy_df_with_features = pl.DataFrame({"time": sample_price_data.index.to_list(), "close": sample_price_data["close"].to_list()})

@pytest.fixture
def mock_strategy_instance(sample_price_data):
    """Mock strategy instance with a 5-value return for generate_signal."""
    mock_strategy = AsyncMock()
    entries = pl.Series("entries", [True, False, False] * 33 + [True])
    short_entries = pl.Series("short_entries", [False, False, True] * 33 + [False])
    sl_prices = pl.Series("sl_prices", [100.0 if entries[i] else 110.0 if short_entries[i] else None for i in range(100)])
    tp_prices = pl.Series("tp_prices", [110.0 if entries[i] else 100.0 if short_entries[i] else None for i in range(100)])
    df_with_features = pl.DataFrame({
        "time": sample_price_data.index.to_list(),
        "close": range(100, 200),
        "atr": [10.0] * 100
    })
    mock_strategy.generate_signal.return_value = (entries, short_entries, sl_prices, tp_prices, df_with_features)
    return mock_strategy

async def test_run_backtest_with_dynamic_strategy(sample_price_data, mock_strategy_instance):
    """指定された戦略モジュールを動的に読み込み、バックテストが実行されることをテスト"""
    mock_portfolio = MagicMock()
    mock_portfolio.stats.return_value = {"Profit Factor": 1.8, "Sharpe Ratio": 1.2}
    mock_portfolio.trades.records_readable = pd.DataFrame({
        "Entry Timestamp": pd.to_datetime(["2025-01-01 00:00:00"]),
        "Exit Timestamp": pd.to_datetime(["2025-01-01 01:00:00"]),
        "Entry Price": [100.0],
        "Exit Price": [105.0],
        "PnL": [5.0],
        "Status": ["TP"],
        "Duration": [pd.Timedelta(hours=1)],
        "Size": [1],
        "Direction": ["Long"]
    })

    with (
        patch("backtester._load_candles", AsyncMock(return_value=sample_price_data)),
        patch("backtester._load_strategy_module") as mock_load_strategy,
        patch("backtester.vbt.Portfolio.from_signals", return_value=mock_portfolio) as mock_from_signals
    ):
        MockStrategyClass = MagicMock()
        MockStrategyClass.return_value = mock_strategy_instance
        mock_load_strategy.return_value = MagicMock(BacktestTrendStrategy=MockStrategyClass)

        result = await run_backtest("USD_JPY", "trend", output_log_filename="test.csv")

        mock_load_strategy.assert_called_once_with("trend")
        mock_from_signals.assert_called_once()

        # vbt.Portfolio.from_signalsがsl_stopとtp_stop引数付きで呼び出されることを検証
        call_args, call_kwargs = mock_from_signals.call_args
        assert 'sl_stop' in call_kwargs
        assert 'tp_stop' in call_kwargs

        # sl_pricesとtp_pricesが正しく渡されていることを検証
        # Polars SeriesをPandas Seriesに変換し、インデックスを合わせる
        expected_sl_prices_pd = mock_strategy_instance.generate_signal.return_value[2].to_pandas().reindex(sample_price_data.index)
        expected_tp_prices_pd = mock_strategy_instance.generate_signal.return_value[3].to_pandas().reindex(sample_price_data.index)

        pd.testing.assert_series_equal(call_kwargs['sl_stop'], expected_sl_prices_pd, check_dtype=False)
        pd.testing.assert_series_equal(call_kwargs['tp_stop'], expected_tp_prices_pd, check_dtype=False)

        assert result["Sharpe Ratio"] == 1.2

mock_portfolio = MagicMock()
mock_portfolio.stats.return_value = {"Profit Factor": 1.8, "Sharpe Ratio": 1.2}

# 戦略クラスのモック
MockStrategyClass = MagicMock()
MockStrategyClass.return_value = MagicMock(generate_signal=AsyncMock(return_value=(signals, scores, dummy_df_with_features, sl_prices, tp_prices)))

with (
    patch("backtester._load_candles", AsyncMock(return_value=sample_price_data)),
    patch("backtester._load_strategy_module") as mock_load_strategy,
    patch("backtester.vbt.Portfolio.from_signals", return_value=mock_portfolio) as mock_from_signals
):

    # _load_strategy_moduleが返すモジュール内にモックされた戦略クラスを設定
    mock_load_strategy.return_value = MagicMock(BacktestTrendStrategy=MockStrategyClass)

    result = await run_backtest("USD_JPY", "trend")

    mock_load_strategy.assert_called_once_with("trend")
    mock_from_signals.assert_called_once()

    # vbt.Portfolio.from_signalsがsl_stopとtp_stop引数付きで呼び出されることを検証
    call_args, call_kwargs = mock_from_signals.call_args
    assert 'sl_stop' in call_kwargs
    assert 'tp_stop' in call_kwargs

    # sl_pricesとtp_pricesが正しく渡されていることを検証
    # Polars SeriesをPandas Seriesに変換し、インデックスを合わせる
    expected_sl_prices_pd = sl_prices.to_pandas().reindex(sample_price_data.index)
    expected_tp_prices_pd = tp_prices.to_pandas().reindex(sample_price_data.index)

    pd.testing.assert_series_equal(call_kwargs['sl_stop'], expected_sl_prices_pd, check_dtype=False)
    pd.testing.assert_series_equal(call_kwargs['tp_stop'], expected_tp_prices_pd, check_dtype=False)

    assert result["Sharpe Ratio"] == 1.2