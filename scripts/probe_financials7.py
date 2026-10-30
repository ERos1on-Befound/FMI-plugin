import traceback
from financial_intelligence.config import load_settings
from financial_intelligence.providers.akshare import AkshareFinancialProvider
from financial_intelligence.utils.symbol_mapper import map_symbol

spec = map_symbol("00700.HK")
provider = AkshareFinancialProvider(load_settings())
try:
    rows = provider.get_financials(spec, period_type="annual", limit=1)
    print("ok", len(rows))
except Exception:
    traceback.print_exc()
