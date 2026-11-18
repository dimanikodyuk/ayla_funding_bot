"""Read-only multi-filter diagnostics. Never opens orders or changes strategy."""
import time
from .intelligence import REASONS

def evaluate(candidate, quote, stat, funding_minutes, cfg, can_open=None, now=None):
    now=int(time.time()) if now is None else int(now)
    z=candidate.get('z'); delta=stat.get('delta')
    failures=[]
    if z is None: failures.append('WARMING_UP')
    elif abs(float(z)) < 2: failures.append('Z_TOO_LOW')
    if z is not None and (delta is None or abs(float(delta)) < 0.15 or (float(z)*float(delta)<=0)):
        failures.append('PREMIUM_DELTA')
    if not quote or now-int(quote.get('ts',0))>5: failures.append('NO_BOOK')
    if quote and float(stat.get('spread',999))>float(cfg.max_spread_pct): failures.append('SPREAD_HIGH')
    side='SHORT' if z is not None and z>0 else 'LONG'
    momentum=float(stat.get('momentum',0))
    if z is not None and ((side=='SHORT' and momentum>0.4) or (side=='LONG' and momentum < -0.4)):
        failures.append('MOMENTUM')
    if funding_minutes<0: failures.append('FUNDING_UNKNOWN')
    elif funding_minutes<=cfg.max_minutes+5: failures.append('FUNDING_NEAR')
    if can_open is not None:
        allowed,_=can_open(candidate['symbol'])
        if not allowed: failures.append('RISK_LOCK')
    return {'failures':failures,'labels':[REASONS.get(x,x) for x in failures],
            'delta':round(float(delta),5) if delta is not None else None,
            'threshold_z':2.0,'threshold_delta':0.15,
            'data_ts':now,'eligible':not failures}

def threshold_preview(candidates):
    """Current-snapshot counts, not historical backtest and not executable signals."""
    variants=((2.0,0.15),(1.5,0.05),(2.0,0.05),(1.5,0.15))
    out=[]
    for zlim,dlim in variants:
        matches=[]
        for c in candidates:
            z=c.get('z'); d=c.get('delta')
            if z is not None and d is not None and abs(z)>=zlim and abs(d)>=dlim and z*d>0:
                matches.append(c['symbol'])
        out.append({'z_threshold':zlim,'delta_threshold':dlim,'count':len(matches),'symbols':matches[:12]})
    return out
