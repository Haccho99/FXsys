# core/indicators.py (v36 - Pure Pandas / No external TA library)
import asyncio, json, hashlib, sys
from pathlib import Path
import polars as pl
import pandas as pd
import numpy as np
from typing import Any, Dict, Callable, Coroutine, Optional
from functools import wraps

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path: sys.path.append(str(project_root))

from core.config_manager import ConfigManager
cfg = ConfigManager(project_root)
from core.logger import get_logger, log_error
from core.data import fetch_candles, add_macro_features
from core.redis_client import create_redis_client

logger = get_logger(cfg, "indicators")

# ==========================================
# 純粋なPandasによるテクニカル計算エンジン (B案)
# ==========================================
def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def _rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False).mean()

def _adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int):
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1/length, adjust=False).mean()
    
    plus_di = (pd.Series(plus_dm, index=high.index).ewm(alpha=1/length, adjust=False).mean() / atr_) * 100
    minus_di = (pd.Series(minus_dm, index=high.index).ewm(alpha=1/length, adjust=False).mean() / atr_) * 100
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.ewm(alpha=1/length, adjust=False).mean()
    return adx, plus_di, minus_di

def _bbands(close: pd.Series, length: int, std: float):
    ma = close.rolling(length).mean()
    stdev = close.rolling(length).std()
    upper = ma + std * stdev
    lower = ma - std * stdev
    width = (upper - lower) / ma * 100
    percent = (close - lower) / (upper - lower)
    return lower, ma, upper, width, percent

def _macd(close: pd.Series, fast: int, slow: int, signal: int):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

# ==========================================
# キャッシュ & Asyncラッパー (運用者様の既存ロジック)
# ==========================================
def _convert_to_json_serializable(obj: Any) -> Any:
    if isinstance(obj, np.ndarray): return obj.tolist()
    if isinstance(obj, dict): return {k: _convert_to_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (pl.Series, pl.DataFrame)): raise TypeError("Polars objects should not be part of the cache payload.")
    return obj

