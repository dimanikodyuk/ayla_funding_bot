from decimal import Decimal
from dataclasses import dataclass
D=lambda x:Decimal(str(x))
@dataclass
class Config:
    initial_balance:Decimal=D('100')
    margin:Decimal=D('10')
    leverage:Decimal=D('2')
    max_positions:int=2
    daily_loss_limit:Decimal=D('2')
    fee_rate:Decimal=D('0.0005')
    stop_pct:Decimal=D('0.006')
    max_minutes:int=30
    min_volume:Decimal=D('5000000')
    max_spread_pct:Decimal=D('0.08')
def premium(mark,index):
    if D(index)<=0:raise ValueError('invalid index')
    return (D(mark)/D(index)-1)*100
def zscore(value,history,min_samples=1152):
    if len(history)<min_samples:return None # configurable 80% coverage of chosen window
    values=[float(v) for v in history]
    mean=sum(values)/len(values)
    variance=sum((v-mean)**2 for v in values)/len(values)
    return (float(value)-mean)/max(variance**0.5,0.02)
def trade_pnl(side,qty,entry,exit):return (D('1') if side=='LONG' else D('-1'))*D(qty)*(D(exit)-D(entry))
def funding_cashflow(side,notional,rate):return (D('-1') if side=='LONG' else D('1'))*D(notional)*D(rate)
def signal(p,z,volume,spread,momentum,funding_minutes,valid=True,delta=None):
    if not valid or z is None or D(volume)<D('5000000') or D(spread)>D('0.08') or funding_minutes<5:return None
    if delta is None:return None
    if z>=2 and D(delta)>=D('0.15') and momentum<=0.4:return 'SHORT'
    if z<=-2 and D(delta)<=D('-0.15') and momentum>=-0.4:return 'LONG'
    return None
