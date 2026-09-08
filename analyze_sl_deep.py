import glob, pandas as pd, numpy as np

files = glob.glob('logs/virtual_trade_log_*.csv')
df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df['is_sl'] = df['reason'] == 'SL'
df['is_win'] = df['profit_amount'] > 0

# --- 1. SL vs Non-SL の基本比較 ---
print("=" * 70)
print("1. SL vs Non-SL BASIC COMPARISON")
print("=" * 70)
sl_df = df[df['is_sl']]
non_sl_df = df[~df['is_sl']]
print(f"SL Trades:     {len(sl_df):4d} ({len(sl_df)/len(df)*100:.1f}%) | Avg Loss: {sl_df['profit_amount'].mean():,.0f} JPY | Total: {sl_df['profit_amount'].sum():,.0f} JPY")
print(f"Non-SL Trades: {len(non_sl_df):4d} ({len(non_sl_df)/len(df)*100:.1f}%) | Avg PnL:  {non_sl_df['profit_amount'].mean():,.0f} JPY | Total: {non_sl_df['profit_amount'].sum():,.0f} JPY")

# --- 2. SLの通貨ペア別内訳 ---
print("\n" + "=" * 70)
print("2. SL BREAKDOWN BY PAIR")
print("=" * 70)
for pair, group in df.groupby('pair'):
    sl_count = group['is_sl'].sum()
    total = len(group)
    sl_pnl = group[group['is_sl']]['profit_amount'].sum()
    sl_avg = group[group['is_sl']]['profit_amount'].mean() if sl_count > 0 else 0
    print(f"{pair:10s} | SL: {sl_count:3d}/{total:3d} ({sl_count/total*100:.1f}%) | SL Total: {sl_pnl:,.0f} JPY | SL Avg: {sl_avg:,.0f} JPY")

# --- 3. SLの戦略別内訳 ---
print("\n" + "=" * 70)
print("3. SL BREAKDOWN BY STRATEGY")
print("=" * 70)
for strat, group in df.groupby('strategy'):
    sl_count = group['is_sl'].sum()
    total = len(group)
    sl_pnl = group[group['is_sl']]['profit_amount'].sum()
    sl_avg = group[group['is_sl']]['profit_amount'].mean() if sl_count > 0 else 0
    print(f"{strat:10s} | SL: {sl_count:3d}/{total:3d} ({sl_count/total*100:.1f}%) | SL Total: {sl_pnl:,.0f} JPY | SL Avg: {sl_avg:,.0f} JPY")

