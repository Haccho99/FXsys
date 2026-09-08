
import pandas as pd
import json
import glob
import os

print("=== 1. 設定ファイルの確認 ===")
config_path = "config.json"
if os.path.exists(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        risk_cfg = config.get("risk_management", {})
        tp_trail = risk_cfg.get("tp_trailing", {})
        print(f"  enable_doten: {risk_cfg.get('enable_doten', 'Not Found')}")
        print(f"  use_atr_trail: {tp_trail.get('use_atr_trail', 'Not Found')}")
        print(f"  tp_trailing enabled: {tp_trail.get('enabled', 'Not Found')}")
else:
    print(f"  {config_path} が見つかりません。")

print("\n=== 2. 取引ログの決済理由集計 ===")
log_files = glob.glob("logs/virtual_trade_log_*.csv")
if not log_files:
    print("取引ログが見つかりません。")
else:
    latest_log = max(log_files, key=os.path.getctime)
    print(f"Latest log: {latest_log}")
    df_trades = pd.read_csv(latest_log)
    if 'reason' in df_trades.columns:
        print(df_trades['reason'].value_counts().to_string())
    else:
        print("Reason column not found in trades log.")

print("\n=== 3. ドテン不発の原因究明 (decision_log) ===")
decision_file = "data/reports/decision_log_USD_JPY.csv"
if os.path.exists(decision_file):
    df_dec = pd.read_csv(decision_file)
    df_dec['Time'] = pd.to_datetime(df_dec['Time'], utc=True)
    # 2026/05/11 17:30 以降を抽出 (ユーザー指定は17:30だが、前回のログでは08:30だったため広めに取る)
    mask = df_dec['Time'] >= pd.to_datetime("2026-05-11 08:30", utc=True)
    df_period = df_dec[mask].reset_index(drop=True)
    
    doten_chances = 0
    for i in range(1, len(df_period)):
        # MACDヒストグラムがプラスからマイナスへ転換（ショートの兆候）
        if df_period.loc[i-1, 'MACD_Hist'] > 0 and df_period.loc[i, 'MACD_Hist'] < 0:
            doten_chances += 1
            if doten_chances <= 5: # 最初の5回を表示
                print(f"  [ドテン候補 {doten_chances}] Time: {df_period.loc[i, 'Time']}")
                print(f"    Action: {df_period.loc[i, 'Action']} | ADX: {df_period.loc[i, 'ADX']} | H1_Trend: {df_period.loc[i, 'H1_Trend']} | RSI: {df_period.loc[i, 'RSI']}")
    print(f"  -> 合計 {doten_chances} 回のショートサイン兆候がありましたが、ドテンは発動しませんでした。")
else:
    print("decision_log が見つかりません。")
