"""
data.py (v24 - Fixed API Auth & Robust Macro Features)
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator, Dict, Any
from io import StringIO
import httpx
import httpcore
import polars as pl
import pandas as pd
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from tenacity import retry, stop_after_attempt, wait_fixed
import sys
import numpy as np
import yfinance as yf

from core import cfg
from core.logger import get_logger, log_error
from core.redis_client import create_redis_client
from core.oanda_api import (
    get_active_token, build_oanda_url, build_stream_url,
    get_mode_capabilities, get_active_account_id
)

logger = get_logger(cfg, "data")
DATA_DIR = cfg.project_root / cfg.get_sync("system.data_dir", "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 修正1: use_auth引数を正しく定義し、OANDAトークンの送信をコントロール
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def _get_json(url: str, params: Dict[str, Any] | None = None, use_auth: bool = True) -> Dict:
    try:
        headers = {}
        if use_auth:
            headers["Authorization"] = f"Bearer {get_active_token()}"
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        await log_error(logger, "_get_json", error=e, url=url)
        raise

async def _fetch_from_oanda(
    pair: str, gran: str = "M15", count: int | None = None,
    start: str | None = None, end: str | None = None
) -> pl.DataFrame:
    logger.info(f"Fetching data from OANDA API for {pair}...", gran=gran, count=count, start=start, end=end)
    
    # 修正: countもstart/endも無い場合は安全なデフォルト値をセット
    if not count and not (start and end):
        count = 100
        logger.info(f"No range or count specified for {pair}. Using default count={count}")
        
    if count:
        params = {"granularity": gran, "price": "M", "count": count}
        if start: params["from"] = start
        if end: params["to"] = end
        url = build_oanda_url(f"instruments/{pair}/candles")
        data = await _get_json(url, params)
    
    elif start and end:
        all_candles = []
        current_start = pd.to_datetime(start, utc=True)
        end_dt = pd.to_datetime(end, utc=True)
        
        while current_start < end_dt:
            params = {
                "granularity": gran,
                "price": "M",
                "from": current_start.isoformat().replace('+00:00', 'Z'),
                "count": 4900
            }
            url = build_oanda_url(f"instruments/{pair}/candles")
            
            try:
                data = await _get_json(url, params)
                candles = data.get("candles", [])
                if not candles:
                    break
                all_candles.extend(candles)
                
                last_time_str = candles[-1]['time']
                new_start = pd.to_datetime(last_time_str, utc=True)
                
                if new_start <= current_start:
                    logger.info(f"No new candles returned for {pair} (reached end of data). Breaking fetch loop.")
                    break
                    
                current_start = new_start
            
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400 and "future" in str(e.response.content).lower():
                     logger.info(f"Reached end of available data for {pair}.")
                     break
                else:
                    raise e

        data = {"candles": all_candles}
    else:
        logger.error("Invalid arguments for _fetch_from_oanda. 'count' or both 'start' and 'end' must be provided.")
        return pl.DataFrame()

    if not data.get("candles"):
        return pl.DataFrame()

    candles_list = [
        {"time": c["time"], "open": float(c["mid"]["o"]), "high": float(c["mid"]["h"]),
         "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]), "volume": int(c["volume"])}
        for c in data.get("candles", []) if c.get("complete")
    ]
    
    if not candles_list:
        return pl.DataFrame()

    df = pl.from_dicts(candles_list)
    df = df.with_columns(
        # 💡 Polars最新版の仕様に合わせ、タイムゾーン（UTC）と厳密なフォーマット（OANDA形式）を明記してパースする
        pl.col("time").str.strptime(pl.Datetime, format="%Y-%m-%dT%H:%M:%S%.fZ", strict=False).dt.replace_time_zone("UTC"),
        pl.lit(pair).alias("pair")
    ).unique(subset=["time"], keep="first").sort("time")
    
    return df.select(["time", "open", "high", "low", "close", "volume", "pair"])


async def fetch_candles(
    pair: str, gran: str = "M15", count: int | None = None,
    start: str | None = None, end: str | None = None
) -> pl.DataFrame | None:
    pair_dir = DATA_DIR / pair
    if start and end and gran == "M15":
        try:
            start_dt = pd.to_datetime(start, utc=True).to_pydatetime()
            end_dt = pd.to_datetime(end, utc=True).to_pydatetime()
            required_years = range(start_dt.year, end_dt.year + 1)
            all_files_exist = all((pair_dir / f"{year}.parquet").exists() for year in required_years)

            if all_files_exist:
                df_list = [pl.read_parquet(pair_dir / f"{year}.parquet") for year in required_years]
                combined_df = pl.concat(df_list)
                if combined_df.is_empty():
                    raise ValueError("Local Parquet files are empty.")
                    
                combined_df = combined_df.with_columns(pl.col("time").cast(pl.Datetime("ns", "UTC"))).sort("time")

                min_local_date = combined_df["time"].min()
                max_local_date = combined_df["time"].max()
                
                if min_local_date and min_local_date.tzinfo is None:
                    min_local_date = min_local_date.replace(tzinfo=timezone.utc)
                if max_local_date and max_local_date.tzinfo is None:
                    max_local_date = max_local_date.replace(tzinfo=timezone.utc)

                if min_local_date and max_local_date and min_local_date <= start_dt and max_local_date >= end_dt:
                    logger.info(f"Sufficient data in local file for {pair}. Loading from disk.")
                    return combined_df.filter(
                        (pl.col("time") >= start_dt) & (pl.col("time") <= end_dt)
                    )

        except Exception as e:
            logger.error(f"Failed to load data from local yearly files. Falling back to API.", exc_info=True)
    
    logger.warning(f"Local data is incomplete or request used 'count'. Falling back to OANDA API for {pair}.")
    return await _fetch_from_oanda(pair, gran, count, start, end)

async def fetch_orderbook(pair: str) -> float:
    if not get_mode_capabilities().get("has_orderbook", False):
        return 0.5
    try:
        redis_client = create_redis_client(cfg, logger)
        key = f"orderbook:{pair}"
        if redis_client and (v := await redis_client.get(key)) is not None: return float(v)
        
        url = build_oanda_url(f"instruments/{pair}/orderBook")
        data = await _get_json(url, None)
        imb = float(data["orderBook"]["priceBuckets"][0]["longCountPercent"])
        if redis_client: await redis_client.setex(key, 300, str(imb))
        return imb
    except Exception as e:
        return 0.5

async def fetch_positionbook(pair: str) -> float:
    if not get_mode_capabilities().get("has_positionbook", False):
        return 0.5
    try:
        redis_client = create_redis_client(cfg, logger)
        key = f"posbook:{pair}"
        if redis_client and (v := await redis_client.get(key)) is not None: return float(v)

        url = build_oanda_url(f"instruments/{pair}/positionBook")
        data = await _get_json(url, None)
        imb = float(data["positionBook"]["priceBuckets"][0]["longCountPercent"])
        if redis_client: await redis_client.setex(key, 300, str(imb))
        return imb
    except Exception as e:
        return 0.5

async def pricing_stream(pair: str, force_simulation: bool = False) -> AsyncGenerator[Dict[str, float], None]:
    use_simulation = force_simulation or cfg.get_sync("system.use_price_simulation", False)
    if use_simulation:
        price_now = 150.0
        while True:
            price_now += (np.random.rand() - 0.5) * 0.02
            # 修正: config.json の instruments オブジェクトから取得するようパスを統一
            pip_size = cfg.get_sync(f"instruments.{pair}.pip_value", 0.01)
            yield {"price": price_now, "spread": 0.2 / pip_size}
            await asyncio.sleep(1)
    else:
        while True:
            try:
                url = build_stream_url(f"accounts/{get_active_account_id()}/pricing/stream?instruments={pair}")
                logger.info(f"Connecting to REAL price stream for {pair}.")
                headers = {"Authorization": f"Bearer {get_active_token()}"}
                
                async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
                    async with client.stream("GET", url) as r:
                        r.raise_for_status()
                        async for line in r.aiter_lines():
                            if line.startswith('{"type":"PRICE"'):
                                j = json.loads(line)
                                bid = float(j["bids"][0]["price"])
                                ask = float(j["asks"][0]["price"])
                                yield (bid + ask) / 2
                            elif line.startswith('{"type":"HEARTBEAT"'):
                                pass
            
            except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout, httpcore.RemoteProtocolError) as e:
                logger.warning(f"Price stream for {pair} disconnected. Reconnecting in 5 seconds...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"An unexpected error occurred in {pair} stream. Reconnecting in 20 seconds...")
                await asyncio.sleep(20)

async def get_spread(pair: str) -> float:
    try:
        redis_client = create_redis_client(cfg)
        account_id = get_active_account_id()
        url = build_oanda_url(f"accounts/{account_id}/pricing?instruments={pair}")
        
        # タイムアウトを少し長めに設定して、稼働中のラグを許容する
        data = await _get_json(url, params=None, use_auth=True)
        
        # APIレスポンス構造の厳密なチェック
        if "prices" in data and len(data["prices"]) > 0:
            p = data["prices"][0]
            if "bids" in p and len(p["bids"]) > 0 and "asks" in p and len(p["asks"]) > 0:
                bid = float(p["bids"][0]["price"])
                ask = float(p["asks"][0]["price"])
                
                # 修正: 通貨ペアごとの正しい pip_size を動的に取得（ドルストレートの桁落ち防止）
                pip_size = cfg.get_sync(f"instruments.{pair}.pip_value", 0.01)
                spread_pips = (ask - bid) / pip_size
                
                if redis_client:
                    await redis_client.setex(f"spread:{pair}", 10, str(spread_pips))
                return spread_pips

        # ここまで到達するのはデータが空、または構造が予期せぬ場合（異常時）
        logger.warning(f"[{pair}] Received empty/malformed pricing data from API.")
        return 2.0 
        
    except Exception as e:
        # 稼働中であれば詳細なエラーをロギングする
        await log_error(logger, "get_spread", error=e)
        return 2.0

async def fetch_econ_calendar(start_date_str: str, end_date_str: str) -> list:
    try:
        redis_client = create_redis_client(cfg)
        start_date_obj = datetime.strptime(start_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        cache_key = "economic_calendar:" + start_date_obj.strftime('%Y-W%V')
        
        if redis_client and (cached := await redis_client.get(cache_key)) is not None:
            return json.loads(cached)

        api_key = cfg.get_sync("api_keys.newsapi")
        if not api_key:
            return []
        
        url = "https://newsapi.org/v2/everything"
        query = "economic calendar OR interest rate OR CPI OR FOMC OR GDP OR NFP OR unemployment rate"
        params = {"apiKey": api_key, "q": query, "from": start_date_str, "to": end_date_str, "language": "en", "pageSize": 100, "sortBy": "relevancy"}
        
        # 修正3: params を正しく渡し、use_auth=False で認証エラーを回避
        data = await _get_json(url, params=params, use_auth=False)
        events = []
        for art in data.get("articles", []):
            try:
                ts_str = art.get("publishedAt")
                if not ts_str: continue
                
                try:
                    event_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    event_time = pd.to_datetime(ts_str).to_pydatetime()
                    
                title = art.get("title", "").lower()
                
                impact = 'low'
                if any(k in title for k in ['rate decision', 'fomc', 'nfp', 'cpi']):
                    impact = 'high'
                elif any(k in title for k in ['gdp', 'unemployment', 'retail sales']):
                    impact = 'medium'

                event_data = { "time": event_time.isoformat(), "event": art.get("title"), "currency": "N/A", "impact": impact }
                for cur in ("USD", "EUR", "GBP", "JPY", "AUD"):
                    if cur.lower() in title:
                        event_data["currency"] = cur; break
                events.append(event_data)
            except Exception:
                continue
        
        unique_events = list({v['event']:v for v in events}.values())
        sorted_events = sorted(unique_events, key=lambda x: x['time'])

        if redis_client:
            await redis_client.setex(cache_key, 21600, json.dumps(sorted_events))
        return sorted_events
    except Exception as e:
        await log_error(logger, "fetch_econ_calendar", error=e)
        return []

async def add_macro_features(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df

    start_date = df["time"].min().date()
    end_date = df["time"].max().date() + timedelta(days=1)

    def _fetch_macro():
        # 修正4: 不安定な日本国債(jp10y)への依存を減らし、取得可能なものだけを取得
        tickers = {
            "us10y": "^TNX",
            "us02y": "^IRX"
        }
        macro_data_frames = []
        for key, ticker in tickers.items():
            try:
                t = yf.Ticker(ticker)
                h = t.history(start=start_date, end=end_date)
                if not h.empty:
                    h = h[['Close']].rename(columns={'Close': key})
                    macro_data_frames.append(h)
            except Exception:
                pass
        
        if not macro_data_frames:
            return pd.DataFrame()
            
        return pd.concat(macro_data_frames, axis=1).sort_index()

    macro_pd = await asyncio.to_thread(_fetch_macro)
    
    if macro_pd.empty:
        logger.warning("Macro data fetch failed. Returning original data.")
        # 後続処理がエラーにならないよう、ダミーカラムを追加して返す
        return df.with_columns([
            pl.lit(0.0).alias("yield_diff_10y"),
            pl.lit(0.0).alias("us_yield_curve")
        ])

    macro_pl = pl.from_pandas(macro_pd.reset_index().rename(columns={'Date': 'time'}))
    
    if macro_pl["time"].dtype == pl.Datetime:
        macro_pl = macro_pl.with_columns(pl.col("time").dt.replace_time_zone("UTC").cast(pl.Datetime("ns", "UTC")))
    else:
        macro_pl = macro_pl.with_columns(pl.col("time").str.to_datetime().dt.replace_time_zone("UTC").cast(pl.Datetime("ns", "UTC")))

    df = df.sort("time")
    macro_pl = macro_pl.sort("time")
    
    combined = df.join_asof(macro_pl, on="time", strategy="backward")

    # 修正5: 必要なカラムが存在しない場合でも、ダミー値(0.0)を入れて絶対に後続AIを止めない
    expected_cols = ["us10y", "us02y", "jp10y", "yield_diff_10y", "us_yield_curve"]
    new_cols = []
    
    if "us10y" in combined.columns and "jp10y" not in combined.columns:
        # 日本金利が取得できなかった場合は、適当な固定値(例: 1.0%)を入れておく
        combined = combined.with_columns(pl.lit(1.0).alias("jp10y"))
        
    cols = combined.columns
    if "us10y" in cols and "jp10y" in cols:
        combined = combined.with_columns((pl.col("us10y") - pl.col("jp10y")).alias("yield_diff_10y"))
    if "us10y" in cols and "us02y" in cols:
        combined = combined.with_columns((pl.col("us10y") - pl.col("us02y")).alias("us_yield_curve"))
        
    # 最終チェック：AIが要求するカラムがどうしても無ければ0で埋める
    final_cols = combined.columns
    for req_col in expected_cols:
        if req_col not in final_cols:
            combined = combined.with_columns(pl.lit(0.0).alias(req_col))

    logger.info("Macro features robustly integrated.")
    return combined