import pandas as pd
from pathlib import Path
import re
import numpy as np

print("="*60)
print(" 🕵️‍♂️ Gemini CLI: フィルター検証用・ハイブリッド解析エンジン (ターゲット指定版)")
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
pattern = r'🏆 【(.*?)】 検証結果.*?最終残高\s*: (.*?) JPY.*?取引回数\s*: (\d+) 回 \(勝率: ([\d.]+)%\).*?PF\s*: ([\d.]+)'
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

# --- 3. CSVからのクオンツ解析 ---
print("\n🛡️ 【ロジック・防衛壁の確認 (CSV解析)】")

if 'Time' in df.columns:
    df['datetime'] = pd.to_datetime(df['Time'])
    # JST 01:00-08:59 を UTC に換算すると 16:00-23:59 (前日)
    # replay_backtester.py では JST で判定している。
    # ログにある Time は UTC のはず。
    # 日本時間 01-09時 (UTC 16-24時) をチェック
    forbidden = df[(df['datetime'].dt.hour >= 16) | (df['datetime'].dt.hour < 0)] # これは不正確か
    # 正確には JST = UTC + 9
    df['jst'] = df['datetime'] + pd.Timedelta(hours=9)
    forbidden_jst = df[(df['jst'].dt.hour >= 1) & (df['jst'].dt.hour <= 8)]
    
    if len(forbidden_jst) == 0:
        print(" => ✅ 完璧です。JST 01:00-08:59 の魔の時間帯エントリーは0件です。")
    else:
        print(f" => ⚠️ 警告: {len(forbidden_jst)}件の禁止時間帯(JST)エントリーが漏れています。")

if all(c in df.columns for c in ['Direction', 'Price', 'SL_Price', 'TP_Price']):
    def calc_rr(row):
        try:
            if row['SL_Price'] == 0 or row['TP_Price'] == 0: return np.nan
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
