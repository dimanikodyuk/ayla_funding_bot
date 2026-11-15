from decimal import Decimal
from ayla.core import premium,zscore,trade_pnl,funding_cashflow,signal

def test_premium():assert premium('100.3','100')==Decimal('0.300')
def test_pnl():
    assert trade_pnl('SHORT','0.2','100','99.6')==Decimal('0.08')
    assert trade_pnl('LONG','0.2','100','99.6')==Decimal('-0.08')
def test_funding():
    assert funding_cashflow('SHORT',20,'0.001')==Decimal('0.020')
    assert funding_cashflow('LONG',20,'0.001')==Decimal('-0.020')
def test_zscore():assert zscore(0,[0]*13824)==0
def test_signal():
    assert signal(.2,2.5,6000000,.03,0,60,delta=.16)=='SHORT'
    assert signal(-.2,-2.5,6000000,.03,0,60,delta=-.16)=='LONG'
    assert signal(.2,2.5,6000000,.1,0,60) is None
