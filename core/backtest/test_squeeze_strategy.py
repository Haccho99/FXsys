import sys
import os

# プロジェクトのルートディレクトリをPythonパスに追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""backtest/test_squeeze_strategy.py (v4 - Adapted for 5 return values)"""
import pytest
import polars as pl
import numpy as np
from unittest.mock import patch, AsyncMock

from backtest.strategies.bt_bb_squeeze_strategy import BacktestBbSqueezeStrategy

pytestmark = pytest.mark.asyncio

@pytest.fixture
def sample_df():
    return pl.DataFrame({
        "close": np.random.rand(100) + 149.5,
        "high": np.random.rand(100) + 150.0,
        "low": np.random.rand(100) + 149.0
    })

async def test_generate_signal_returns_five_values(sample_df):
    """generate_signalが5つの値を返すことをテスト"""
    strategy = BacktestBbSqueezeStrategy()
    with patch.object(strategy, '_calculate_indicators', new_callable=AsyncMock) as mock_calc:
        # _calculate_indicatorsが適切な列を持つDataFrameを返すように設定
        mock_df = sample_df.with_columns([
            pl.Series("bb_width", np.random.rand(100)),
            pl.Series("upper", np.random.rand(100) + 151),
            pl.Series("lower", np.random.rand(100) + 148),
            pl.Series("macd_hist", np.random.rand(100) - 0.5),
            pl.Series("rsi", np.random.rand(100) * 100),
            pl.Series("atr", np.random.rand(100) * 0.1)
        ])
        mock_calc.return_value = mock_df

        result = await strategy.generate_signal(sample_df)
        assert len(result) == 5
        entries, short_entries, sl_prices, tp_prices, df_with_features = result
        
        assert isinstance(entries, pl.Series)
        assert isinstance(short_entries, pl.Series)
        assert isinstance(sl_prices, pl.Series)
        assert isinstance(tp_prices, pl.Series)
        assert isinstance(df_with_features, pl.DataFrame)

async def test_generate_signal_long_when_all_conditions_met(sample_df):
    """全ての条件が満たされた場合にロングシグナルとスコアを返すことをテスト"""
    strategy = BacktestBbSqueezeStrategy()
    with patch.object(strategy, '_calculate_indicators', new_callable=AsyncMock) as mock_calc:
        mock_df = sample_df.with_columns([
            pl.Series("bb_width", [0.1] * 100),
            pl.Series("upper", [150.0] * 100),
            pl.Series("lower", [149.0] * 100),
            pl.Series("macd_hist", [0.1] * 100),
            pl.Series("rsi", [60] * 100),
            pl.Series("atr", [0.1] * 100)
        ])
        # Breakout condition
        mock_df = mock_df.with_columns(pl.Series("close", mock_df["close"].to_list()[:-1] + [150.1]))
        mock_calc.return_value = mock_df

        entries, _, _, _, _ = await strategy.generate_signal(sample_df)
        assert entries.to_list()[-1] is True

async def test_generate_signal_none_when_macd_not_confirm(sample_df):
    """MACDが不一致の場合にシグナルが出ないことをテスト"""
    strategy = BacktestBbSqueezeStrategy()
    with patch.object(strategy, '_calculate_indicators', new_callable=AsyncMock) as mock_calc:
        mock_df = sample_df.with_columns([
            pl.Series("bb_width", [0.1] * 100),
            pl.Series("upper", [150.0] * 100),
            pl.Series("lower", [149.0] * 100),
            pl.Series("macd_hist", [-0.1] * 100), # MACD condition fails
            pl.Series("rsi", [60] * 100),
            pl.Series("atr", [0.1] * 100)
        ])
        mock_df = mock_df.with_columns(pl.Series("close", mock_df["close"].to_list()[:-1] + [150.1]))
        mock_calc.return_value = mock_df

        entries, _, _, _, _ = await strategy.generate_signal(sample_df)
        assert entries.to_list()[-1] is False

async def test_generate_signal_none_when_not_squeeze(sample_df):
    """スクイーズ状態でない場合にシグナルが出ないことをテスト"""
    strategy = BacktestBbSqueezeStrategy()
    with patch.object(strategy, '_calculate_indicators', new_callable=AsyncMock) as mock_calc:
        mock_df = sample_df.with_columns([
            pl.Series("bb_width", np.random.uniform(low=0.5, high=1.0, size=100)), # Squeeze condition fails
            pl.Series("upper", [150.0] * 100),
            pl.Series("lower", [149.0] * 100),
            pl.Series("macd_hist", [0.1] * 100),
            pl.Series("rsi", [60] * 100),
            pl.Series("atr", [0.1] * 100)
        ])
        mock_df = mock_df.with_columns(pl.Series("close", mock_df["close"].to_list()[:-1] + [150.1]))
        mock_calc.return_value = mock_df

        entries, _, _, _, _ = await strategy.generate_signal(sample_df)
        assert entries.to_list()[-1] is False
