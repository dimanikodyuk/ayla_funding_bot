from types import SimpleNamespace
from ayla.signal_validation import evaluate, threshold_preview

def test_all_reasons():
    c={'symbol':'BTC-USDT','z':2.3}
    st={'delta':0.04,'spread':0.1,'momentum':0.5}
    cfg=SimpleNamespace(max_spread_pct=0.08,max_minutes=30)
    r=evaluate(c,None,st,-1,cfg,now=100)
    assert {'PREMIUM_DELTA','NO_BOOK','FUNDING_UNKNOWN','MOMENTUM'}.issubset(r['failures'])

def test_thresholds_read_only():
    x=threshold_preview([{'symbol':'BTC-USDT','z':2.2,'delta':0.06}])
    assert x[0]['count']==0 and x[2]['count']==1
