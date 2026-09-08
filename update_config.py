import json
from pathlib import Path

config_path = Path("config.json")
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

new_pairs = ["AUD_USD", "EUR_USD", "GBP_USD"]

# 1. instruments
if "instruments" not in config:
    config["instruments"] = {
        "USD_JPY": {"pip_value": 0.01},
        "EUR_JPY": {"pip_value": 0.01},
        "GBP_JPY": {"pip_value": 0.01},
        "AUD_JPY": {"pip_value": 0.01}
    }
for pair in new_pairs:
    config["instruments"][pair] = {"pip_value": 0.0001}

# 2. trading.symbols
if "trading" in config:
    if "symbols" in config["trading"]:
        for pair in new_pairs:
            if pair not in config["trading"]["symbols"]:
                config["trading"]["symbols"].append(pair)
    if "trade_enabled_pairs" in config["trading"]:
        for pair in new_pairs:
            config["trading"]["trade_enabled_pairs"][pair] = True

# 3. system.active_windows
if "system" in config and "active_windows" in config["system"]:
    for pair in new_pairs:
        if pair not in config["system"]["active_windows"]:
            config["system"]["active_windows"][pair] = []

# 4. risk (margin_rates, di_diff_threshold)
if "risk" in config:
    if "margin_rates" in config["risk"]:
        config["risk"]["margin_rates"]["AUD_USD"] = 0.04
        config["risk"]["margin_rates"]["EUR_USD"] = 0.04
        config["risk"]["margin_rates"]["GBP_USD"] = 0.05
    if "di_diff_threshold" in config["risk"]:
        config["risk"]["di_diff_threshold"]["AUD_USD"] = 10
        config["risk"]["di_diff_threshold"]["EUR_USD"] = 10
        config["risk"]["di_diff_threshold"]["GBP_USD"] = 10

# 5. tasks
if "tasks" in config:
    if "wfa" in config["tasks"]:
        if "pairs_to_optimize" in config["tasks"]["wfa"]:
            for pair in new_pairs:
                if pair not in config["tasks"]["wfa"]["pairs_to_optimize"]:
                    config["tasks"]["wfa"]["pairs_to_optimize"].append(pair)
        if "pairs_to_analyze" in config["tasks"]["wfa"]:
            for pair in new_pairs:
                if pair not in config["tasks"]["wfa"]["pairs_to_analyze"]:
                    config["tasks"]["wfa"]["pairs_to_analyze"].append(pair)
    if "data_downloader" in config["tasks"]:
        if "pairs" in config["tasks"]["data_downloader"]:
            for pair in new_pairs:
                if pair not in config["tasks"]["data_downloader"]["pairs"]:
                    config["tasks"]["data_downloader"]["pairs"].append(pair)
    if "optimizer" in config["tasks"]:
        if "pairs_to_optimize" in config["tasks"]["optimizer"]:
            for pair in new_pairs:
                if pair not in config["tasks"]["optimizer"]["pairs_to_optimize"]:
                    config["tasks"]["optimizer"]["pairs_to_optimize"].append(pair)

# 6. strategy_filters (add defaults)
if "strategy_filters" in config:
    for pair in new_pairs:
        if pair not in config["strategy_filters"]:
            config["strategy_filters"][pair] = {
                "use_ema_order": True,
                "use_close_ema50": False,
                "use_ema_slope": False,
                "use_adx": True,
                "adx_threshold": 20
            }

with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=4, ensure_ascii=False)

print("config.json updated successfully.")
