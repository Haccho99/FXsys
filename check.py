import pandas as pd
import sys

try:
    df = pd.read_csv('C:/WealthSystem/FXsys/2026-07-12T09-24_export.csv', encoding='utf-8-sig')
except:
    df = pd.read_csv('C:/WealthSystem/FXsys/2026-07-12T09-24_export.csv', encoding='shift_jis')

df['エントリー日時'] = pd.to_datetime(df['エントリー日時'])

forbidden = df[(df['エントリー日時'].dt.hour >= 1) & (df['エントリー日時'].dt.hour < 9)]
print(f'{len(forbidden)} forbidden trades')
if len(forbidden) > 0:
    print(forbidden[['エントリー日時']].head())
