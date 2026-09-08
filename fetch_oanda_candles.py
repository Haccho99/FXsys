"""
fetch_oanda_candles.py (v3 - Core API Integrated)
システムの config.json と oanda_api.py を経由して、
指定した期間（日本時間）・時間足のOHLCデータを一括取得するツール。
"""
import sys
from pathlib import Path
import requests
import pandas as pd
import time

# プロジェクトルートのパス設定（coreモジュールを読み込むため）
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from core.oanda_api import get_active_token, build_oanda_url

# =========================================================
# 📝 運用者設定エリア
# =========================================================
INSTRUMENTS = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"]
GRANULARITY = "M1"      # 時間足 (M1: 1分足, M15: 15分足, H1: 1時間足)

# 取得したい期間を【日本時間 (JST)】で指定してください
START_TIME_JST = "2026-06-04 06:00:00"
END_TIME_JST   = "2026-06-04 10:00:00"
# =========================================================

def convert_jst_to_utc(jst_str):
    """日本時間の文字列をOANDA API用のUTC(RFC3339)文字列に自動変換する"""
    dt = pd.to_datetime(jst_str)
    if dt.tzinfo is None:
        dt = dt.tz_localize('Asia/Tokyo')
    return dt.tz_convert('UTC').strftime('%Y-%m-%dT%H:%M:%SZ')

def fetch_candles(instrument, start_utc, end_utc):
    print(f"\n--- OANDA データ取得開始: {instrument} ---")
    
    # coreモジュールからトークンとエンドポイントURLを動的に取得
    token = get_active_token()
    if not token:
        print("❌ エラー: config.json にOANDAのトークンが設定されていません。")
        return

    # oanda_api.py の関数を使って正しいURLを生成
    url = build_oanda_url(f"instruments/{instrument}/candles")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept-Datetime-Format": "RFC3339"
    }

    params = {
        "price": "M",  # Mid (仲値) 
        "granularity": GRANULARITY,
        "from": start_utc,
        "to": end_utc
    }

    # APIリクエスト
    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        print(f"❌ エラー発生 ({instrument}): {response.status_code}")
        print(response.json())
        return

    data = response.json()
    candles = data.get("candles", [])

    if not candles:
        print(f"⚠️ {instrument} の指定期間データは見つかりませんでした。")
        return

    # データのパースと整形
    records = []
    for candle in candles:
        if not candle["complete"]:
            continue
        
        records.append({
            "Time (UTC)": candle["time"],
            "Open": float(candle["mid"]["o"]),
            "High": float(candle["mid"]["h"]),
            "Low":  float(candle["mid"]["l"]),
            "Close": float(candle["mid"]["c"]),
            "Volume": candle["volume"]
        })

    # DataFrame化と日本時間(JST)列の追加
    df = pd.DataFrame(records)
    df["Time (JST)"] = pd.to_datetime(df["Time (UTC)"]).dt.tz_convert('Asia/Tokyo').dt.strftime('%Y-%m-%d %H:%M:%S')
    df = df[["Time (JST)", "Time (UTC)", "Open", "High", "Low", "Close", "Volume"]]

    output_file = f"{instrument}_{GRANULARITY}_validation_data.csv"
    df.to_csv(output_file, index=False)
    print(f"✅ 取得成功！ {len(df)} 件のデータを '{output_file}' に保存しました。")

if __name__ == "__main__":
    print(f"ターゲット期間 (JST): {START_TIME_JST} 〜 {END_TIME_JST}")
    print(f"取得時間足: {GRANULARITY}")
    
    # 日本時間からUTCへ変換
    start_utc = convert_jst_to_utc(START_TIME_JST)
    end_utc   = convert_jst_to_utc(END_TIME_JST)
    
    # 順番にデータ取得
    for inst in INSTRUMENTS:
        fetch_candles(inst, start_utc, end_utc)
        time.sleep(1) # レート制限回避
        
    print("\n🎉 すべての通貨ペアのデータ取得が完了しました！")