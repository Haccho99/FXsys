# core/strategies/trend_strategy.py (v28 - Robust & Aligned)
import polars as pl
from typing import Optional

class LiveTrendStrategy:
    def __init__(self, params: dict | None = None):
        self.params = params if params is not None else {}

    async def check_signal(self, df_with_indicators: pl.DataFrame) -> Optional[str]:
        """
        指標がすでに追加されたDataFrameを基に、Trend戦略のシグナルを判定する。
        None値に対して安全なチェックを行い、シグナル方向のみを返す。
        """
        required_cols = ["ema_short", "ema_long"]
        if not all(col in df_with_indicators.columns for col in required_cols):
            return None

        # 最新のインジケーター値を取得
        latest_ema_short = df_with_indicators.get_column("ema_short")[-1]
        latest_ema_long = df_with_indicators.get_column("ema_long")[-1]

        # ▼▼▼【重要】Noneチェックを追加して堅牢化 ▼▼▼
        if latest_ema_short is None or latest_ema_long is None:
            return None
        
        # クロス状態を判定
        if latest_ema_short > latest_ema_long:
            return "long"

        if latest_ema_short < latest_ema_long:
            return "short"
            
        return None