import pandas as pd
from pathlib import Path
import re
import numpy as np

print("="*60)
print(" 🕵️‍♂️ Gemini CLI: フィルター検証用・ハイブリッド解析エンジン (修正版)")
print("="*60)

# --- 1. データの収集 ---
csv_path = Path("data/reports/decision_log_multi_pairs.csv")
log_path = Path("logs/system_20260613_165955_33068.log")

if not csv_path.exists():
    print(f"❌ [エラー] {csv_path} が見つかりません。")
    exit()

if not log_path.exists():
    print(f"❌ [エラー] {log_path} が見つかりません。")
    exit()

print(f"📂 読み込みCSV: {csv_path.name}")
print(f"📂 読み込みLOG: {log_path.name}")

df = pd.read_csv(csv_path)
with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
    log_content = f.read()

# --- 2. ログファイルからのパフォーマンス抽出 ---
print("\n📊 【全体パフォーマンス (ログ解析)】")
# "期間検証結果" を含むパターンに修正
pattern = r'🏆 【(.*?)】 期間検証結果.*?最終残高\s*: (.*?) JPY.*?取引回数\s*: (\d+) 回 \(勝率: ([\d.]+)%\).*?PF\s*: ([\d.]+)'
matches = re.findall(pattern, log_content, re.DOTALL)

total_trades = 0
total_balance = 0
initial_balance = 250000

if matches:
    for match in matches:
        pair, balance_str, trades, win_rate, pf = match
        balance = float(balance_str.replace(',', ''))
        total_trades += int(trades)
        total_balance += balance
        net_profit = balance - initial_balance
        print(f" - {pair}: 取引 {trades:>3}回 | 勝率 {win_rate:>5}% | PF {pf:>4} | 損益 {net_profit:+8,.0f} JPY")
    
    total_net_profit = total_balance - (initial_balance * len(matches))
    print("-" * 50)
    print(f" 📈 全体合計: 取引 {total_trades}回 | 総純利益 {total_net_profit:+,.0f} JPY")
else:
    print("⚠️ ログからパフォーマンス結果を抽出できませんでした。")
    # 代替手段: CSVから直接集計 (もし損益データがあれば)
    # virtual_trade_log_*.csv を探す
    log_dir = Path("logs")
    trade_logs = list(log_dir.glob("virtual_trade_log_*.csv"))
    if trade_logs:
        print("\n[INFO] virtual_trade_log_*.csv から直接集計を試みます...")
        all_trades = pd.concat([pd.read_csv(f) for f in trade_logs])
        all_trades = all_trades.drop_duplicates(subset=['timestamp', 'entry_price'])
        
        t_trades = len(all_trades)
        t_wins = all_trades[all_trades['profit_amount'] > 0]
        t_wr = len(t_wins) / t_trades * 100 if t_trades > 0 else 0
        t_profit = all_trades['profit_amount'].sum()
        
        gross_profit = t_wins['profit_amount'].sum()
        gross_loss = abs(all_trades[all_trades['profit_amount'] <= 0]['profit_amount'].sum())
        t_pf = gross_profit / gross_loss if gross_loss > 0 else 0
        
        print(f" 📊 CSV集計結果: 取引 {t_trades}回 | 勝率 {t_wr:.1f}% | PF {t_pf:.2f} | 損益 {t_profit:+,.0f} JPY")

# --- 3. CSVからのクオンツ解析 (防衛壁と設定RR比) ---
print("\n🛡️ 【ロジック・防衛壁の確認 (CSV解析)】")

if 'Time' in df.columns:
    df['datetime'] = pd.to_datetime(df['Time'])
    df['jst'] = df['datetime'] + pd.Timedelta(hours=9)
    forbidden_jst = df[(df['jst'].dt.hour >= 1) & (df['jst'].dt.hour <= 8)]
    
    if len(forbidden_jst) == 0:
        print(" => ✅ 完璧です。JST 01:00-08:59 の魔の時間帯エントリーは0件です。")
    else:
        print(f" => ⚠️ 警告: {len(forbidden_jst)}件の禁止時間帯(JST)エントリーが漏れています。")

if all(c in df.columns for c in ['Direction', 'Price', 'SL_Price', 'TP_Price']):
    def calc_rr(row):
        try:
            risk = abs(row['Price'] - row['SL_Price'])
            reward = abs(row['Price'] - row['TP_Price'])
            return reward / risk if risk > 0 else np.nan
        except:
            return np.nan

    df['Target_RR'] = df.apply(calc_rr, axis=1)
    valid_rr = df['Target_RR'].dropna()
    if not valid_rr.empty:
        mean_rr = valid_rr.mean()
        print(f" => 🎯 設定リスクリワード(RR)比の平均: {mean_rr:.2f} (目標: 1.50)")

print("\n" + "="*60)
print(" 💡 Gemini（LLM）への指示：")
print(" 以上の出力結果を、前回のベーステスト結果（Tier1のみ: 取引回数654回, 勝率21.6%, PF0.33）と比較してください。")
print(" 指定された【標準比較・検証フォーム】のマークダウン表を生成し、今回のTier追加によって「どのダマシが削られたか」「期待値がどう改善したか」をクオンツの視点で論理的に分析してください。")
print("="*60)
