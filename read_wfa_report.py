import glob
import polars as pl
import os
import numpy as np

files = glob.glob('logs/wfa_final_report_*.parquet')
latest_file = max(files, key=os.path.getctime)
df = pl.read_parquet(latest_file)

usd_df = df.filter((pl.col("Pair") == "USD_JPY") & (pl.col("Walk Number") == 9)).sort("Entry Timestamp")

for strategy_marker in ['bb_squeeze', 'trend']:
    if "Position Id" in usd_df.columns:
        sub_df = usd_df.filter(pl.col("Position Id").str.contains(strategy_marker))
    else:
        sub_df = usd_df
        
    if len(sub_df) == 0:
        print(f"No trades found for {strategy_marker}")
        continue
    
    pnl = sub_df["PnL"].to_numpy()
    wins = (pnl > 0).sum()
    win_rate = wins / len(pnl) if len(pnl) > 0 else 0

    gross_profit = pnl[pnl > 0].sum()
    gross_loss = abs(pnl[pnl < 0].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    cum_pnl = pnl.cumsum()
    # Assume 1M starting balance and PnL is raw currency or normalized, just compute max relative drawdown
    balance = 1_000_000 + cum_pnl
    peak = np.maximum.accumulate(balance)
    drawdown = (peak - balance) / peak
    max_drawdown = drawdown.max()

    print(f"\n--- USD_JPY Walk 9 Performance ({strategy_marker}) ---")
    print(f"Trades       : {len(pnl)}")
    print(f"Win Rate     : {win_rate:.2%}")
    print(f"Profit Factor: {profit_factor:.3f}")
    print(f"Max Drawdown : {max_drawdown:.2%}")
    print(f"Total PnL    : {cum_pnl[-1]}")
