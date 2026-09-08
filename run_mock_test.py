import os
import glob
import pandas as pd
import subprocess
import shutil

data_dir = r"C:\WealthSystem\FXsys\data"
backup_dir = r"C:\WealthSystem\FXsys\data_backup"

# Backup original data
if not os.path.exists(backup_dir):
    shutil.copytree(data_dir, backup_dir, dirs_exist_ok=True)
    print("Original data backed up.")

parquet_files = glob.glob(os.path.join(data_dir, "**", "*.parquet"), recursive=True)
print(f"Found {len(parquet_files)} parquet files. Adding mock spread...")

for f in parquet_files:
    df = pd.read_parquet(f)
    # 2 pips spread
    if "JPY" in f:
        df["spread"] = 0.02
    else:
        df["spread"] = 0.0002
    df.to_parquet(f, index=False)

print("Mock spread added. Running generate_trade_report.py...")
subprocess.run(["python", r"C:\WealthSystem\FXsys\generate_trade_report.py"], cwd=r"C:\WealthSystem\FXsys")

print("Running calc_pf_mdd.py for new results...")
subprocess.run(["python", r"C:\WealthSystem\FXsys\calc_pf_mdd.py"], cwd=r"C:\WealthSystem\FXsys")

print("Restoring original data...")
shutil.copytree(backup_dir, data_dir, dirs_exist_ok=True)
print("Done.")
