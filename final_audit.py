import pandas as pd
import glob
import os

print("=== TP/SL 正常稼働の最終確認 ===")
# logsディレクトリも含めて検索
log_files = glob.glob("logs/virtual_trade_log_*.csv")
if not log_files:
    log_files = glob.glob("virtual_trade_log_*.csv")

if not log_files:
    print("ログが見つかりません。")
else:
    latest_log = max(log_files, key=os.path.getctime)
    df = pd.read_csv(latest_log)
    print(f"対象ファイル: {latest_log}\n")

    # pips計算 (ロングとショートの方向を考慮)
    def calc_pips(row):
        diff = (row.get('exit_price', 0) - row.get('entry_price', 0)) * 100
        return diff if row.get('direction') == 'long' else -diff

    df['actual_pips'] = df.apply(calc_pips, axis=1)

    print("[1] 決済理由の内訳")
    if 'reason' in df.columns:
        print(df['reason'].value_counts().to_string())
    else:
        print("reason カラムがありません。")

    print("\n[2] 平均獲得 / 損失 pips")
    tp_df = df[df['reason'] == 'TP'] if 'reason' in df.columns else pd.DataFrame()
    sl_df = df[df['reason'] == 'SL'] if 'reason' in df.columns else pd.DataFrame()
    wk_df = df[df['reason'] == 'Weekend Close'] if 'reason' in df.columns else pd.DataFrame()

    if not tp_df.empty:
        print(f"  TP到達時 平均: {tp_df['actual_pips'].mean():+.1f} pips (最大: {tp_df['actual_pips'].max():+.1f}, 最小: {tp_df['actual_pips'].min():+.1f})")
    if not sl_df.empty:
        print(f"  SL到達時 平均: {sl_df['actual_pips'].mean():+.1f} pips (最大: {sl_df['actual_pips'].max():+.1f}, 最小: {sl_df['actual_pips'].min():+.1f})")
    if not wk_df.empty:
        print(f"  Weekend Close 平均: {wk_df['actual_pips'].mean():+.1f} pips")

    print("\n[3] 直近5件のトレード詳細")
    for _, row in df.tail(5).iterrows():
        print(f"  {str(row.get('direction', 'N/A')).upper():<5} | 理由: {str(row.get('reason', 'N/A')):<15} | Pips: {row['actual_pips']:+6.1f} | 損益: {row.get('profit_amount', 0):>8,.0f} JPY")
