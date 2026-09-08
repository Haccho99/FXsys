import json
import pandas as pd
import glob
from pytz import timezone

# Load config
with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

active_windows = config['system'].get('active_windows', {})

files = glob.glob('logs/virtual_trade_log_*.csv')
if not files:
    print('No logs found.')
    exit()

df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df['entry_time_dt'] = pd.to_datetime(df['entry_time'])
df['entry_time_jst'] = df['entry_time_dt'].dt.tz_convert('Asia/Tokyo')

# Create a time string HH:MM for easy comparison
df['time_str'] = df['entry_time_jst'].dt.strftime('%H:%M')

violations = []
for index, row in df.iterrows():
    pair = row['pair']
    time_str = row['time_str']
    
    windows = active_windows.get(pair, [])
    # If no windows are defined, all times (except forbidden_hours) are allowed
    if not windows:
        continue
        
    is_valid = False
    for window in windows:
        start = window['start']
        end = window['end']
        
        # Handle cases where end time is on the next day (e.g., 22:30 to 01:00)
        if start > end:
            if time_str >= start or time_str <= end:
                is_valid = True
                break
        else:
            if start <= time_str <= end:
                is_valid = True
                break
                
    if not is_valid:
        violations.append(row)

violations_df = pd.DataFrame(violations)
print(f"Total Trades Analyzed: {len(df)}")
if len(violations_df) > 0:
    print(f"Active Window Violations: {len(violations_df)} trades")
    print(violations_df.groupby('pair').size())
    print("\nSample violations:")
    print(violations_df[['entry_time_jst', 'pair', 'time_str']].head(10))
else:
    print("No Active Window Violations found. All trades were executed within their respective allowed windows.")

