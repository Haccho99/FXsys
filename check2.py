import pandas as pd

try:
    df = pd.read_csv('C:/WealthSystem/FXsys/2026-07-12T09-24_export.csv', encoding='utf-8-sig')
except:
    df = pd.read_csv('C:/WealthSystem/FXsys/2026-07-12T09-24_export.csv', encoding='shift_jis')

df['エントリー日時'] = pd.to_datetime(df['エントリー日時'])
# Consultant mistake: assume it's UTC and convert to JST
df['consultant_time'] = df['エントリー日時'].dt.tz_localize('UTC').dt.tz_convert('Asia/Tokyo')

forbidden = df[(df['consultant_time'].dt.hour >= 1) & (df['consultant_time'].dt.hour < 9)]
print(f'{len(forbidden)} forbidden trades found with consultant mistake')
