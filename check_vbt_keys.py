import vectorbt as vbt
import pandas as pd
import numpy as np

# Create a dummy portfolio
price = pd.Series([100, 101, 102, 101, 100], name='close')
entries = pd.Series([True, False, False, False, False])
exits = pd.Series([False, False, False, True, False])
portfolio = vbt.Portfolio.from_signals(price, entries, exits, freq='15min')

stats = portfolio.stats()
print("VectorBT Stats Index Labels:")
for label in stats.index:
    print(f"'{label}'")

stats_dict = stats.to_dict()
print("\nDictionary Keys:")
for key in stats_dict.keys():
    print(f"'{key}'")