# --- 4. SLのAIスコア別内訳 ---
print("\n" + "=" * 70)
print("4. SL BREAKDOWN BY AI SCORE")
print("=" * 70)
if 'ai_score' in df.columns:
    df['score_bin'] = pd.cut(df['ai_score'], bins=[0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
                             labels=['0.0-0.5', '0.5-0.6', '0.6-0.7', '0.7-0.8', '0.8-0.9', '0.9-1.0'])
    for bin_label, group in df.groupby('score_bin', observed=False):
        if len(group) == 0: continue
        sl_count = group['is_sl'].sum()
        total = len(group)
        sl_pnl = group[group['is_sl']]['profit_amount'].sum()
        sl_avg = group[group['is_sl']]['profit_amount'].mean() if sl_count > 0 else 0
        print(f"Score {bin_label:7s} | SL: {sl_count:3d}/{total:3d} ({sl_count/total*100:.1f}%) | SL Total: {sl_pnl:,.0f} JPY | SL Avg: {sl_avg:,.0f} JPY")

# --- 5. SLのロットサイズ別内訳 ---
print("\n" + "=" * 70)
print("5. SL BREAKDOWN BY LOT SIZE (Units)")
print("=" * 70)
if 'lot_size' in df.columns:
    df['lot_bin'] = pd.cut(df['lot_size'], bins=[0, 10000, 15000, 20000, 25000, 30000],
                           labels=['<10K', '10K-15K', '15K-20K', '20K-25K', '25K+'])
    for bin_label, group in df.groupby('lot_bin', observed=False):
        if len(group) == 0: continue
        sl_count = group['is_sl'].sum()
        total = len(group)
        sl_pnl = group[group['is_sl']]['profit_amount'].sum()
        sl_avg = group[group['is_sl']]['profit_amount'].mean() if sl_count > 0 else 0
        print(f"Lot {bin_label:8s} | SL: {sl_count:3d}/{total:3d} ({sl_count/total*100:.1f}%) | SL Total: {sl_pnl:,.0f} JPY | SL Avg: {sl_avg:,.0f} JPY")

# --- 6. SL連敗パターン分析 ---
print("\n" + "=" * 70)
print("6. SL CONSECUTIVE LOSS STREAK ANALYSIS")
print("=" * 70)
for pair in df['pair'].unique():
    pair_df = df[df['pair'] == pair].sort_values('timestamp').reset_index(drop=True)
    streaks = []
    current_streak = 0
    for _, row in pair_df.iterrows():
        if row['reason'] == 'SL':
            current_streak += 1
        else:
            if current_streak > 0:
                streaks.append(current_streak)
            current_streak = 0
    if current_streak > 0:
        streaks.append(current_streak)
    if streaks:
        print(f"{pair:10s} | Max Streak: {max(streaks)} | Avg Streak: {np.mean(streaks):.1f} | Streak Count: {len(streaks)}")

# --- 7. SL 1件あたりの損失額分布 ---
print("\n" + "=" * 70)
print("7. SL LOSS AMOUNT DISTRIBUTION")
print("=" * 70)
sl_losses = sl_df['profit_amount'].abs()
print(f"Min Loss:  {sl_losses.min():,.0f} JPY")
print(f"25th %ile: {sl_losses.quantile(0.25):,.0f} JPY")
print(f"Median:    {sl_losses.median():,.0f} JPY")
print(f"75th %ile: {sl_losses.quantile(0.75):,.0f} JPY")
print(f"Max Loss:  {sl_losses.max():,.0f} JPY")
print(f"Mean Loss: {sl_losses.mean():,.0f} JPY")

# --- 8. 曜日別 SL発生率 ---
print("\n" + "=" * 70)
print("8. SL RATE BY DAY OF WEEK (JST)")
print("=" * 70)
df['timestamp_dt'] = pd.to_datetime(df['timestamp'])
if df['timestamp_dt'].dt.tz is not None:
    df['jst_weekday'] = df['timestamp_dt'].dt.tz_convert('Asia/Tokyo').dt.dayofweek
else:
    df['jst_weekday'] = df['timestamp_dt'].dt.dayofweek
day_names = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
for wd in sorted(df['jst_weekday'].unique()):
    group = df[df['jst_weekday'] == wd]
    sl_count = group['is_sl'].sum()
    total = len(group)
    sl_pnl = group[group['is_sl']]['profit_amount'].sum()
    print(f"{day_names.get(wd, '?'):3s} | SL: {sl_count:3d}/{total:3d} ({sl_count/total*100:.1f}%) | SL PnL: {sl_pnl:,.0f} JPY")

# --- 9. 時間帯別 SL発生率 (JST) ---
print("\n" + "=" * 70)
print("9. SL RATE BY HOUR (JST)")
print("=" * 70)
if df['timestamp_dt'].dt.tz is not None:
    df['jst_hour'] = df['timestamp_dt'].dt.tz_convert('Asia/Tokyo').dt.hour
else:
    df['jst_hour'] = df['timestamp_dt'].dt.hour
for hour in sorted(df['jst_hour'].unique()):
    group = df[df['jst_hour'] == hour]
    sl_count = group['is_sl'].sum()
    total = len(group)
    if total < 3: continue
    sl_pnl = group[group['is_sl']]['profit_amount'].sum()
    print(f"{hour:02d}:00 JST | SL: {sl_count:3d}/{total:3d} ({sl_count/total*100:.1f}%) | SL PnL: {sl_pnl:,.0f} JPY")

# --- 10. Signal Exit (Wave Ended) の詳細分析 ---
print("\n" + "=" * 70)
print("10. SIGNAL EXIT (WAVE ENDED) ANALYSIS")
print("=" * 70)
wave_df = df[df['reason'].str.contains('Wave Ended', na=False)]
print(f"Total Wave Ended Exits: {len(wave_df)}")
print(f"Win Rate: {wave_df['is_win'].mean():.2%}")
print(f"Total PnL: {wave_df['profit_amount'].sum():,.0f} JPY")
print(f"Avg PnL:   {wave_df['profit_amount'].mean():,.0f} JPY")
for pair, group in wave_df.groupby('pair'):
    print(f"  {pair:10s} | Count: {len(group):3d} | PnL: {group['profit_amount'].sum():,.0f} JPY")
