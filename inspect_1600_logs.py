import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

log_file = "logs/system_20260802_220704_26280.log"
with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

recent_lines = [l for l in lines if "2026-08-03 16:" in l or "2026-08-03 15:" in l or "2026-08-03 11:" in l]
print(f"Total lines in afternoon: {len(recent_lines)}")
for l in recent_lines:
    print(l.strip())
