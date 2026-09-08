import sys
import io
import os
import glob
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from pathlib import Path
# FXsys フォルダの中にある logs フォルダの絶対パスを強制生成する
log_dir = str(Path(__file__).resolve().parent / "logs")
print("--- All files in logs/ ---")
for f in sorted(glob.glob(f"{log_dir}/*")):
    stat = os.stat(f)
    mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    print(f"{f:45s} | Size: {stat.st_size:10d} bytes | Modified: {mtime}")

# Check running python processes
import psutil
print("\n--- Running Python Processes ---")
for p in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
    try:
        cmd = p.info['cmdline']
        if cmd and any('python' in c.lower() for c in cmd):
            ctime = datetime.fromtimestamp(p.info['create_time']).strftime('%Y-%m-%d %H:%M:%S')
            print(f"PID {p.info['pid']:6d} | Created: {ctime} | Cmd: {' '.join(cmd)}")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
