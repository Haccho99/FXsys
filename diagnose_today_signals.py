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

async def diagnose_all_pairs():
    print("=== TODAY (2026-08-03) SIGNAL & MARKET DIAGNOSTIC ===")
    
    broker = VirtualBroker(initial_balance=1000000.0)
    guard = GuardCenter()
    executor = VirtualExecutorAdapter(broker)
    signal_gen = SignalGenerator(executor=executor)
    
    symbols = cfg.get_sync("trading.symbols", ["USD_JPY"])
    time_filters = cfg.get_sync("system.time_filters", {})
    active_windows = cfg.get_sync("system.active_windows", {})
    enabled_strategies = cfg.get_sync("trading.enabled_strategies", {})
    
    print(f"Enabled Strategies: {enabled_strategies}")
    print(f"Time Filters: {time_filters}")
    print("\n--- Current Pair Status Check ---")
    
    now_jst = datetime.now(timezone.utc).astimezone(pytz.timezone('Asia/Tokyo'))
    print(f"Current JST Time: {now_jst.strftime('%Y-%m-%d %H:%M:%S')}")
    
    for pair in symbols:
        print(f"\n==================== [{pair}] ====================")
        # Check spread
        spread_pips = await get_spread(pair)
        spread_threshold = cfg.get_sync("risk_management.volatility_filter.spread_threshold_pips", 3.0)
        print(f"Current Spread: {spread_pips:.2f} pips (Threshold: {spread_threshold:.2f})")
        
        # Check active windows
        windows = active_windows.get(pair, [])
        print(f"Active Windows: {windows}")
        
        # Fetch M15 candles
        df_m15 = await fetch_candles(pair, gran="M15", count=300)
        if df_m15 is None or df_m15.is_empty():
            print(f"ERROR: Could not fetch M15 candles for {pair}")
            continue
            
        wfa_trend = signal_gen._get_latest_wfa_params(pair, "trend")
        print(f"WFA Trend Params: {wfa_trend}")
        
        df_ind = await add_all_indicators(df_m15, wfa_trend)
        df_pd = df_ind.to_pandas()
        
        # Convert time to JST
        if 'time' in df_pd.columns:
            df_pd['time_jst'] = pd.to_datetime(df_pd['time'], utc=True).dt.tz_convert('Asia/Tokyo')
        elif 'timestamp' in df_pd.columns:
            df_pd['time_jst'] = pd.to_datetime(df_pd['timestamp'], utc=True).dt.tz_convert('Asia/Tokyo')
            
        # Filter today's candles (2026-08-03)
        today_date = now_jst.date()
        today_df = df_pd[df_pd['time_jst'].dt.date == today_date].copy()
        
        print(f"Today's M15 candles count: {len(today_df)}")
        
        # Trace each bar today
        print("\nToday's Bar by Bar Evaluation:")
        cols_to_show = ['time_jst', 'close', 'close_h1', 'ema_fast_h1', 'ema_slow_h1', 'adx_m15', 'hist_m15', 'hist_m15_prev', 'rsi_m15']
        existing_cols = [c for c in cols_to_show if c in today_df.columns]
        
        signals_today = []
        for idx, row in today_df.iterrows():
            bar_time = row['time_jst']
            bar_time_t = bar_time.time()
            bar_str = bar_time.strftime('%H:%M')
            
            # Check 1: Forbidden hours (01:00 - 09:00)
            f_start = datetime.strptime(time_filters.get("forbidden_start", "01:00"), "%H:%M").time()
            f_end = datetime.strptime(time_filters.get("forbidden_end", "09:00"), "%H:%M").time()
            is_forbidden = False
            if f_start <= f_end:
                if f_start <= bar_time_t <= f_end: is_forbidden = True
            else:
                if bar_time_t >= f_start or bar_time_t <= f_end: is_forbidden = True
                
            # Check 2: Active windows
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
            
            # Check 3: Indicator conditions
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
            
            rejection_reasons = []
            if is_forbidden: rejection_reasons.append("Forbidden Time (01:00-09:00)")
            if not in_window: rejection_reasons.append(f"Outside Active Window")
            if not adx_ok: rejection_reasons.append(f"ADX low ({adx:.1f} < {adx_th})")
            if not (h1_long or h1_short): rejection_reasons.append(f"H1 No Trend (C={c_h1:.3f}, EMA_F={ef_h1:.3f}, EMA_S={es_h1:.3f})")
            elif h1_long and not m15_long: rejection_reasons.append(f"M15 Hist not expanding for Long (hist={h_curr:.4f} <= prev={h_prev:.4f})")
            elif h1_short and not m15_short: rejection_reasons.append(f"M15 Hist not expanding for Short (hist={h_curr:.4f} >= prev={h_prev:.4f})")
            if sig and score < 0.55: rejection_reasons.append(f"Score below 0.55 ({score:.2f})")
            
            status = f"✅ SIGNAL {sig} (Score {score:.2f})" if (sig and score >= 0.55 and in_window and not is_forbidden) else f"❌ {', '.join(rejection_reasons)}"
            
            print(f"[{bar_str}] Close:{row['close']:.3f} | ADX:{adx:4.1f} | H1_Long:{str(h1_long):5s} H1_Short:{str(h1_short):5s} | M15_L:{str(m15_long):5s} M15_S:{str(m15_short):5s} | Win:{str(in_window):5s} | {status}")
            
            if sig and score >= 0.55 and in_window and not is_forbidden:
                signals_today.append((bar_str, pair, sig, score))
                
        print(f"\n--> Total Valid Entry Signals Today for {pair}: {len(signals_today)}")
        for s in signals_today:
            print(f"    {s}")

if __name__ == "__main__":
    asyncio.run(diagnose_all_pairs())
