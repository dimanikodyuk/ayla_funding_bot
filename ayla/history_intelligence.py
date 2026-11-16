"""Read-only 24h minute coverage diagnostics. No changes to paper execution."""
import time

WINDOW=1440
TARGET=1152

def history_detail(db, symbol, now=None):
    now=int(time.time()) if now is None else int(now)
    if not (1<=len(symbol)<=35 and all(c.isupper() or c.isdigit() or c=='-' for c in symbol)):
        raise ValueError('Invalid symbol')
    start=now-86400
    # Use the existing PRIMARY KEY(symbol,ts) index. Read only this symbol/window.
    rows=db.execute('SELECT ts FROM samples WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts',
                    (symbol,start,now)).fetchall()
    minutes={int(r[0])//60 for r in rows}
    first=int(rows[0][0]) if rows else None
    last=int(rows[-1][0]) if rows else None
    count=len(minutes)
    span=max(0,min(86400,now-first)) if first is not None else 0
    fresh=last is not None and now-last<=120
    ready=span>=86100 and fresh and count>=TARGET
    # Calendar-aligned hourly slots ending at current minute; 24 bins of 60 minutes.
    current_minute=now//60
    base=current_minute-1439
    hours=[]
    for i in range(24):
        a=base+i*60
        covered=sum(1 for m in range(a,a+60) if m in minutes)
        hours.append({'start_ts':a*60,'covered':covered,'total':60})
    recent=sum(1 for m in range(current_minute-59,current_minute+1) if m in minutes)
    oldest=sum(1 for m in range(base,base+60) if m in minutes)
    # Forecast: every upcoming minute has a sample; old minute buckets expire.
    # This is conditional, not a guarantee of future collection or exchange availability.
    eta=None
    if ready: eta=0
    elif fresh:
        # Keep a fixed 1440-minute calendar window, shift by one minute per step.
        # Preserve the existing span requirement and recent-data requirement.
        occupancy=sum(m in minutes for m in range(base,current_minute+1))
        for step in range(1,1441):
            departing=base+step-1
            occupancy-=int(departing in minutes)
            occupancy+=1
            future_span=span+step*60 if span<86100 else span
            if occupancy>=TARGET and future_span>=86100:
                eta=step
                break
    if ready: reason='READY'
    elif not fresh: reason='STALE_DATA'
    elif span<86100: reason='INSUFFICIENT_SPAN'
    else: reason='INSUFFICIENT_COVERAGE'
    return {'symbol':symbol,'now':now,'coverage_percent':round(100*count/WINDOW,2),
            'observed_minutes':count,'missing_minutes':max(0,WINDOW-count),
            'minutes_to_threshold':max(0,TARGET-count),'recent_hour_minutes':recent,
            'oldest_hour_minutes':oldest,'sample_count':len(rows),
            'last_sample_ts':last,'sample_age_seconds':now-last if last is not None else None,
            'largest_gap_seconds':max((int(b[0])-int(a[0]) for a,b in zip(rows,rows[1:])),default=None),
            'span_seconds':span,'ready':ready,'reason':reason,'eta_minutes':eta,
            'eta_ts':now+eta*60 if eta is not None else None,
            'eta_assumption':'Один новий заповнений хвилинний інтервал щохвилини; без нових збоїв',
            'hours':hours}
