import pandas as pd
import glob
from pathlib import Path
import re
import numpy as np

print("="*60)
print(" 🕵️‍♂️ Gemini CLI: バックテスト・データ解析エンジン起動")
print("="*60)

# --- 1. データの収集 ---
report_dir = Path("data/reports")
log_dir = Path("logs")

# CSVデータの検索
csv_files = list(report_dir.glob("*.csv"))
decision_df = pd.DataFrame()
if csv_files:
    # decision_log_multi_pairs.csv などを読み込む
    latest_csv = report_dir / "decision_log_multi_pairs.csv"
    if not latest_csv.exists():
        latest_csv = max(csv_files, key=lambda p: p.stat().st_mtime)
    print(f"[データ取得] CSVファイルを使用: {latest_csv.name}")
    decision_df = pd.read_csv(latest_csv)
else:
    print("[警告] data/reports/ 内にCSVファイルが見つかりません。")

# ログファイルの検索（コンソール出力の最終結果を抽出）
log_files = list(log_dir.glob("system_*.log"))
latest_log_content = ""
if log_files:
    latest_log = max(log_files, key=lambda p: p.stat().st_mtime)
    print(f"[データ取得] 最新のログファイルを発見: {latest_log.name}")
    with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
        latest_log_content = f.read()

# --- 2. パフォーマンスのパースと集計 ---
print("\n" + "="*60)
print(" 📊 【フェーズ1】総合パフォーマンス解析")
print("="*60)

# ログからパフォーマンスレポートブロックを正規表現で探す
report_match = re.search(r'🏆 複数通貨ペア 自動売買 パフォーマンス・レポート.*?(=+ *\n)', latest_log_content, re.DOTALL)
if report_match:
    print(report_match.group(0))
else:
    print("ログファイル内に詳細なパフォーマンスレポートが見つかりませんでした。")
    # 取引ログ（CSV）からの独自集計を試みる
    trade_log_files = list(log_dir.glob("virtual_trade_log_*.csv"))
    if trade_log_files:
        print(f"[データ集計] {len(trade_log_files)} 個の取引ログを統合解析します...")
        combined_trade_df = pd.concat([pd.read_csv(f) for f in trade_log_files])
        
        # 損益計算
        total_trades = len(combined_trade_df)
        win_trades = combined_trade_df[combined_trade_df['profit_amount'] > 0]
        loss_trades = combined_trade_df[combined_trade_df['profit_amount'] <= 0]
        
        win_rate = (len(win_trades) / total_trades * 100) if total_trades > 0 else 0
        total_profit = combined_trade_df['profit_amount'].sum()
        gross_profit = win_trades['profit_amount'].sum()
        gross_loss = abs(loss_trades['profit_amount'].sum())
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
        
        def calc_pips(row):
            mult = 100.0 if 'JPY' in str(row.get('pair', '')) else 10000.0
            diff = row['exit_price'] - row['entry_price']
            return diff * mult if row['direction'] == 'long' else -diff * mult
        
        combined_trade_df['pips'] = combined_trade_df.apply(calc_pips, axis=1)
        avg_tp = combined_trade_df[combined_trade_df['profit_amount'] > 0]['pips'].mean()
        avg_sl = combined_trade_df[combined_trade_df['profit_amount'] <= 0]['pips'].mean()
        
        print(f"\n--- 取引ログからの統合集計結果 ---")
        print(f"総取引回数 : {total_trades} 回")
        print(f"勝率       : {win_rate:.1f} %")
        print(f"純利益     : {total_profit:,.0f} JPY")
        print(f"プロフィットファクター (PF) : {pf:.2f}")
        print(f"平均利確幅 : {avg_tp:.1f} pips")
        print(f"平均損切幅 : {avg_sl:.1f} pips")
        print(f"実質RR比   : {abs(avg_tp/avg_sl) if avg_sl != 0 and not pd.isna(avg_sl) else 0:.2f}")

# --- 3. ロジック整合性と防衛壁の検証 ---
print("\n" + "="*60)
print(" 🛡️ 【フェーズ2】ロジック整合性＆防衛壁チェック")
print("="*60)

if not decision_df.empty and 'Time' in decision_df.columns:
    # 時間をパース
    decision_df['datetime'] = pd.to_datetime(decision_df['Time'])
    decision_df['hour'] = decision_df['datetime'].dt.hour
    
    # ① 魔の時間帯ガードのチェック (01:00 - 08:59)
    entries = decision_df[decision_df['Action'] == 'ENTRY']
    forbidden_entries = entries[(entries['hour'] >= 1) & (entries['hour'] <= 8)]
    print(f"[チェック] 魔の時間帯(01:00-08:59)のエントリー数: {len(forbidden_entries)} 件")
    if len(forbidden_entries) == 0:
        print("  => 完璧です。早朝のスプレッド拡大リスクを完全に回避しています。")
    else:
        print("  => ⚠️ 警告: 禁止時間帯にエントリーが漏れています。")
        print(forbidden_entries[['Time', 'Pair', 'Price']].head(10).to_string(index=False))

    # ② 通貨ペア別のエントリー分布
    print("\n[チェック] 通貨ペア別 エントリー回数分布:")
    pair_counts = entries['Pair'].value_counts()
    for pair, count in pair_counts.items():
        print(f"  - {pair}: {count} 回")

else:
    print("CSVデータに必要なカラム（Time, Action 等）がないため、時間帯ガードの検証をスキップします。")

print("\n" + "="*60)
print(" 🏁 解析完了")
print("="*60)
