import glob, pandas as pd

files = glob.glob('logs/virtual_trade_log_*.csv')
if not files:
    print('No log files found.')
    exit()

df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df['timestamp_dt'] = pd.to_datetime(df['timestamp'], utc=True)
df = df.sort_values('timestamp_dt')

gross_profit = df[df['profit_amount'] > 0]['profit_amount'].sum()
gross_loss = abs(df[df['profit_amount'] < 0]['profit_amount'].sum())
profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')

df['cumulative_pnl'] = df['profit_amount'].cumsum()
df['peak'] = df['cumulative_pnl'].cummax()
df['drawdown'] = df['peak'] - df['cumulative_pnl']
max_drawdown = df['drawdown'].max()

print(f"Profit Factor: {profit_factor:.3f}")
print(f"Max Drawdown: {max_drawdown:,.0f} JPY")
