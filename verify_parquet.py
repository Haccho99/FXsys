import polars as pl
from pathlib import Path
import sys
from datetime import datetime, timedelta

def get_expected_trading_days(start_dt: datetime, end_dt: datetime) -> int:
    """
    指定された期間内の予想される取引日数（土日と元日を除く）を計算します。
    """
    current_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    trading_days = 0
    while current_dt <= end_dt:
        # 土日を除外 (weekday() は月曜が0、日曜が6)
        if current_dt.weekday() < 5: # 0-4 は月曜から金曜
            # 元日を除外
            if not (current_dt.month == 1 and current_dt.day == 1):
                trading_days += 1
        current_dt += timedelta(days=1)
    return trading_days

def verify_parquet_data(file_path: Path):
    """
    指定されたParquetファイルの健全性を検証し、内容を詳細に表示します。
    """
    if not file_path.exists():
        print(f"エラー: ファイルが見つかりません - {file_path}")
        return

    try:
        df = pl.read_parquet(file_path)
        
        if df.is_empty():
            print(f"ファイルは空です: {file_path}")
            return
            
        print(f"--- ファイル: {file_path} の内容検証 ---\n")
        print(f"総行数: {len(df)}")
        
        # スキーマ (カラム名とデータ型) の表示
        print("\n--- スキーマ (カラム名とデータ型) ---")
        print(df.schema)

        # 時間範囲の確認
        if "time" in df.columns:
            min_time = df["time"].min()
            max_time = df["time"].max()
            print(f"\n--- 時間範囲 ---\n")
            print(f"開始時刻: {min_time}")
            print(f"終了時刻: {max_time}")
        else:
            print("\n'time' カラムが見つかりません。")
            return # timeカラムがない場合はこれ以上進めない

        # データ総数の表示と警告 (土日・祝日考慮版)
        row_count = len(df)
        
        # データ期間の長さを計算 (年単位)
        # PolarsのTimestampをPythonのdatetimeに変換は不要、min()/max()は直接datetimeを返す
        start_dt_py = min_time
        end_dt_py = max_time
        
        # 期間内の予想される取引日数を計算
        expected_trading_days = get_expected_trading_days(start_dt_py, end_dt_py)
        
        # 15分足なので、1日あたり 24時間 * 4 = 96本のロウソク足
        expected_rows = expected_trading_days * 96 
        
        print(f"\nデータ総数: {row_count}件")
        print(f"予想される取引日数: {expected_trading_days}日")
        print(f"予想されるデータ総数（土日・元日考慮）: {expected_rows}件")

        # 実際のデータ数が予想の8割未満なら警告
        if expected_rows > 0 and row_count < expected_rows * 0.8:
             print("\n警告: データ数が予想される総数に対して少ないようです。")
             print("データが不完全か、長期間の欠損がある可能性があります。")
        elif expected_rows == 0:
            print("\n警告: 予想されるデータ総数が0です。期間設定を確認してください。")


        # データのプレビュー (先頭と末尾の数行)
        print("\n--- データプレビュー (先頭5行) ---")
        print(df.head(5))
        print("\n--- データプレビュー (末尾5行) ---")
        print(df.tail(5))

    except Exception as e:
        print(f"ファイルの読み込み中にエラーが発生しました: {e}")

if __name__ == "__main__":
    # コマンドライン引数からファイルパスを取得
    # 例: python verify_parquet.py data/GBP_JPY/M15_historical.parquet
    if len(sys.argv) > 1:
        target_file_path = Path(sys.argv[1])
    else:
        # 引数がない場合のデフォルトファイルパス
        # プロジェクトのルートディレクトリからの相対パスを指定してください
        # 例: data/GBP_JPY/M15_historical.parquet
        print("ファイルパスが指定されていません。デフォルトのファイルを使用します。")
        print("使用例: python verify_parquet.py data/GBP_JPY/M15_historical.parquet")
        target_file_path = Path("data/GBP_JPY/M15_historical.parquet") # ここをデフォルトのファイルパスに設定してください
    
    # プロジェクトのルートディレクトリをPythonパスに追加
    # これにより、スクリプトがどこから実行されても相対パスが正しく解決されます。
    # (このスクリプト自体がプロジェクトルートにあるため、実際には不要かもしれませんが、汎用性のため残します)
    project_root = Path(__file__).resolve().parents[0]
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))

    verify_parquet_data(target_file_path)