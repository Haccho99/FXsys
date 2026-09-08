# core/strategies/bb_squeeze_strategy.py (v27 - Robust & Aligned)
from __future__ import annotations
import polars as pl
from typing import Optional

from core import cfg
from core.logger import get_logger

logger = get_logger(cfg, "live_squeeze_strategy")

class LiveSqueezeStrategy:
    def __init__(self, params: dict | None = None):
        self.params = params if params is not None else {}
        # パラメータを初期化
        self.width_pctl = self.params.get("bb_squeeze_pctl", 0.85) # config.jsonに合わせる
        self.use_macd_filter = self.params.get("use_macd_filter", False)
        self.use_rsi_filter = self.params.get("use_rsi_filter", False)
        self.rsi_long_th = self.params.get("rsi_long_threshold", 40)
        self.rsi_short_th = self.params.get("rsi_short_threshold", 60)
        logger.debug("LiveSqueezeStrategy initialized.")

    async def check_signal(self, df: pl.DataFrame) -> Optional[str]:
        """
        指標がすでに追加されたDataFrameを基に、Squeeze戦略のシグナル方向を判定する。
        """
        # ▼▼▼ 修正: カラム名を indicators.py の出力に合わせる ('hist' -> 'macd_hist') ▼▼▼
        required_cols = ["bb_width", "upper", "lower", "close", "macd_hist", "rsi"]
        if not all(col in df.columns for col in required_cols):
            logger.warning("Required columns for Squeeze strategy are missing. Skipping signal check.")
            return None

        valid_widths = df.get_column("bb_width").drop_nans()
        if valid_widths.is_empty(): 
            return None
        
        width_threshold = valid_widths.quantile(self.width_pctl)
        if width_threshold is None:
            return None

        latest = df.row(-1, named=True)
        
        # ▼▼▼ 修正: Noneチェックを追加して堅牢化 ▼▼▼
        required_values = [latest.get(key) for key in required_cols]
        if any(value is None for value in required_values):
            return None
        
        signal = None
        is_squeezed = latest["bb_width"] < width_threshold

        # ロングシグナルの判定
        if is_squeezed and latest["close"] > latest["upper"]:
            macd_ok = not self.use_macd_filter or latest["macd_hist"] > 0
            rsi_ok = not self.use_rsi_filter or latest["rsi"] > self.rsi_long_th
            if macd_ok and rsi_ok:
                signal = "long"
        
        # ショートシグナルの判定
        elif is_squeezed and latest["close"] < latest["lower"]:
            macd_ok = not self.use_macd_filter or latest["macd_hist"] < 0
            rsi_ok = not self.use_rsi_filter or latest["rsi"] < self.rsi_short_th
            if macd_ok and rsi_ok:
                signal = "short"

        return signal