def cache_indicator_async(func: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any | None:
        redis_client = await create_redis_client(cfg)
        if not redis_client: return await func(*args, **kwargs)
        key_parts = [func.__name__]
        all_args = kwargs.copy(); arg_names = func.__code__.co_varnames[:func.__code__.co_argcount]
        for i, arg in enumerate(args): all_args[arg_names[i]] = arg
        for k, v in sorted(all_args.items()):
            if isinstance(v, (pl.Series, pl.DataFrame)):
                hasher = hashlib.md5()
                # copy=False を外し、安全にバイト列へ変換する
                hasher.update(v.to_pandas().to_numpy().tobytes())
                key_parts.append(f"{k}:{hasher.hexdigest()}")
            else: 
                key_parts.append(f"{k}:{v}")
        key = ":".join(key_parts)
        try:
            cached_result_json = await redis_client.get(key)
            if cached_result_json:
                cached_data = json.loads(cached_result_json)
                if isinstance(cached_data, dict): return {k: np.array(v) for k, v in cached_data.items()}
                if isinstance(cached_data, list): return np.array(cached_data)
                return cached_data
            result = await func(*args, **kwargs)
            if result is not None:
                payload = json.dumps(_convert_to_json_serializable(result)); await redis_client.setex(key, 3600, payload)
            return result
        except Exception as e:
            await log_error(logger, f"cache_decorator:{func.__name__}", error=e)
            return await func(*args, **kwargs)
        finally:
            await redis_client.close()
    return wrapper

@cache_indicator_async
async def calculate_ema(data: pl.Series, span: int) -> np.ndarray | None:
    if data.is_empty() or len(data) < span: return None
    return _ema(data.to_pandas(), span).to_numpy()

@cache_indicator_async
async def calculate_atr(high: pl.Series, low: pl.Series, close: pl.Series, period: int = 14) -> np.ndarray | None:
    if high.is_empty() or len(high) < period: return None
    return _atr(high.to_pandas(), low.to_pandas(), close.to_pandas(), period).to_numpy()

@cache_indicator_async
async def calculate_rsi(data: pl.Series, period: int = 14) -> np.ndarray | None:
    if data.is_empty() or len(data) < period: return None
    return _rsi(data.to_pandas(), period).to_numpy()

@cache_indicator_async
async def calculate_adx(high: pl.Series, low: pl.Series, close: pl.Series, period: int = 14) -> Dict[str, np.ndarray] | None:
    if high.is_empty() or len(high) < period * 2: return None
    adx_val, plus_di, minus_di = _adx(high.to_pandas(), low.to_pandas(), close.to_pandas(), period)
    return {"adx": adx_val.to_numpy(), "di_plus": plus_di.to_numpy(), "di_minus": minus_di.to_numpy()}

@cache_indicator_async
async def calculate_bbands(data: pl.Series, period: int = 20, std_dev: float = 2.0) -> Dict[str, np.ndarray] | None:
    if data.is_empty() or len(data) < period: return None
    lower, middle, upper, width, percent = _bbands(data.to_pandas(), period, std_dev)
    return {"lower": lower.to_numpy(), "middle": middle.to_numpy(), "upper": upper.to_numpy(), "width": width.to_numpy()}

@cache_indicator_async
async def calculate_macd(data: pl.Series, fast: int = 12, slow: int = 26, signal_period: int = 9) -> Dict[str, np.ndarray] | None:
    if data.is_empty() or len(data) < slow: return None
    macd_line, signal_line, hist = _macd(data.to_pandas(), fast, slow, signal_period)
    return {"macd": macd_line.to_numpy(), "signal": signal_line.to_numpy(), "hist": hist.to_numpy()}

async def add_all_indicators(df_input: pl.DataFrame, params: Optional[Dict[str, Any]] = None) -> pl.DataFrame:
    params = params or {}  # paramsがNoneの場合は空辞書にする
    df_pd = df_input.to_pandas()
    if 'time' in df_pd.columns:
        df_pd['time'] = pd.to_datetime(df_pd['time'])
        df_pd = df_pd.set_index('time')

    numeric_cols = ["open", "high", "low", "close", "volume"]
    cols_to_process = [col for col in numeric_cols if col in df_pd.columns]
    
    if not all(c in cols_to_process for c in ["open", "high", "low", "close"]):
        logger.error("OHLC columns are missing, cannot calculate indicators.")
        df_pd.reset_index(inplace=True)
        return pl.from_pandas(df_pd)

    df_calc = df_pd[cols_to_process].copy()
    
    for col in ["open", "high", "low", "close"]:
        df_calc[col] = pd.to_numeric(df_calc[col], errors='coerce')
    if "volume" in df_calc.columns:
        df_calc["volume"] = pd.to_numeric(df_calc["volume"], errors='coerce').fillna(0)

    df_calc.ffill(inplace=True)
    df_calc.bfill(inplace=True)
    
    # 時系列順にソート（リサンプリングの前提）
    df_calc.sort_index(inplace=True)

    try:
        # =========================================================
        # 1. 【ベース層】 M5（5分足）のインジケーター計算
        # =========================================================
        df_pd['atr'] = _atr(df_calc['high'], df_calc['low'], df_calc['close'], 14)
        
        # RSIやその他共通指標 (必要に応じて)
        df_pd['rsi'] = _rsi(df_calc['close'], 14)

        # =========================================================
        # 2. 【中間層】 M15（15分足）の合成とインジケーター計算 (動的パラメータ適用)
        # =========================================================
        df_m15 = df_calc.resample('15min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        # --- 既存: MACD & ADX ---
        macd_fast = params.get('macd_fast', 12)
        macd_slow = params.get('macd_slow', 26)
        macd_sig = params.get('macd_signal_period', 9)
        _, _, hist_m15 = _macd(df_m15['close'], fast=macd_fast, slow=macd_slow, signal=macd_sig)
        
        adx_m15, _, _ = _adx(df_m15['high'], df_m15['low'], df_m15['close'], length=14)
        
        # ＝＝＝ ▼ 修正: BB Squeeze用インジケーターのバリデーション強化 ▼ ＝＝＝
        # 1. RSI (M15)
        rsi_period = int(params.get('rsi_period', 14))
        rsi_m15 = _rsi(df_m15['close'], length=rsi_period)

        # 2. ボリンジャーバンド (M15)
        bb_period = int(params.get('bb_period', 20))
        bb_std = float(params.get('bb_std', 2.0))
        bb_lower_m15, bb_ma_m15, bb_upper_m15, bb_width_m15, bb_percent_m15 = _bbands(df_m15['close'], bb_period, bb_std)

        # 3. スクイーズ指数 (Squeeze Ratio) の計算を強化
        bb_squeeze_lookback = int(params.get('bb_squeeze_lookback', 50))
        rolling_max = bb_width_m15.rolling(bb_squeeze_lookback).max()
        rolling_min = bb_width_m15.rolling(bb_squeeze_lookback).min()
        
        # 分母の差分が極端に小さい場合を考慮し、微小なepsilonを加えることでゼロ除算とNaNを回避
        diff = rolling_max - rolling_min
        bb_squeeze_ratio_m15 = (rolling_max - bb_width_m15) / diff.replace(0, np.nan)
        bb_squeeze_ratio_m15 = bb_squeeze_ratio_m15.fillna(0.0).clip(0.0, 1.0)
        # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

        # --- ffillでM5の行数に合わせる (マージ処理) ---
        # 既存
        df_pd['hist_m15'] = hist_m15.reindex(df_calc.index, method='ffill')
        df_pd['hist_m15_prev'] = hist_m15.shift(1).reindex(df_calc.index, method='ffill')
        df_pd['adx_m15'] = adx_m15.reindex(df_calc.index, method='ffill')
        
        # 新規 (BB Squeeze用)
        df_pd['rsi_m15'] = rsi_m15.reindex(df_calc.index, method='ffill')
        df_pd['bb_upper_m15'] = bb_upper_m15.reindex(df_calc.index, method='ffill')
        df_pd['bb_lower_m15'] = bb_lower_m15.reindex(df_calc.index, method='ffill')
        df_pd['bb_width_m15'] = bb_width_m15.reindex(df_calc.index, method='ffill')
        df_pd['bb_squeeze_ratio_m15'] = bb_squeeze_ratio_m15.reindex(df_calc.index, method='ffill')

        # =========================================================
        # 3. 【環境層】 H1（1時間足）の合成とインジケーター計算 (動的パラメータ適用)
        # =========================================================
        df_h1 = df_calc.resample('1h').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
        }).dropna()
        
        # WFAから渡されたパラメータでEMAを計算
        ema_fast_span = params.get('ema_fast_span', 50)
        ema_slow_span = params.get('ema_slow_span', 200)
        ema_fast_h1 = _ema(df_h1['close'], ema_fast_span)
        ema_slow_h1 = _ema(df_h1['close'], ema_slow_span)
        
        # 🛡️【修正：未来参照（Look-ahead Bias）の完全排除】
        # M15各足の判定時点で未来のH1終値をカンニングしないよう、
        # 「完全に確定した前1時間足（shift(1)）」のみをffillで展開する
        df_pd['close_h1'] = df_h1['close'].shift(1).reindex(df_calc.index, method='ffill')
        df_pd['ema_fast_h1'] = ema_fast_h1.shift(1).reindex(df_calc.index, method='ffill')
        df_pd['ema_slow_h1'] = ema_slow_h1.shift(1).reindex(df_calc.index, method='ffill')

    except Exception as e:
        logger.error(f"Failed to calculate MTF indicators: {e}", exc_info=True)

    df_pd.reset_index(inplace=True)
    df_pl = pl.from_pandas(df_pd)
    
    # Polarsのjoin_asofを通すための型統一
    if "time" in df_pl.columns:
        try:
            if df_pl["time"].dtype == pl.Utf8:
                df_pl = df_pl.with_columns(pl.col("time").str.to_datetime())
                
            tz = getattr(df_pl["time"].dtype, "time_zone", None)
            if tz is None:
                df_pl = df_pl.with_columns(pl.col("time").dt.replace_time_zone("UTC"))
            elif tz != "UTC":
                df_pl = df_pl.with_columns(pl.col("time").dt.convert_time_zone("UTC"))
                
            df_pl = df_pl.with_columns(pl.col("time").cast(pl.Datetime("ns", "UTC")))
        except Exception as e:
            logger.error(f"Time column formatting failed: {e}")

    try:
        df_pl = await add_macro_features(df_pl)
    except Exception as e:
        logger.error("Failed to add macro features (join error).", exc_info=True)
        for col in ["us10y", "us02y", "jp10y", "yield_diff_10y", "us_yield_curve"]:
            if col not in df_pl.columns:
                df_pl = df_pl.with_columns(pl.lit(0.0).alias(col))
                
    return df_pl

