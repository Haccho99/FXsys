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

async def detailed_trace():
    broker = VirtualBroker(initial_balance=1000000.0)
    guard = GuardCenter()
    executor = VirtualExecutorAdapter(broker)
    signal_gen = SignalGenerator(executor=executor)
    
    symbols = ["USD_JPY", "AUD_JPY", "EUR_USD", "EUR_JPY"]
    time_filters = cfg.get_sync("system.time_filters", {})
    active_windows = cfg.get_sync("system.active_windows", {})
    now_jst = datetime.now(timezone.utc).astimezone(pytz.timezone('Asia/Tokyo'))
    today_date = now_jst.date()
    
    for pair in symbols:
        print(f"\n=======================================================")
        print(f"               DETAILED TRACE: {pair}")
        print(f"=======================================================")
        windows = active_windows.get(pair, [])
        wfa_trend = signal_gen._get_latest_wfa_params(pair, "trend")
        print(f"Active Windows: {windows}")
        print(f"WFA params: {wfa_trend}")
        
        df_m15 = await fetch_candles(pair, gran="M15", count=300)
        df_ind = await add_all_indicators(df_m15, wfa_trend)
        df_pd = df_ind.to_pandas()
        
        if 'time' in df_pd.columns:
            df_pd['time_jst'] = pd.to_datetime(df_pd['time'], utc=True).dt.tz_convert('Asia/Tokyo')
        elif 'timestamp' in df_pd.columns:
            df_pd['time_jst'] = pd.to_datetime(df_pd['timestamp'], utc=True).dt.tz_convert('Asia/Tokyo')
            
        today_df = df_pd[df_pd['time_jst'].dt.date == today_date].copy()
        
        for idx, row in today_df.iterrows():
            bar_time = row['time_jst']
            bar_time_t = bar_time.time()
            bar_str = bar_time.strftime('%H:%M')
            
            # Check window
            in_window = False
            for w in windows:
                st = datetime.strptime(w['start'], "%H:%M").time()
                et = datetime.strptime(w['end'], "%H:%M").time()
                if st <= et:
                    if st <= bar_time_t <= et: in_window = True; break
                else:
                    if bar_time_t >= st or bar_time_t <= et: in_window = True; break
            
            if not in_window and bar_time.hour >= 9:
                # Outside window during daytime
                continue
                
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
                
            print(f"[{bar_str}] InWin:{str(in_window):5s} | Close:{row['close']:8.3f} | H1(C={c_h1:.3f}, EF={ef_h1:.3f}, ES={es_h1:.3f} -> L:{h1_long}, S:{h1_short}) | M15 Hist(curr={h_curr:+.4f}, prev={h_prev:+.4f} -> L:{m15_long}, S:{m15_short}) | ADX:{adx:4.1f} | Sig:{str(sig):5s} (Score:{score:.2f})")

if __name__ == "__main__":
    asyncio.run(detailed_trace())
