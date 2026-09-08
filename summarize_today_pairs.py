import asyncio
import sys
import io
from datetime import datetime, timezone
import polars as pl
import pandas as pd
import pytz

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core import cfg
from core.data import fetch_candles, get_spread
from core.monitor import GuardCenter
from core.signal_module import SignalGenerator
from core.virtual_trade import VirtualBroker
from virtual_main_loop import VirtualExecutorAdapter
from core.indicators import add_all_indicators

async def summarize():
    broker = VirtualBroker(initial_balance=1000000.0)
    guard = GuardCenter()
    executor = VirtualExecutorAdapter(broker)
    signal_gen = SignalGenerator(executor=executor)
    
    symbols = cfg.get_sync("trading.symbols", ["USD_JPY"])
    time_filters = cfg.get_sync("system.time_filters", {})
    active_windows = cfg.get_sync("system.active_windows", {})
    now_jst = datetime.now(timezone.utc).astimezone(pytz.timezone('Asia/Tokyo'))
    today_date = now_jst.date()
    
    summary_results = {}
    
    for pair in symbols:
        spread_pips = await get_spread(pair)
        windows = active_windows.get(pair, [])
        df_m15 = await fetch_candles(pair, gran="M15", count=300)
        if df_m15 is None or df_m15.is_empty():
            summary_results[pair] = {"error": "Failed to fetch M15 candles"}
            continue
            
        wfa_trend = signal_gen._get_latest_wfa_params(pair, "trend")
        df_ind = await add_all_indicators(df_m15, wfa_trend)
        df_pd = df_ind.to_pandas()
        
        if 'time' in df_pd.columns:
            df_pd['time_jst'] = pd.to_datetime(df_pd['time'], utc=True).dt.tz_convert('Asia/Tokyo')
        elif 'timestamp' in df_pd.columns:
            df_pd['time_jst'] = pd.to_datetime(df_pd['timestamp'], utc=True).dt.tz_convert('Asia/Tokyo')
            
        today_df = df_pd[df_pd['time_jst'].dt.date == today_date].copy()
        
        # Analyze why each bar didn't trigger
        reasons_count = {
            "forbidden_time": 0,
            "outside_active_window": 0,
            "adx_too_low": 0,
            "h1_no_trend": 0,
            "m15_hist_mismatch": 0,
            "score_low": 0,
            "valid_signal": 0
        }
        
        hourly_summary = []
        
        for idx, row in today_df.iterrows():
            bar_time = row['time_jst']
            bar_time_t = bar_time.time()
            bar_str = bar_time.strftime('%H:%M')
            
            f_start = datetime.strptime(time_filters.get("forbidden_start", "01:00"), "%H:%M").time()
            f_end = datetime.strptime(time_filters.get("forbidden_end", "09:00"), "%H:%M").time()
            is_forbidden = False
            if f_start <= f_end:
                if f_start <= bar_time_t <= f_end: is_forbidden = True
            else:
                if bar_time_t >= f_start or bar_time_t <= f_end: is_forbidden = True
                
            in_window = False
            if not windows:
                in_window = True
            else:
                for w in windows:
                    st = datetime.strptime(w['start'], "%H:%M").time()
                    et = datetime.strptime(w['end'], "%H:%M").time()
                    if st <= et:
                        if st <= bar_time_t <= et: in_window = True; break
                    else:
                        if bar_time_t >= st or bar_time_t <= et: in_window = True; break
            
            c_h1 = row['close_h1']
            ef_h1 = row['ema_fast_h1']
            es_h1 = row['ema_slow_h1']
            adx = row['adx_m15']
            h_curr = row['hist_m15']
            h_prev = row['hist_m15_prev']
            rsi = row.get('rsi_m15', 50)
            
            h1_long = (c_h1 > ef_h1) and (ef_h1 > es_h1)
            h1_short = (c_h1 < ef_h1) and (ef_h1 < es_h1)
            m15_long = (h_curr > h_prev)
            m15_short = (h_curr < h_prev)
            
            adx_th = wfa_trend.get("adx_threshold", 20)
            adx_ok = (adx >= adx_th)
            
            sig = None
            if adx_ok:
                if h1_long and m15_long: sig = "long"
                elif h1_short and m15_short: sig = "short"
                
            score = 0.0
            if sig:
                score = 0.40
                if adx > adx_th: score += 0.15
                if adx > adx_th + 5: score += 0.10
                if sig == "long" and h1_long: score += 0.15
                elif sig == "short" and h1_short: score += 0.15
                if sig == "long" and rsi > 60: score += 0.10
                elif sig == "short" and rsi < 40: score += 0.10
                
            if is_forbidden:
                reasons_count["forbidden_time"] += 1
            elif not in_window:
                reasons_count["outside_active_window"] += 1
            elif not adx_ok:
                reasons_count["adx_too_low"] += 1
            elif not (h1_long or h1_short):
                reasons_count["h1_no_trend"] += 1
            elif (h1_long and not m15_long) or (h1_short and not m15_short):
                reasons_count["m15_hist_mismatch"] += 1
            elif score < 0.55:
                reasons_count["score_low"] += 1
            else:
                reasons_count["valid_signal"] += 1
                
        summary_results[pair] = {
            "spread": spread_pips,
            "adx_latest": today_df['adx_m15'].iloc[-1] if not today_df.empty else 0,
            "adx_max_today": today_df['adx_m15'].max() if not today_df.empty else 0,
            "adx_mean_today": today_df['adx_m15'].mean() if not today_df.empty else 0,
            "reasons": reasons_count,
            "total_bars_today": len(today_df)
        }

    print("\n" + "="*70)
    print("           TODAY'S PAIR-BY-PAIR ENTRY DIAGNOSIS")
    print("="*70)
    for pair, data in summary_results.items():
        print(f"\n--- {pair} ---")
        print(f"Spread: {data['spread']:.2f} pips | ADX Today (Mean: {data['adx_mean_today']:.1f}, Max: {data['adx_max_today']:.1f}, Latest: {data['adx_latest']:.1f})")
        r = data['reasons']
        print(f"Total M15 Bars Today: {data['total_bars_today']}")
        print(f"  - Forbidden Time (01:00-09:00) : {r['forbidden_time']} bars")
        print(f"  - Outside Active Window         : {r['outside_active_window']} bars")
        print(f"  - In Window, but ADX < 20 (レンジ) : {r['adx_too_low']} bars")
        print(f"  - In Window, ADX>=20 but H1 No Trend : {r['h1_no_trend']} bars")
        print(f"  - In Window, H1 Trend but M15 Mismatch: {r['m15_hist_mismatch']} bars")
        print(f"  - Score < 0.55                  : {r['score_low']} bars")
        print(f"  --> VALID SIGNALS GENERATED    : {r['valid_signal']} bars")

if __name__ == "__main__":
    asyncio.run(summarize())
