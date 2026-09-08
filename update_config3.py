import json
from pathlib import Path

config_path = Path("config.json")
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

new_pairs = ["AUD_USD", "EUR_USD", "GBP_USD"]
all_pairs = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "AUD_USD", "EUR_USD", "GBP_USD"]

# Fix risk_management -> margin_rates
if "risk_management" in config:
    if "margin_rates" not in config["risk_management"]:
        config["risk_management"]["margin_rates"] = {}
    config["risk_management"]["margin_rates"]["AUD_USD"] = 0.04
    config["risk_management"]["margin_rates"]["EUR_USD"] = 0.04
    config["risk_management"]["margin_rates"]["GBP_USD"] = 0.05

# Fix indicators -> di_diff_threshold
if "indicators" in config:
    if "di_diff_threshold" not in config["indicators"]:
        config["indicators"]["di_diff_threshold"] = {}
    config["indicators"]["di_diff_threshold"]["AUD_USD"] = 10
    config["indicators"]["di_diff_threshold"]["EUR_USD"] = 10
    config["indicators"]["di_diff_threshold"]["GBP_USD"] = 10

# Clean up accidental "risk" key if exists
if "risk" in config:
    del config["risk"]

# Fix tasks -> short_term_optimizer
if "tasks" in config and "short_term_optimizer" in config["tasks"]:
    if "pairs_to_optimize" not in config["tasks"]["short_term_optimizer"]:
        config["tasks"]["short_term_optimizer"]["pairs_to_optimize"] = []
    # Make sure all pairs are present, including USD_JPY
    for pair in all_pairs:
        if pair not in config["tasks"]["short_term_optimizer"]["pairs_to_optimize"]:
            config["tasks"]["short_term_optimizer"]["pairs_to_optimize"].append(pair)

# Fix tasks -> walk_forward_analyzer (wait, is it 'optimizer' or 'walk_forward_analyzer' or 'wfa'?)
# Looking at the previous output, it is 'walk_forward_analyzer' but wait, there is an 'optimizer' key too!
if "tasks" in config:
    if "walk_forward_analyzer" in config["tasks"]:
        if "pairs_to_analyze" in config["tasks"]["walk_forward_analyzer"]:
            for pair in all_pairs:
                if pair not in config["tasks"]["walk_forward_analyzer"]["pairs_to_analyze"]:
                    config["tasks"]["walk_forward_analyzer"]["pairs_to_analyze"].append(pair)
        # Maybe it also has pairs_to_optimize?
        if "pairs_to_optimize" in config["tasks"]["walk_forward_analyzer"]:
            for pair in all_pairs:
                if pair not in config["tasks"]["walk_forward_analyzer"]["pairs_to_optimize"]:
                    config["tasks"]["walk_forward_analyzer"]["pairs_to_optimize"].append(pair)
                    
    if "optimizer" in config["tasks"]:
        if "pairs_to_optimize" in config["tasks"]["optimizer"]:
            for pair in all_pairs:
                if pair not in config["tasks"]["optimizer"]["pairs_to_optimize"]:
                    config["tasks"]["optimizer"]["pairs_to_optimize"].append(pair)

with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=4, ensure_ascii=False)

print("config.json fixed successfully.")
