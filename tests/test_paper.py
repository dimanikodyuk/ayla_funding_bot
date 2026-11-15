import sqlite3
from decimal import Decimal
from ayla.core import Config,signal,D
from ayla.trading import PaperEngine

def setup():
    from ayla.db import SCHEMA
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript(SCHEMA)
    return db,PaperEngine(db,Config())

def test_open_close_short():
    db,e=setup();tid,status=e.open('BTC-USDT','SHORT','100','100.1',ts=100)
    assert status=='OPENED'
    pnl=e.close(e.positions()[0],'99','99.1','NORMALIZED',ts=200)
    assert pnl>0 and len(e.positions())==0
    assert e.close({'id':tid,'side':'SHORT','qty':'0.2','entry':'100','fee_open':'0.01','funding':'0'},'99','99.1','REPEAT') is None

def test_no_real_orders_and_risk():
    db,e=setup()
    assert not hasattr(e,'place_live_order')
    assert e.open('BTC-USDT','LONG','100','101')[1]=='OPENED'
    assert e.open('BTC-USDT','LONG','100','101')[1]=='ALREADY_OPEN'
    assert e.open('ETH-USDT','LONG','100','101')[1]=='OPENED'
    assert e.open('SOL-USDT','LONG','100','101')[1]=='MAX_POSITIONS'

def test_signal_requires_delta():
    assert signal('.2',3,6000000,.01,0,20) is None
    assert signal('.2',3,6000000,.01,0,20,delta='.16')=='SHORT'
