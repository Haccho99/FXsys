import sys
import pandas as pd
from unittest.mock import patch
import asyncio

original_read_parquet = pd.read_parquet

def mocked_read_parquet(path, *args, **kwargs):
    df = original_read_parquet(path, *args, **kwargs)
    # Add a fixed 2.0 pips spread
    if "JPY" in str(path):
        df["spread"] = 0.02
    else:
        df["spread"] = 0.0002
    return df

async def main():
    with patch('pandas.read_parquet', side_effect=mocked_read_parquet):
        import replay_backtester
        await replay_backtester.main()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