async def generate_strategy_signals(df_input: pl.DataFrame, strategy_type: str, params: Dict[str, Any]) -> pl.DataFrame:
    if strategy_type == "trend":
        if 'ema_fast_span' in params and 'ema_slow_span' in params:
            try:
                ema_fast_pd = _ema(df_input["close"].to_pandas(), params['ema_fast_span'])
                ema_slow_pd = _ema(df_input["close"].to_pandas(), params['ema_slow_span'])
                df = df_input.with_columns(
                    ema_short_dynamic=pl.from_pandas(ema_fast_pd),
                    ema_long_dynamic=pl.from_pandas(ema_slow_pd)
                )
                ema_short_col, ema_long_col = "ema_short_dynamic", "ema_long_dynamic"
            except Exception:
                df = df_input
                ema_short_col, ema_long_col = "ema_short", "ema_long"
        else:
            df = df_input
            ema_short_col, ema_long_col = "ema_short", "ema_long"

        required_cols = [ema_short_col, ema_long_col, "atr", "close"]
        if not all(col in df.columns for col in required_cols):
            return df.with_columns(
                entries=pl.lit(False), short_entries=pl.lit(False),
                signal_exits=pl.lit(False), short_signal_exits=pl.lit(False),
                sl_price=pl.lit(None, dtype=pl.Float64),
                tp_price=pl.lit(None, dtype=pl.Float64),
            )

        sl_mult = params.get("sl_atr_multiplier", 2.0)
        tp_mult = params.get("tp_atr_multiplier", 1.5)

        cross_state = (
            pl.when(pl.col(ema_short_col) > pl.col(ema_long_col)).then(pl.lit(1))
            .when(pl.col(ema_short_col) < pl.col(ema_long_col)).then(pl.lit(-1))
            .otherwise(pl.lit(0))
        )
        
        prev_state = cross_state.shift(1).fill_null(0)
        
        long_entries = (prev_state != 1) & (cross_state == 1)
        short_entries = (prev_state != -1) & (cross_state == -1)
        long_exits = (prev_state == 1) & (cross_state != 1)
        short_exits = (prev_state == -1) & (cross_state != -1)

        sl_price_expr = (
            pl.when(long_entries).then(pl.col("close") - pl.col("atr") * sl_mult)
            .when(short_entries).then(pl.col("close") + pl.col("atr") * sl_mult)
            .otherwise(None)
        )
        tp_price_expr = (
            pl.when(long_entries).then(pl.col("close") + pl.col("atr") * tp_mult)
            .when(short_entries).then(pl.col("close") - pl.col("atr") * tp_mult)
            .otherwise(None)
        )
        
        return df.with_columns(
            entries=long_entries.fill_null(False),
            short_entries=short_entries.fill_null(False),
            signal_exits=long_exits.fill_null(False),
            short_signal_exits=short_exits.fill_null(False),
            sl_price=sl_price_expr,
            tp_price=tp_price_expr,
        )
    return df_input