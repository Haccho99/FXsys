import sys
import os
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np

df = pd.read_csv('2026-08-02T13-04_export.csv')
print("Total rows:", len(df))

df['entry_time'] = pd.to_datetime(df['エントリー日時'])
df['exit_time'] = pd.to_datetime(df['決済日時'])

print("\nDate range of entry_time:")
min_dt = df['entry_time'].min()
max_dt = df['entry_time'].max()
print("Min:", min_dt)
print("Max:", max_dt)

# Hour extraction
df['entry_hour'] = df['entry_time'].dt.hour
df['entry_date'] = df['entry_time'].dt.date
df['weekday'] = df['entry_time'].dt.day_name()
df['weekday_num'] = df['entry_time'].dt.weekday

# 1. Hourly distribution
hourly_counts = df['entry_hour'].value_counts().sort_index()
print("\n--- Hourly Entry Counts ---")
for h in range(24):
    cnt = (df['entry_hour'] == h).sum()
    pct = cnt / len(df) * 100
    print(f"Hour {h:02d}:00-{h:02d}:59 : {cnt:4d} ({pct:5.2f}%)")

most_frequent_hour = df['entry_hour'].value_counts().idxmax()
most_frequent_cnt = df['entry_hour'].value_counts().max()
print(f"\n[Q1] Most frequent entry hour: {most_frequent_hour}:00 - {most_frequent_hour}:59 (Count: {most_frequent_cnt}, {most_frequent_cnt/len(df)*100:.2f}%)")

# Also check top 3 hours:
print("\nTop 5 entry hours:")
print(df['entry_hour'].value_counts().head(5))

# 2. Entries between 06:00 and 16:00
entries_6_to_16_strict = df[(df['entry_hour'] >= 6) & (df['entry_hour'] < 16)] # 06:00 - 15:59
entries_6_to_16_inclusive = df[(df['entry_hour'] >= 6) & (df['entry_hour'] <= 16)] # 06:00 - 16:59
print(f"\n[Q2] Entries between 06:00 and 15:59 (06:00 - 16:00前): {len(entries_6_to_16_strict)} (out of {len(df)}, {len(entries_6_to_16_strict)/len(df)*100:.2f}%)")
print(f"[Q2-alt] Entries between 06:00 and 16:59 (06:00 - 16:00台含む): {len(entries_6_to_16_inclusive)} (out of {len(df)}, {len(entries_6_to_16_inclusive)/len(df)*100:.2f}%)")

# Detailed hourly breakdown 6 to 16:
print("\nHourly details 06:00 - 16:00:")
for h in range(6, 17):
    cnt = (df['entry_hour'] == h).sum()
    pct = cnt / len(df) * 100
    print(f"Hour {h:02d}:00-{h:02d}:59 : {cnt:4d} ({pct:5.2f}%)")

# 3. Market open days with 0 entries
min_date = df['entry_time'].min().date()
max_date = df['entry_time'].max().date()
print(f"\nFull date range from {min_date} to {max_date}")

# Let's count business days (Mon-Fri)
all_dates = pd.date_range(min_date, max_date, freq='B') # Business days (Mon-Fri)
total_bdays = len(all_dates)

entry_dates = set(df['entry_date'].unique())
no_entry_business_days = [d.date() for d in all_dates if d.date() not in entry_dates]
print(f"\n[Q3] Total Business Days (Mon-Fri): {total_bdays} days")
print(f"[Q3] Business days with 0 entries: {len(no_entry_business_days)} days ({len(no_entry_business_days)/total_bdays*100:.2f}%)")
print("List of 0-entry business days:")
for d in no_entry_business_days:
    print(f"  {d} ({d.strftime('%A')})")

# Daily stats
daily_counts = df.groupby('entry_date').size()
all_bday_counts = pd.Series(index=all_dates.date, data=0)
for d, cnt in daily_counts.items():
    if d in all_bday_counts.index:
        all_bday_counts[d] = cnt

print(f"\n--- Daily Trade Statistics across {total_bdays} Business Days ---")
print(f"Total entries: {len(df)}")
print(f"Mean entries / business day: {all_bday_counts.mean():.2f}")
print(f"Median entries / business day: {all_bday_counts.median():.2f}")
print(f"Std dev: {all_bday_counts.std():.2f}")
print(f"Min: {all_bday_counts.min()}, Max: {all_bday_counts.max()}")

print("\nDistribution of trades per day:")
bins = [0, 1, 3, 5, 8, 12, 20, 100]
labels = ['0 trades', '1-2 trades', '3-4 trades', '5-7 trades', '8-11 trades', '12-19 trades', '20+ trades']
# Let's do exact counts
print("0 trades:", (all_bday_counts == 0).sum(), f"({(all_bday_counts == 0).mean()*100:.1f}%)")
print("1 trade :", (all_bday_counts == 1).sum(), f"({(all_bday_counts == 1).mean()*100:.1f}%)")
print("2 trades:", (all_bday_counts == 2).sum(), f"({(all_bday_counts == 2).mean()*100:.1f}%)")
print("3 trades:", (all_bday_counts == 3).sum(), f"({(all_bday_counts == 3).mean()*100:.1f}%)")
print("4 trades:", (all_bday_counts == 4).sum(), f"({(all_bday_counts == 4).mean()*100:.1f}%)")
print("5 trades:", (all_bday_counts == 5).sum(), f"({(all_bday_counts == 5).mean()*100:.1f}%)")
print("6 trades:", (all_bday_counts == 6).sum(), f"({(all_bday_counts == 6).mean()*100:.1f}%)")
print("7 trades:", (all_bday_counts == 7).sum(), f"({(all_bday_counts == 7).mean()*100:.1f}%)")
print("8+ trades:", (all_bday_counts >= 8).sum(), f"({(all_bday_counts >= 8).mean()*100:.1f}%)")

# Weekday distribution
print("\nEntries by Weekday:")
for w in range(7):
    day_df = df[df['weekday_num'] == w]
    print(f"Weekday {w} ({['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][w]}): {len(day_df)} trades ({len(day_df)/len(df)*100:.1f}%)")

# Pair breakdown
print("\nEntries by Pair:")
print(df['通貨ペア'].value_counts())

# Pair vs Hour matrix
print("\nPair vs Hour (Top Hours):")
pair_hour = pd.crosstab(df['通貨ペア'], df['entry_hour'])
print(pair_hour)
