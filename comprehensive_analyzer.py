import polars as pl
from datetime import datetime, timezone

def main():
    file_path = "logs/wfa_final_report_20260728_163749.parquet"
    print(f"Loading data from {file_path}...")
    df = pl.read_parquet(file_path)
    
    # Check what 'Pair' column has
    if 'Pair' not in df.columns and 'pair' in df.columns:
        df = df.rename({'pair': 'Pair'})

    # Filter for date: 2025-07-01 to 2026-06-30
    start_date = datetime(2025, 7, 1, tzinfo=timezone.utc)
    end_date = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
    df = df.filter((pl.col('Entry Timestamp') >= start_date) & (pl.col('Entry Timestamp') <= end_date))

    # 1. Overall Summary
    total_trades = len(df)
    winning_trades = df.filter(pl.col('PnL') > 0)
    losing_trades = df.filter(pl.col('PnL') <= 0)
    win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0
    total_pnl = df['PnL'].sum()
    
    avg_profit = winning_trades['PnL'].mean() if len(winning_trades) > 0 else 0
    avg_loss = abs(losing_trades['PnL'].mean()) if len(losing_trades) > 0 else 1
    rr_ratio = avg_profit / avg_loss if avg_loss > 0 else 0

    print("\n--- 1. Overall Summary ---")
    print(f"Filtered Date: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    print(f"Total Trades: {total_trades}")
    print(f"Win Rate: {win_rate:.1%}")
    print(f"RR Ratio: {rr_ratio:.2f}")
    print(f"Total PnL: {total_pnl:.2f}")

    # 2. Pair-wise Performance
    print("\n--- 2. Pair-wise Performance ---")
    pair_stats = df.group_by('Pair').agg([
        pl.len().alias('Trades'),
        (pl.col('PnL') > 0).sum().alias('Wins'),
        pl.col('PnL').sum().alias('Total_PnL')
    ]).with_columns(
        (pl.col('Wins') / pl.col('Trades')).alias('Win_Rate')
    ).sort('Total_PnL', descending=True)
    print(pair_stats.to_pandas().to_string(index=False))

    # 3. Day of Week Performance (Monday=0, Sunday=6)
    print("\n--- 3. Day of Week Performance ---")
    df = df.with_columns(pl.col('Entry Timestamp').dt.weekday().alias('DayOfWeek') - 1)
    day_names = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
    dow_stats = df.group_by('DayOfWeek').agg([
        pl.len().alias('Trades'),
        pl.col('PnL').sum().alias('Total_PnL')
    ]).sort('DayOfWeek')
    dow_pandas = dow_stats.to_pandas()
    dow_pandas['DayName'] = dow_pandas['DayOfWeek'].map(day_names)
    print(dow_pandas[['DayOfWeek', 'DayName', 'Trades', 'Total_PnL']].to_string(index=False))

    # 4. Hour of Day Performance
    print("\n--- 4. Hour of Day Performance ---")
    df = df.with_columns(pl.col('Entry Timestamp').dt.hour().alias('HourOfDay'))
    hour_stats = df.group_by('HourOfDay').agg([
        pl.len().alias('Trades'),
        pl.col('PnL').sum().alias('Total_PnL')
    ]).sort('HourOfDay')
    print(hour_stats.to_pandas().to_string(index=False))

    # 5. Exit Reason Performance
    print("\n--- 5. Exit Reason Performance ---")
    reason_col = 'Exit Reason Detail' if 'Exit Reason Detail' in df.columns else 'reason'
    if reason_col in df.columns:
        exit_stats = df.group_by(reason_col).agg([
            pl.len().alias('Trades'),
            pl.col('PnL').mean().alias('Avg_PnL'),
            pl.col('PnL').sum().alias('Total_PnL')
        ]).sort('Total_PnL', descending=True)
        print(exit_stats.to_pandas().to_string(index=False))
    else:
        print(f"Column for exit reason not found.")

    # 6. AI Score Performance
    print("\n--- 6. AI Score Performance ---")
    if 'score' in df.columns:
        # Create score bins
        df = df.with_columns(
            (pl.col('score') * 10).cast(pl.Int32).alias('Score_Bin_x10')
        )
        score_stats = df.group_by('Score_Bin_x10').agg([
            pl.len().alias('Trades'),
            (pl.col('PnL') > 0).sum().alias('Wins'),
            pl.col('PnL').sum().alias('Total_PnL')
        ]).with_columns(
            (pl.col('Wins') / pl.col('Trades')).alias('Win_Rate')
        ).sort('Score_Bin_x10')
        print(score_stats.to_pandas().to_string(index=False))
    else:
        print("Score column not found.")

if __name__ == "__main__":
    main()
