import time

import akshare as ak

t0 = time.time()
try:
    df = ak.stock_info_a_code_name()
    print("stock_info_a_code_name ok", len(df), "cols", list(df.columns), f"{time.time()-t0:.2f}s")
    print(df.head(3).to_string())
    hit = df[df["code"] == "600519"]
    print("600519 ->", hit.to_dict("records"))
except Exception as exc:
    print("FAILED", type(exc).__name__, str(exc)[:300])
