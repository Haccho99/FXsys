"""core/strategies/base/base_trend.py (v4 - Final Abstract Base with Import Fix)
"""
# ▼▼▼ 修正箇所 START ▼▼▼
# 抽象基底クラス(ABC)を定義するために必要なモジュールをインポート
from abc import ABC, abstractmethod
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

import polars as pl
from typing import Tuple

class BaseBacktestStrategy(ABC):
    """
    全てのバックテスト戦略が継承すべき抽象基底クラス。
    シグナル生成のインターフェースを定義する。
    """
    @abstractmethod
    async def generate_signal(self, df: pl.DataFrame) -> Tuple[pl.Series, pl.Series, pl.Series, pl.Series, pl.DataFrame]:
        """
        価格データフレームを受け取り、エントリー、ショートエントリー、
        SL価格、TP価格、そして特徴量付きDataFrameをタプルとして返す。
        """
        pass
