from types import SimpleNamespace
from ayla.intelligence import classify, aggregate, premium_series, daily_report
from ayla.db import connect
from ayla.core import Config

def test_reasons():
    cfg=Config(); c={'symbol':'BTC-USDT','z':None,'premium':0.2}
    assert classify(c,None,{},-1,cfg)=='WARMING_UP'
    c['z']=2.5
    assert classify(c,None,{'delta':0.3},-1,cfg)=='NO_BOOK'
    assert classify(c,{'ts':100},{'delta':0.3,'spread':0.01,'momentum':0},-1,cfg,now=100)=='FUNDING_UNKNOWN'
    assert classify(c,{'ts':100},{'delta':0.3,'spread':0.01,'momentum':0},100,cfg,now=100)=='READY'

def test_aggregate_idempotent(tmp_path):
    db=connect(str(tmp_path/'a.sqlite'))
    x=[{'symbol':'BTC-USDT','reason':'Z_TOO_LOW','z':0.2,'premium':0.1}]
    aggregate(db,x,now=1000);aggregate(db,x,now=1000)
    assert db.execute('SELECT COUNT(*) FROM signal_diagnostics').fetchone()[0]==1

def test_premium_series(tmp_path):
    import time
    db=connect(str(tmp_path/'b.sqlite'));now=int(time.time())
    with db:db.execute('INSERT INTO samples VALUES(?,?,?,?,?)',('BTC-USDT',now-20,0.2,100,99))
    assert premium_series(db,'BTC-USDT')[0]['premium']==0.2
    assert daily_report(db)['closed_trades']==0
