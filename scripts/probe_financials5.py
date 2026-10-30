import json
import akshare as ak

df = ak.stock_financial_report_sina(stock="sh600519", symbol="利润表")
cols = ["报告日", "公告日期", "更新日期", "数据源", "类型"]
print(df[cols].head(6).to_string())

for api, kwargs in [
    ("stock_individual_basic_info_xq", {"symbol": "SH600519"}),
]:
    try:
        frame = getattr(ak, api)(**kwargs)
        print(api, "ok", len(frame))
        print(frame.head(12).to_string()[:800])
    except Exception as exc:
        print(api, "FAILED", type(exc).__name__, str(exc)[:200])
