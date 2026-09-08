import pandas as pd
import numpy as np
from pathlib import Path
import sys
import io

def analyze():
    export_file = Path("2026-07-09T07-37_export.csv")
    if not export_file.exists():
        print(f"Error: {export_file} not found")
        return
        
    try:
        # Read the file. It's likely cp932 or utf-8 encoded.
        try:
            df_trades = pd.read_csv(export_file, encoding='utf-8')
        except UnicodeDecodeError:
            df_trades = pd.read_csv(export_file, encoding='cp932')
    except Exception as e:
        print(f"Error reading {export_file}: {e}")
        return
        
    print("=== 解析対象トレード数 ===")
    print(f"{len(df_trades)} 件")
    
    # 損益額のクレンジング (e.g. "111 円" -> 111)
    if '損益額' in df_trades.columns:
        df_trades['profit_amount'] = df_trades['損益額'].astype(str).str.replace('円', '').str.replace(',', '').astype(float)
    else:
        print("Error: '損益額' column not found")
        return

    # Time parsing (決済日時 = Exit Time, エントリー日時 = Entry Time)
    if '決済日時' in df_trades.columns and 'エントリー日時' in df_trades.columns:
        df_trades['exit_time'] = pd.to_datetime(df_trades['決済日時'])
        df_trades['entry_time'] = pd.to_datetime(df_trades['エントリー日時'])
        df_trades['hold_duration'] = (df_trades['exit_time'] - df_trades['entry_time']).dt.total_seconds() / 60.0  # in minutes
        
    # Reason
    if '決済理由' in df_trades.columns:
        df_trades['reason'] = df_trades['決済理由'].astype(str)

    # 1. 利小損大の構造
    print("\n=== 【1】利小損大の構造 ===")
    wins = df_trades[df_trades['profit_amount'] > 0]
    losses = df_trades[df_trades['profit_amount'] < 0]
    
    avg_win = wins['profit_amount'].mean() if not wins.empty else 0
    avg_loss = losses['profit_amount'].mean() if not losses.empty else 0
    win_rate = len(wins) / len(df_trades) * 100 if len(df_trades) > 0 else 0
    
    print(f"勝率: {win_rate:.1f}%")
    print(f"平均利益: {avg_win:,.0f} JPY")
    print(f"平均損失: {avg_loss:,.0f} JPY")
    if avg_loss != 0:
        rr_ratio = abs(avg_win / avg_loss)
        print(f"リスクリワード比 (平均利益/平均損失): {rr_ratio:.2f}")
    
    # 2. 決済理由ごとの内訳
    print("\n=== 【2】決済理由ごとの内訳 ===")
    if 'reason' in df_trades.columns:
        reason_stats = df_trades.groupby('reason').agg(
            count=('profit_amount', 'count'),
            avg_profit=('profit_amount', 'mean'),
            win_rate=('profit_amount', lambda x: (x > 0).sum() / len(x) * 100)
        ).round(2)
        print(reason_stats)
    
    # 3. 決済理由ごとの保有時間
    print("\n=== 【3】決済理由ごとの保有時間 ===")
    if 'reason' in df_trades.columns and 'hold_duration' in df_trades.columns:
        hold_time_stats = df_trades.groupby('reason').agg(
            avg_hold_minutes=('hold_duration', 'mean'),
            median_hold_minutes=('hold_duration', 'median'),
            max_hold_minutes=('hold_duration', 'max')
        ).round(2)
        print(hold_time_stats)

if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    analyze()
