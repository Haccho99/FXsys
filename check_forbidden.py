import pandas as pd
import glob
from pytz import timezone

files = glob.glob('logs/virtual_trade_log_*.csv')
if not files:
    print('No logs found.')
    exit()

df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df['entry_time_dt'] = pd.to_datetime(df['entry_time'])
df['entry_time_jst'] = df['entry_time_dt'].dt.tz_convert('Asia/Tokyo')

df['hour'] = df['entry_time_jst'].dt.hour
df['day_of_week'] = df['entry_time_jst'].dt.dayofweek # 0=Mon, 4=Fri

# Rule 1: Daily forbidden hours 01:00 to 08:59
violations_daily = df[(df['hour'] >= 1) & (df['hour'] < 9)]

# Rule 2: Friday after 20:00
violations_friday = df[(df['day_of_week'] == 4) & (df['hour'] >= 20)]

print(f"Total Trades Analyzed: {len(df)}")
print(f"Daily Forbidden Violations (01:00-08:59 JST): {len(violations_daily)} trades")
if len(violations_daily) > 0:
    print("Sample daily violations:")
    print(violations_daily[['entry_time_jst', 'pair', 'direction', 'strategy']].head())

print(f"\nFriday Forbidden Violations (After 20:00 JST): {len(violations_friday)} trades")
if len(violations_friday) > 0:
    print("Sample Friday violations:")
    print(violations_friday[['entry_time_jst', 'pair', 'direction', 'strategy']].head())

print("\n--- Violation Analysis Complete ---")
