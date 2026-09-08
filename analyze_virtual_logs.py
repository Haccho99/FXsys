import glob, os, pandas as pd
import numpy as np

files = glob.glob('logs/virtual_trade_log_*.csv')
if not files:
    print('No log files found.')
    exit()

df_list = []
for f in files:
    try:
        df_list.append(pd.read_csv(f))
    except Exception:
        pass
if not df_list:
    print("No valid data.")
    exit()

df = pd.concat(df_list, ignore_index=True)
df['is_win'] = df['profit_amount'] > 0

print("--- OVERALL SUMMARY ---")
print(f"Total Trades: {len(df)}")
print(f"Overall Win Rate: {df['is_win'].mean():.2%}")
print(f"Total PnL: {df['profit_amount'].sum():,.0f} JPY")

print("\n--- PAIR BREAKDOWN ---")
for pair, group in df.groupby('pair'):
    print(f"{pair:10s} | Trades: {len(group):4d} | Win Rate: {group['is_win'].mean():.2%} | PnL: {group['profit_amount'].sum():,.0f} JPY")

print("\n--- STRATEGY BREAKDOWN ---")
for strat, group in df.groupby('strategy'):
    print(f"{strat:10s} | Trades: {len(group):4d} | Win Rate: {group['is_win'].mean():.2%} | PnL: {group['profit_amount'].sum():,.0f} JPY")

print("\n--- REASON BREAKDOWN ---")
for reason, group in df.groupby('reason'):
    print(f"{reason:20s} | Trades: {len(group):4d} | Win Rate: {group['is_win'].mean():.2%} | PnL: {group['profit_amount'].sum():,.0f} JPY")

print("\n--- AI SCORE ANALYSIS ---")
if 'ai_score' in df.columns:
    df['score_bin'] = pd.cut(df['ai_score'], bins=[0, 0.4, 0.6, 0.7, 0.8, 1.0], labels=['0.0-0.4', '0.4-0.6', '0.6-0.7', '0.7-0.8', '0.8-1.0'])
    for bin_label, group in df.groupby('score_bin', observed=False):
        if len(group) > 0:
            print(f"Score {bin_label:7s} | Trades: {len(group):4d} | Win Rate: {group['is_win'].mean():.2%} | PnL: {group['profit_amount'].sum():,.0f} JPY | Avg PnL: {group['profit_amount'].mean():,.0f} JPY")
    
    correlation = df['ai_score'].corr(df['profit_amount'])
    print(f"\nCorrelation (AI Score vs PnL): {correlation:.3f}")
else:
    print("No ai_score column found.")
