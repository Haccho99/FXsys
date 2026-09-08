import glob, pandas as pd

files = glob.glob('logs/virtual_trade_log_*.csv')
df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df['is_win'] = df['profit_amount'] > 0

if 'lot_size' in df.columns:
    df = df[df['lot_size'] > 0].copy()
    # lot_size is inversely proportional to ATR in this system
    df['implied_volatility'] = 1.0 / df['lot_size']
    mean_vol = df.groupby('pair')['implied_volatility'].transform('mean')
    df['vol_ratio'] = df['implied_volatility'] / mean_vol
    
    df['vol_bin'] = pd.cut(df['vol_ratio'], bins=[0, 0.8, 1.2, 1.5, 2.0, 10.0], labels=['Low (<0.8x)', 'Normal (0.8-1.2x)', 'High (1.2-1.5x)', 'Very High (1.5-2.0x)', 'Extreme (>2.0x)'])
    
    print("\n--- Win Rate by Implied Volatility (ALL PAIRS) ---")
    for bin_label, group in df.groupby('vol_bin', observed=False):
        if len(group) > 0:
            print(f"Vol Ratio {bin_label:20s} | Trades: {len(group):4d} | Win Rate: {group['is_win'].mean():.2%} | PnL: {group['profit_amount'].sum():,.0f} JPY")

    print("\n--- Win Rate by Implied Volatility (EUR & GBP only) ---")
    df_eur_gbp = df[df['pair'].str.contains('EUR|GBP')]
    for bin_label, group in df_eur_gbp.groupby('vol_bin', observed=False):
        if len(group) > 0:
            print(f"Vol Ratio {bin_label:20s} | Trades: {len(group):4d} | Win Rate: {group['is_win'].mean():.2%} | PnL: {group['profit_amount'].sum():,.0f} JPY")
