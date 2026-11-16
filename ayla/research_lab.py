"""Read-only, strictly chronological premium research. NEVER sends orders."""
import math
import time
from collections import deque

WINDOWS=(60,360,1440)
HORIZONS=(5,15,30)

def minute_data(db,symbol,now=None):
    now=int(time.time()) if now is None else int(now)
    if not (1<=len(symbol)<=35 and all(c.isupper() or c.isdigit() or c=='-' for c in symbol)):
        raise ValueError('Invalid symbol')
    # Bounded 72h range, bucket samples by minute in SQL; one observation per minute.
    rows=db.execute('''SELECT ts/60 AS minute, AVG(premium) AS p,
                      AVG(mark) AS mark, AVG(idx) AS idx
                      FROM samples WHERE symbol=? AND ts>=? AND ts<=?
                      GROUP BY ts/60 ORDER BY minute''',(symbol,now-72*3600,now)).fetchall()
    return [(int(r['minute']),float(r['p']),float(r['mark']),float(r['idx'])) for r in rows]

def analyze(rows,window,fee_rate=0.0005,slippage_rate=0.0002):
    """Walk-forward proxy: entry at next minute mark, exit at +5/+15/+30 min.
    Mark is NOT executable; results are hypothetical, not a real backtest.
    Missing minutes are never filled. Historical spread/funding not modeled.
    """
    by_min={r[0]:r for r in rows}
    minutes=sorted(by_min)
    q=deque()
    results={h:[] for h in HORIZONS}
    signals=0; eligible=0; latest=None
    for minute in minutes:
        while q and q[0][0]<minute-window+1:q.popleft()
        # Only observations STRICTLY BEFORE the decision minute are included.
        past=[v for _,v in q]
        coverage=len(past)/window
        if len(past)>=math.ceil(window*0.8) and past:
            eligible+=1
            mean=sum(past)/len(past)
            var=sum((x-mean)**2 for x in past)/len(past)
            sd=max(math.sqrt(var),0.02)
            z=(by_min[minute][1]-mean)/sd
            delta=by_min[minute][1]-mean
            latest={'minute':minute,'z':round(z,3),'delta':round(delta,4),'coverage_pct':round(coverage*100,1)}
            if abs(z)>=2 and abs(delta)>=0.15:
                side=-1 if z>0 else 1
                signals+=1
                # The NEXT minute is the earliest possible entry: no same-bar fill.
                entry=by_min.get(minute+1)
                if entry and entry[2]>0:
                    for h in HORIZONS:
                        exit_row=by_min.get(minute+1+h)
                        if exit_row and exit_row[2]>0:
                            gross=side*(exit_row[2]/entry[2]-1)*100
                            cost=(2*fee_rate+2*slippage_rate)*100
                            results[h].append(gross-cost)
        q.append((minute,by_min[minute][1]))
    horizon_stats=[]
    for h in HORIZONS:
        a=results[h]
        horizon_stats.append({'minutes':h,'completed':len(a),
           'win_rate_pct':round(100*sum(x>0 for x in a)/len(a),1) if a else None,
           'mean_net_pct':round(sum(a)/len(a),4) if a else None,
           'sum_net_pct':round(sum(a),4) if a else None})
    return {'window_minutes':window,'observed_minutes':len(rows),'eligible_decisions':eligible,
            'candidate_signals':signals,'latest':latest,'horizons':horizon_stats}

def research_report(db,symbol,now=None):
    rows=minute_data(db,symbol,now)
    last=rows[-1][0]*60 if rows else None
    return {'symbol':symbol,'source':'local_sqlite','observed_minutes':len(rows),
      'last_sample_ts':last,'window_hours':72,
      'fee_rate_per_side':0.0005,'assumed_slippage_per_side':0.0002,
      'execution':'READ_ONLY_SHADOW','results':[analyze(rows,w) for w in WINDOWS],
      'limitations':['Це не виконувані ціни: використовуються історичні Mark Price.',
       'Bid/Ask spread, ліквідність, funding та ризик-фільтри не відтворені.',
       'Сигнали можуть перекриватися; сума результатів не є PnL портфеля.',
       'Відсутні хвилини не заповнюються; потрібні повні точки входу/виходу.',
       'Статистика на малому числі спостережень не доводить перевагу.']}
