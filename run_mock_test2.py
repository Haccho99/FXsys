import os
import glob
import pandas as pd
import subprocess
import shutil

data_dir = r"C:\WealthSystem\FXsys\data\history"
backup_dir = r"C:\WealthSystem\FXsys\data_backup_history"

# If data_dir doesn't exist, maybe it's in data/USD_JPY/ ?
data_dir_base = r"C:\WealthSystem\FXsys\data"
backup_dir_base = r"C:\WealthSystem\FXsys\data_backup"

parquet_files = glob.glob(os.path.join(data_dir_base, "**", "*.parquet"), recursive=True)
print(f"Found {len(parquet_files)} parquet files. Adding mock spread...")

for f in parquet_files:
    try:
        df = pd.read_parquet(f)
        if "spread" not in df.columns:
            if "JPY" in f:
                df["spread"] = 0.02
            else:
                df["spread"] = 0.0002
            df.to_parquet(f, index=False)
    except Exception as e:
        print(f"Error on {f}: {e}")

print("Mock spread added. Running replay_backtester.py...")
subprocess.run([r"C:\WealthSystem\.venv/bin/python", r"C:\WealthSystem\FXsys\replay_backtester.py"], cwd=r"C:\WealthSystem\FXsys")

print("Done. To analyze, run analyze_comprehensive.py.")
