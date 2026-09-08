import json
from pathlib import Path

config_path = Path("config.json")
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

new_pairs = ["AUD_USD", "EUR_USD", "GBP_USD"]

if "tasks" in config:
    # Fix short_term_optimizer
    if "short_term_optimizer" in config["tasks"]:
        if "pairs_to_optimize" in config["tasks"]["short_term_optimizer"]:
            for pair in new_pairs:
                if pair not in config["tasks"]["short_term_optimizer"]["pairs_to_optimize"]:
                    config["tasks"]["short_term_optimizer"]["pairs_to_optimize"].append(pair)
                    
    # Fix walk_forward_analyzer
    if "walk_forward_analyzer" in config["tasks"]:
        if "pairs_to_analyze" in config["tasks"]["walk_forward_analyzer"]:
            for pair in new_pairs:
                if pair not in config["tasks"]["walk_forward_analyzer"]["pairs_to_analyze"]:
                    config["tasks"]["walk_forward_analyzer"]["pairs_to_analyze"].append(pair)

# Fix per_symbol_overrides
if "per_symbol_overrides" in config:
    for pair in new_pairs:
        if pair not in config["per_symbol_overrides"]:
            config["per_symbol_overrides"][pair] = {}

# Fix best_params
if "best_params" in config:
    for pair in new_pairs:
        if pair not in config["best_params"]:
            config["best_params"][pair] = {}

with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=4, ensure_ascii=False)

print("config.json updated successfully (part 2).")
