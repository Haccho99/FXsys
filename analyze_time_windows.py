import pandas as pd
import glob

# Load data
files = glob.glob('logs/virtual_trade_log_*.csv')
if not files:
    print('No logs found.')
    exit()

df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df['entry_time_dt'] = pd.to_datetime(df['entry_time'])
df['entry_time_jst'] = df['entry_time_dt'].dt.tz_convert('Asia/Tokyo')
df['hour'] = df['entry_time_jst'].dt.hour
df['weekday'] = df['entry_time_jst'].dt.dayofweek # 0=Mon, 4=Fri
df['is_win'] = df['profit_amount'] > 0

# We want to see performance per pair per hour
for pair in sorted(df['pair'].unique()):
    pair_df = df[df['pair'] == pair]
    print(f"\n{'='*50}\n{pair} Time Analysis\n{'='*50}")
    
    # By Hour
    hourly = pair_df.groupby('hour').agg(
        trades=('is_win', 'count'),
        wins=('is_win', 'sum'),
        pnl=('profit_amount', 'sum')
    )
    hourly['win_rate'] = (hourly['wins'] / hourly['trades'] * 100).round(1)
    
    # Filter only hours with terrible performance to highlight
    # (e.g. negative PnL or win rate < 50%)
    print("--- [By Hour (All Trades)] ---")
    print(hourly.sort_index().to_string())
    
    bad_hours = hourly[(hourly['pnl'] < 0) | (hourly['win_rate'] < 50)]
    print("\n--- [Bad Hours Candidates for Restriction] ---")
    if not bad_hours.empty:
        print(bad_hours.sort_values('pnl').to_string())
    else:
        print("None! All traded hours are profitable.")
        
    # By Weekday
    print("\n--- [By Weekday (0=Mon, 4=Fri)] ---")
    weekday = pair_df.groupby('weekday').agg(
        trades=('is_win', 'count'),
        wins=('is_win', 'sum'),
        pnl=('profit_amount', 'sum')
    )
    weekday['win_rate'] = (weekday['wins'] / weekday['trades'] * 100).round(1)
    print(weekday.sort_index().to_string())
