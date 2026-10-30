"""Probe the AKShare statement shapes we depend on (annual + quarterly)."""
import json
import akshare as ak

def probe_cn():
    for sym in ("利润表", "资产负债表", "现金流量表"):
        df = ak.stock_financial_report_sina(stock="sh600519", symbol=sym)
        dates = [str(v) for v in df["报告日"].tolist()][:6]
        print(sym, "rows", len(df), "dates", dates)
        row = df.iloc[0]
        meta = {k: row.get(k) for k in ("数据源","是否审计","公告日期","币种","类型","更新日期")}
        print("  meta", json.dumps(meta, ensure_ascii=False, default=str))

def probe_hk():
    df = ak.stock_financial_hk_report_em(stock="00700", symbol="利润表", indicator="年度")
    print("hk annual rows", len(df), "cols", list(df.columns))
    print(df[["REPORT_DATE","DATE_TYPE_CODE","FISCAL_YEAR","STD_ITEM_NAME","AMOUNT"]].head(3).to_string())
    try:
        q = ak.stock_financial_hk_report_em(stock="00700", symbol="利润表", indicator="报告期")
        print("hk quarterly rows", len(q), "date types", sorted(q["DATE_TYPE_CODE"].astype(str).unique())[:6])
        print(q[["REPORT_DATE","DATE_TYPE_CODE"]].drop_duplicates().head(8).to_string())
    except Exception as exc:
        print("hk quarterly failed:", type(exc).__name__, str(exc)[:200])

probe_cn()
probe_hk()
