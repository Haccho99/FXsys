import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

log_file = "logs/system_20260802_220704_26280.log"
with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

print(f"Total lines in {log_file}: {len(lines)}")
print("\n--- First 20 lines ---")
for l in lines[:20]:
    print(l.strip())

print("\n--- Last 30 lines ---")
for l in lines[-30:]:
    print(l.strip())

# Check unique log messages / levels / errors
errors = [l for l in lines if "ERROR" in l or "CRITICAL" in l or "WARNING" in l or "Signal" in l or "Trade" in l or "Position" in l]
print(f"\nTotal interesting lines (ERROR/CRITICAL/WARNING/Signal/Trade): {len(errors)}")
for l in errors[:50]:
    print(l.strip())
