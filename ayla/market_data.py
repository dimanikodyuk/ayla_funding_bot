"""BingX ticker snapshot validation and fresh-book selection (paper only)."""
import time
from .core import D

MAX_QUOTE_AGE_SECONDS = 10

def ticker_snapshot(rows, received_at=None):
    """Validate each ticker independently; never reuse a stale/invalid book."""
    ts=int(time.time()) if received_at is None else int(received_at)
    quotes={}; volumes={}; rejected=0
    for row in rows:
        if not isinstance(row,dict):
            rejected+=1; continue
        sym=row.get('symbol')
        if not isinstance(sym,str) or not sym.endswith('-USDT'):
            continue
        try:
            vol=D(row.get('quoteVolume') or row.get('quoteVolume24h') or '0')
            if vol>=0: volumes[sym]=vol
            bid=D(row.get('bidPrice') or row.get('bid') or row.get('bestBidPrice') or '0')
            ask=D(row.get('askPrice') or row.get('ask') or row.get('bestAskPrice') or '0')
            if bid<=0 or ask<bid: rejected+=1; continue
            quotes[sym]={'bid':str(bid),'ask':str(ask),'ts':ts,'source':'ticker'}
        except (TypeError,ValueError,ArithmeticError):
            rejected+=1
    return quotes,volumes,rejected

def fresh_quote(quotes,symbol,now=None,max_age=MAX_QUOTE_AGE_SECONDS):
    now=int(time.time()) if now is None else int(now)
    q=quotes.get(symbol)
    if not q: return None
    age=now-int(q.get('ts',0))
    if age<0 or age>max_age:return None
    try:
        bid=D(q['bid']);ask=D(q['ask'])
        if bid<=0 or ask<bid:return None
    except (KeyError,TypeError,ValueError,ArithmeticError):return None
    return q

def spread_pct(q):
    bid=D(q['bid']);ask=D(q['ask'])
    return float((ask-bid)/((ask+bid)/2)*100)
