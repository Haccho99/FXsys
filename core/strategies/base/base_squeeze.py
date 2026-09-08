"""
strategies/base/base_squeeze.py (vFinal - Refactored)
"""
from __future__ import annotations
import polars as pl
from typing import Optional, Dict, Any

from core import cfg
from core.logger import get_logger

logger = get_logger(cfg, "base_squeeze_strategy")

class BaseSqueezeStrategy:
    def __init__(self, params: Optional[Dict[str, Any]] = None):
        p = params if params is not None else {}
        self.params = p
        squeeze_cfg = cfg.get_sync("indicators.squeeze", {})
        self.period = p.get("bb_period", squeeze_cfg.get("period", 20))
        self.std_dev = p.get("bb_std", squeeze_cfg.get("std_dev", 2.0))
        self.width_pctl = p.get("bb_squeeze_pctl", squeeze_cfg.get("width_percentile", 0.10))
        self.use_macd_filter = squeeze_cfg.get("use_macd_filter", True)
        self.use_rsi_filter = squeeze_cfg.get("use_rsi_filter", True)
        self.rsi_long_th = p.get("rsi_long_th", squeeze_cfg.get("rsi_long_th", 55))
        self.rsi_short_th = p.get("rsi_short_th", squeeze_cfg.get("rsi_short_th", 45))
        self.sl_atr_multiplier = p.get("sl_atr_multiplier", 2.0)
        self.tp_atr_multiplier = p.get("tp_atr_multiplier", 1.5)

    def _evaluate_signals(self, df_with_features: pl.DataFrame) -> pl.DataFrame:
        """指標がすでに追加されたDataFrameを基に、Squeeze戦略のシグナルを判定する。"""
        required_cols = ["width", "upper", "lower", "close", "hist", "rsi", "atr"]
        if not all(col in df_with_features.columns for col in required_cols):
            logger.warning(f"SqueezeStrategy: Required columns missing. Cannot generate signal.")
            return pl.DataFrame()

        width_threshold = df_with_features["width"].drop_nans().quantile(self.width_pctl)
        is_squeezed = pl.col("width") < width_threshold

        long_cond = is_squeezed & (pl.col("close") > pl.col("upper")) & \
                    (pl.lit(not self.use_macd_filter) | (pl.col("hist") > 0)) & \
                    (pl.lit(not self.use_rsi_filter) | (pl.col("rsi") > self.rsi_long_th))
        
        short_cond = is_squeezed & (pl.col("close") < pl.col("lower")) & \
                     (pl.lit(not self.use_macd_filter) | (pl.col("hist") < 0)) & \
                     (pl.lit(not self.use_rsi_filter) | (pl.col("rsi") < self.rsi_short_th))

        sl_prices_expr = (pl.when(long_cond).then(pl.col("close") - pl.col("atr") * self.sl_atr_multiplier)
                           .when(short_cond).then(pl.col("close") + pl.col("atr") * self.sl_atr_multiplier)
                           .otherwise(None))

        tp_prices_expr = (pl.when(long_cond).then(pl.col("close") + pl.col("atr") * self.tp_atr_multiplier)
                           .when(short_cond).then(pl.col("close") - pl.col("atr") * self.tp_atr_multiplier)
                           .otherwise(None))

        return df_with_features.with_columns(
            entries=long_cond.fill_null(False),
            short_entries=short_cond.fill_null(False),
            sl_price=sl_prices_expr,
            tp_price=tp_prices_expr
        )

    async def generate_signal(self, df: pl.DataFrame) -> pl.DataFrame:
        """指標計算済みのDataFrameを受け取り、シグナル評価を実行する。"""
        return self._evaluate_signals(df)