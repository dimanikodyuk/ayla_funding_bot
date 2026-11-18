"""Read-only observational replay: mark-price proxies, not executable fills."""
from .core import D, trade_pnl

def replay(db,cfg):
    trades=[dict(r) for r in db.execute("SELECT * FROM trades WHERE status='CLOSED' ORDER BY id DESC LIMIT 100")]
    horizons=(0,30,60,120)
    result=[]
    for seconds in horizons:
        pnl=[]; covered=0
        for t in trades:
            ts=t['opened']+seconds
            # Baseline 0 = actual recorded exit; others = first market sample at/after target.
            if seconds==0:
                if t['pnl'] is not None:pnl.append(float(D(t['pnl'])));covered+=1
                continue
            row=db.execute('SELECT mark,ts FROM samples WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts LIMIT 1',
                           (t['symbol'],ts,ts+45)).fetchone()
            if row is None:continue
            # Mark-price proxy relative to actual entry; ignores executable exit spread.
            px=D(row['mark']);qty=D(t['qty'])
            estimate=trade_pnl(t['side'],qty,D(t['entry']),px)-D(t['fee_open'])-qty*px*cfg.fee_rate
            pnl.append(float(estimate));covered+=1
        result.append({'hold_seconds':seconds,'observations':covered,
                       'mean_net_usdt':round(sum(pnl)/len(pnl),6) if pnl else None,
                       'sum_net_usdt':round(sum(pnl),6) if pnl else None,
                       'price_basis':'actual_bid_ask_exit' if seconds==0 else 'mark_price_proxy'})
    return {'results':result,'closed_trades_considered':len(trades),
      'limitations':['0s row is actual recorded exit, not an immediate-exit counterfactual.',
                     '30/60/120s rows use future mark-price samples, not bid/ask execution.',
                     'No stop-loss or time-limit replay, slippage, funding or intrabar path.',
                     'Observational only; cannot establish a profitable exit rule.']}
