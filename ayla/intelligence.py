"""Read-only analytics for Ayla Funding Bot. No order execution."""
import os
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

REASONS = {
    'WARMING_UP': 'Недостатньо історії',
    'Z_TOO_LOW': 'Недостатній Z-score',
    'PREMIUM_DELTA': 'Мале відхилення премії',
    'NO_BOOK': 'Немає актуального Bid/Ask',
    'SPREAD_HIGH': 'Завеликий Bid/Ask spread',
    'MOMENTUM': 'Несприятливий імпульс ціни',
    'FUNDING_UNKNOWN': 'Невідомий час funding',
    'FUNDING_NEAR': 'Наближається funding',
    'RISK_LOCK': 'Ризиковий ліміт / відкрита позиція',
    'READY': 'Умови сигналу виконано',
}

def classify(candidate, quote, stat, funding_minutes, cfg, can_open=None, now=None):
    """Informational preflight; does not alter actual trading rules."""
    now = int(time.time()) if now is None else now
    z = candidate.get('z')
    if z is None: return 'WARMING_UP'
    if abs(z) < 2: return 'Z_TOO_LOW'
    if abs(float(stat.get('delta', 0))) < 0.15: return 'PREMIUM_DELTA'
    if not quote or now - quote.get('ts', 0) > 5: return 'NO_BOOK'
    if float(stat.get('spread', 999)) > float(cfg.max_spread_pct): return 'SPREAD_HIGH'
    side = 'SHORT' if z > 0 else 'LONG'
    momentum = float(stat.get('momentum', 0))
    if (side == 'SHORT' and momentum > 0.4) or (side == 'LONG' and momentum < -0.4): return 'MOMENTUM'
    if funding_minutes < 0: return 'FUNDING_UNKNOWN'
    if funding_minutes <= cfg.max_minutes + 5: return 'FUNDING_NEAR'
    if can_open is not None and not can_open(candidate['symbol'])[0]: return 'RISK_LOCK'
    return 'READY'

def aggregate(db, candidates, now=None):
    """At most one write per symbol per five-minute bucket, bounded storage."""
    now = int(time.time()) if now is None else now
    bucket = now // 300 * 300
    with db:
        db.executemany('INSERT OR IGNORE INTO signal_diagnostics(bucket,symbol,reason,z,premium) VALUES(?,?,?,?,?)',
                       [(bucket, c['symbol'], c['reason'], c.get('z'), c.get('premium')) for c in candidates])
    return bucket

def signal_summary(db, hours=24):
    cutoff = int(time.time()) - hours*3600
    rows = db.execute('SELECT reason,COUNT(*) n FROM signal_diagnostics WHERE bucket>=? GROUP BY reason ORDER BY n DESC', (cutoff,)).fetchall()
    return [{'reason':r['reason'],'label':REASONS.get(r['reason'],r['reason']),'count':r['n']} for r in rows]

def premium_series(db, symbol, hours=24, max_points=360):
    """Time-bucket average, query returns <= max_points without loading all samples."""
    hours = max(1,min(168,int(hours)))
    width = max(5, hours*3600 // max_points)
    cutoff = int(time.time())-hours*3600
    rows = db.execute('SELECT (ts / ?) * ? bucket, AVG(premium) p, COUNT(*) n FROM samples '
                      'WHERE symbol=? AND ts>=? GROUP BY bucket ORDER BY bucket',
                      (width,width,symbol,cutoff)).fetchall()
    return [{'ts':r['bucket'],'premium':round(r['p'],6),'samples':r['n']} for r in rows]

def system_health(db, state, started, history):
    now = int(time.time())
    last = db.execute('SELECT MAX(ts) ts FROM samples').fetchone()['ts']
    total = db.execute('SELECT COUNT(*) n FROM samples').fetchone()['n']
    db_path = db.execute('PRAGMA database_list').fetchone()['file']
    size = 0
    for suffix in ('','-wal','-shm'):
        try: size += os.path.getsize(db_path+suffix)
        except OSError: pass
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_mb = round(rss / (1024 if os.name == 'posix' else 1024*1024), 1)
    except Exception: rss_mb = None
    return {'connected':state['connected'],'last_market_ts':state['last_update'],
            'market_age_seconds':now-state['last_update'] if state['last_update'] else None,
            'last_sample_ts':last,'sample_age_seconds':now-last if last else None,
            'sample_rows':total,'db_bytes':size,'uptime_seconds':now-started,
            'rss_mb':rss_mb,'error':state['error'],
            'history_ready':sum(bool(c.get('history',{}).get('ready')) for c in history)}

def daily_report(db, day=None):
    tz = ZoneInfo('Europe/Kyiv')
    day = day or datetime.now(tz).date()
    start = int(datetime(day.year,day.month,day.day,tzinfo=tz).timestamp())
    from datetime import timedelta
    next_day = day + timedelta(days=1)
    end = int(datetime(next_day.year,next_day.month,next_day.day,tzinfo=tz).timestamp())
    trades = db.execute("SELECT pnl,reason FROM trades WHERE status='CLOSED' AND closed>=? AND closed<?",(start,end)).fetchall()
    from decimal import Decimal
    pnl = sum((Decimal(str(r['pnl'] or '0')) for r in trades),Decimal('0'))
    counts = db.execute('SELECT reason,COUNT(*) n FROM signal_diagnostics WHERE bucket>=? AND bucket<? GROUP BY reason',(start,end)).fetchall()
    return {'date':day.isoformat(),'closed_trades':len(trades),'net_pnl':str(pnl),
            'positive_trades':sum(Decimal(str(r['pnl'] or '0'))>0 for r in trades),
            'diagnostics':[{'reason':r['reason'],'label':REASONS.get(r['reason'],r['reason']),'count':r['n']} for r in counts]}
