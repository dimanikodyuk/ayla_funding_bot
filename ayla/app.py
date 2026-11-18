import asyncio, os, time, statistics, sqlite3
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import FastAPI,HTTPException,Header
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from .db import connect,setting,set_setting
from .core import D,premium,zscore,signal,Config
from .bingx import BingX
from .trading import PaperEngine
from .history_intelligence import history_detail
from .research_lab import research_report
from .telegram import notify,notify_trade
from .intelligence import classify, aggregate, signal_summary, premium_series, system_health, daily_report
from .signal_validation import evaluate, threshold_preview
from .market_data import ticker_snapshot, fresh_quote, spread_pct, MAX_QUOTE_AGE_SECONDS


load_dotenv(); CFG=Config();db=connect();client=BingX();engine=PaperEngine(db,CFG)
TRADE_WINDOW_MINUTES=360
TRADE_REQUIRED_MINUTES=288
state={'connected':False,'last_update':0,'error':None,'markets':0,'candidates':[],'last_cycle':0,'qualified':0}
hist=defaultdict(lambda:deque(maxlen=1440)); hist_minutes=defaultdict(lambda:deque(maxlen=1440)); quotes={};stats={};momentum=defaultdict(lambda:deque(maxlen=15))
history_progress_cache={'ts':0,'data':{}}
last_diagnostics_bucket = -1
last_saved = {}
market_health={'ticker_last_success':0,'ticker_last_error':None,'ticker_valid_books':0,'ticker_rejected':0,'book_fallback_ok':0,'book_fallback_fail':0,'last_paper_check':0,'paper_blockers':{}}
cycle_lock=asyncio.Lock()
startup_time=int(time.time()); recovery_pending=bool(engine.positions()); last_volume=0; volumes={};last_book=0

def event(level,message):
    with db:db.execute('INSERT INTO events(ts,level,message) VALUES(?,?,?)',(int(time.time()),level,message[:1000]))

def normalize(data):
    if isinstance(data,list):return data
    if isinstance(data,dict):
        for k in ('list','data','tickers','symbols'):
            if isinstance(data.get(k),list):return data[k]
        return [data]
    return []

def book_prices(raw):
    if isinstance(raw,list):raw=raw[0] if raw else {}
    if not isinstance(raw,dict):return None
    try:
        bid=D(raw.get('bidPrice') or raw.get('bid') or raw.get('bestBidPrice'))
        ask=D(raw.get('askPrice') or raw.get('ask') or raw.get('bestAskPrice'))
        if bid>0 and ask>=bid:return bid,ask
    except (ValueError,TypeError,ArithmeticError):pass
    return None

async def collect():
    global last_volume,recovery_pending,last_diagnostics_bucket
    await asyncio.sleep(1)
    tick=0
    while True:
        async with cycle_lock:
            try:
                now=int(time.time())
                raw=normalize(await client.premiums())
                # One public ticker request supplies Bid/Ask for the whole market.
                # A ticker failure does not interrupt premium collection; old quotes
                # are never eligible for opening or closing paper positions.
                try:
                    ticker_rows=normalize(await client.ticker())
                    tick_ts=int(time.time())
                    fresh, new_volumes, rejected=ticker_snapshot(ticker_rows,tick_ts)
                    if fresh:
                        quotes.update(fresh)
                        volumes.update(new_volumes)
                        market_health.update(ticker_last_success=tick_ts,ticker_last_error=None,
                            ticker_valid_books=len(fresh),ticker_rejected=rejected)
                    else:
                        market_health['ticker_last_error']='No valid ticker books'
                except Exception as exc:
                    market_health['ticker_last_error']=str(exc)[:160]
                # Remove stale books from the shared cache, including previous targets.
                for sym in list(quotes):
                    if not fresh_quote(quotes,sym,int(time.time())): quotes.pop(sym,None)
                candidates=[]; count=0; eligible=[]
                for item in raw:
                    try:
                        sym=str(item['symbol']);mark=D(item['markPrice']);idx=D(item['indexPrice'])
                        if not sym.endswith('-USDT') or mark<=0 or idx<=0:continue
                        count+=1;p=premium(mark,idx);history=hist[sym]
                        vol=volumes.get(sym,D(0))
                        # One observation per minute for a time-balanced 24h Z-score.
                        minute=now//60
                        while hist_minutes[sym] and hist_minutes[sym][0] < minute-1439:
                            hist_minutes[sym].popleft(); history.popleft()
                        # The trading model uses only the last 6h of real minute buckets.
                        # The 24h history remains intact for the Research Lab and diagnostics.
                        trade_minutes=[(m,v) for m,v in zip(hist_minutes[sym],history) if minute-TRADE_WINDOW_MINUTES+1<=m<=minute]
                        recent=[v for m,v in trade_minutes if m<minute]
                        last_minute=trade_minutes[-1][0] if trade_minutes else None
                        first_minute=trade_minutes[0][0] if trade_minutes else None
                        trade_ready=bool(len(recent)>=TRADE_REQUIRED_MINUTES and
                            first_minute is not None and minute-first_minute>=TRADE_REQUIRED_MINUTES and
                            last_minute is not None and minute-last_minute<=2)
                        z=zscore(p,recent,min_samples=TRADE_REQUIRED_MINUTES) if trade_ready else None
                        mean=sum(recent)/len(recent) if z is not None else p
                        delta=p-mean
                        if not hist_minutes[sym] or hist_minutes[sym][-1]!=minute:
                            history.append(p); hist_minutes[sym].append(minute)
                        # No modulo-of-wall-clock gate: a slow REST cycle must not silently lose samples.
                        if now-last_saved.get(sym,0)>=30:
                            db.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?)',(sym,now,float(p),float(mark),float(idx)))
                            last_saved[sym]=now
                        momentum[sym].append((now,idx))
                        old=next((v for t,v in momentum[sym] if now-t>=55),None)
                        mom=float((idx/old-1)*100) if old else 0.0
                        stats[sym]={'z':z,'premium':float(p),'delta':float(delta),'momentum':mom}
                        if vol>=CFG.min_volume:
                            eligible.append((sym,abs(float(delta)),z,p,vol,mom))
                            candidates.append({'symbol':sym,'premium':round(float(p),4),'z':round(z,3) if z is not None else None,'volume':float(vol),'mark':str(mark),'signal':'WARMING_UP' if z is None else 'OBSERVE', 'trade_history':{'ready':trade_ready,'observed_minutes':len(recent),'required_minutes':TRADE_REQUIRED_MINUTES,'window_minutes':TRADE_WINDOW_MINUTES,'coverage_percent':round(len(recent)/TRADE_WINDOW_MINUTES*100,1)}})
                    except (KeyError,TypeError,ValueError,ArithmeticError,ZeroDivisionError):continue
                db.commit()
                # Freshness and spread are calculated for every eligible ticker.
                for sym, *_ in eligible:
                    q=fresh_quote(quotes,sym,int(time.time()))
                    if q: stats[sym]['spread']=spread_pct(q)
                # Optional bookTicker refinement: open positions first, then the
                # strongest statistical candidates. Failure retains fresh ticker.
                positions=engine.positions()
                near=[x for x in eligible if x[2] is not None and abs(float(x[2]))>=1.5]
                targets=list(dict.fromkeys([p['symbol'] for p in positions]+[
                    x[0] for x in sorted(near,key=lambda x:x[1],reverse=True)[:6]]))[:10]
                for sym in targets:
                    try:
                        book=book_prices(await client.book(sym))
                        if book:
                            bid,ask=book
                            quotes[sym]={'bid':str(bid),'ask':str(ask),'ts':int(time.time()),'source':'bookTicker'}
                            if sym in stats:stats[sym]['spread']=spread_pct(quotes[sym])
                            market_health['book_fallback_ok']+=1
                        else:market_health['book_fallback_fail']+=1
                    except Exception:
                        market_health['book_fallback_fail']+=1
                # Never use stale quotes for recovery, management or paper entries.
                for sym in list(quotes):
                    if not fresh_quote(quotes,sym,int(time.time())):quotes.pop(sym,None)
                if recovery_pending:
                    recovered=engine.recovery(quotes)
                    for sym,pnl in recovered:
                        event('WARNING',f'RECOVERY_GAP {sym} PnL={pnl}')
                        closed=next((t for t in engine.closed(20) if t['symbol']==sym and t['reason']=='RECOVERY_GAP'),None)
                        if closed:await notify_trade(closed,engine.decision(closed['id'],'ENTRY'))
                        else:await notify(f'⚠️ Recovery gap: {sym} PnL {pnl}')
                    if not engine.positions():recovery_pending=False
                for sym,reason,pnl in engine.manage(quotes,stats):
                    event('INFO',f'CLOSE {sym} {reason} PnL={pnl}')
                    closed=next((t for t in engine.closed(20) if t['symbol']==sym and t['reason']==reason),None)
                    if closed:
                        await notify_trade(closed,engine.decision(closed['id'],'ENTRY'),engine.decision(closed['id'],'EXIT'))
                    else:await notify(f'Закрито {sym}: {reason}; PnL {pnl} USDT')
                if setting(db,'paper_enabled','0')=='1' and not recovery_pending:
                    market_health['last_paper_check']=int(time.time())
                    blockers={'NO_Z':0,'NO_FRESH_BOOK':0,'FUNDING_GUARD':0,'SIGNAL_FILTER':0,'RISK_OR_EXECUTION':0,'OPENED':0}
                    for sym,_,z,p,vol,mom in sorted(eligible,key=lambda x:x[1],reverse=True):
                        if z is None: blockers['NO_Z']+=1;continue
                        q=fresh_quote(quotes,sym,int(time.time()))
                        if q is None: blockers['NO_FRESH_BOOK']+=1;continue
                        spread=stats[sym].get('spread',999)
                        # Conservative: unknown funding schedule means no entries.
                        funding_time=item_funding_minutes(sym,raw,now)
                        # No funding settlement simulation yet: reject trades that could cross settlement.
                        if funding_time <= CFG.max_minutes + 5: blockers['FUNDING_GUARD']+=1;continue
                        side=signal(p,z,vol,spread,mom,funding_time,delta=stats[sym]['delta'])
                        if side:
                            # Revalidate immediately before the simulated fill.
                            q=fresh_quote(quotes,sym,int(time.time()))
                            if q is None: blockers['NO_FRESH_BOOK']+=1;continue
                            trade_id,why=engine.open(sym,side,q['bid'],q['ask'])
                            if not trade_id: blockers['RISK_OR_EXECUTION']+=1
                            if trade_id:
                                engine.record_exit(next(t for t in engine.positions() if t['id']==trade_id),q,stats[sym],int(time.time()),'ENTRY','PAPER_OPEN',engine.net_exit_pnl(next(t for t in engine.positions() if t['id']==trade_id),q['bid'],q['ask']))
                                blockers['OPENED']+=1
                                engine.log_signal(sym,side,z,float(p),'PAPER_OPEN')
                                event('INFO',f'OPEN {side} {sym} #{trade_id} z={z:.2f}')
                                await notify_trade(engine.trade_by_id(trade_id),engine.decision(trade_id,'ENTRY'))
                                break
                        else: blockers['SIGNAL_FILTER']+=1
                    market_health['paper_blockers']=blockers
                # Read-only diagnostics; never changes trading decisions or order execution.
                diagnostics=[]
                for c in candidates:
                    sym=c['symbol']
                    st=stats.get(sym,{})
                    minutes=item_funding_minutes(sym,raw,now)
                    current_book=fresh_quote(quotes,sym,int(time.time()))
                    reason=classify(c,current_book,st,minutes,CFG,engine.can_open,int(time.time()))
                    c['reason']=reason
                    c['spread']=round(st['spread'],4) if 'spread' in st else None
                    c['momentum']=round(st.get('momentum',0),3)
                    c['funding_minutes']=round(minutes,1) if minutes>=0 else None
                    c.update(evaluate(c,current_book,st,minutes,CFG,engine.can_open,int(time.time())))
                    c['book_source']=current_book.get('source') if current_book else None
                    c['book_age_seconds']=max(0,int(time.time())-current_book['ts']) if current_book else None
                    diagnostics.append(c)
                bucket=now//300
                if bucket != last_diagnostics_bucket:
                    aggregate(db,diagnostics,now)
                    last_diagnostics_bucket=bucket
                state.update(connected=True,last_update=now,error=None,markets=count,qualified=len(eligible),candidates=sorted(candidates,key=lambda x:abs(x['z'] or 0),reverse=True)[:100],last_cycle=now)
            except Exception as exc:
                state.update(connected=False,error=str(exc));event('ERROR',f'collector: {exc}')
        await asyncio.sleep(5 if state['connected'] else 15)

def item_funding_minutes(symbol,raw,now):
    for x in raw:
        if isinstance(x,dict) and x.get('symbol')==symbol:
            try:
                ts=int(x.get('nextFundingTime') or 0)
                if ts>10**11:ts//=1000
                if ts>now:return (ts-now)/60
            except (TypeError,ValueError):pass
    return -1

@asynccontextmanager
async def lifespan(app):
    since=int(time.time())-86400
    # Rebuild 1-minute series after a power loss, without weighting fast cycles more heavily.
    for row in db.execute('SELECT symbol,ts,premium FROM samples WHERE ts>=? ORDER BY ts',(since,)):
        sym=row['symbol']; minute=row['ts']//60
        if not hist_minutes[sym] or hist_minutes[sym][-1]!=minute:
            hist_minutes[sym].append(minute); hist[sym].append(D(row['premium']))
        last_saved[sym]=max(last_saved.get(sym,0),row['ts'])
    event('INFO','Service start; paper broker only; historical gaps require recovery')
    task=asyncio.create_task(collect())
    yield
    task.cancel()
    try:await task
    except asyncio.CancelledError:pass
    await client.close();db.close()

app=FastAPI(title='Ayla Funding Bot',lifespan=lifespan)
# Additive schema migration, existing user SQLite data remains intact.
db.execute('''CREATE TABLE IF NOT EXISTS signal_diagnostics(
 bucket INTEGER NOT NULL,symbol TEXT NOT NULL,reason TEXT NOT NULL,
 z REAL,premium REAL,PRIMARY KEY(bucket,symbol))''')
db.execute('CREATE INDEX IF NOT EXISTS ix_signal_diagnostics_bucket ON signal_diagnostics(bucket)')
db.commit()
def auth(x_token):
    import hmac
    token=os.getenv('WEB_TOKEN','change-me')
    if token=='change-me' or not x_token or not hmac.compare_digest(x_token,token):raise HTTPException(401,'Set WEB_TOKEN and provide X-Token')

@app.get('/',response_class=HTMLResponse)
async def home():return (Path(__file__).resolve().parent.parent/'static/index.html').read_text(encoding='utf8')

def history_progress(symbols, now=None):
    """Coverage of distinct minute buckets, not number of REST polls.

    A complete 24-hour window has 1440 buckets; at least 1152 distinct
    observed minutes (80%) are required, with observations near both ends.
    Missing intervals are never synthesized or backfilled.
    """
    now = int(time.time()) if now is None else int(now)
    if 0 <= now-history_progress_cache['ts'] < 30 and set(symbols).issubset(history_progress_cache['data']):
        return {sym: history_progress_cache['data'][sym] for sym in symbols}
    result={}; start=now-86400
    for sym in symbols:
        r=db.execute("""SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts,
                      COUNT(*) AS n, COUNT(DISTINCT CAST(ts/60 AS INTEGER)) AS minutes
                      FROM samples WHERE symbol=? AND ts>=? AND ts<=?""",
                     (sym,start,now)).fetchone()
        count=int(r['n']); minutes=int(r['minutes']); first=r['first_ts']; last=r['last_ts']
        span=max(0,min(86400,now-first)) if first is not None else 0
        percent=min(100.0,100.0*minutes/1440)
        # Full-day baseline and recent observations, in addition to 80% coverage.
        ready=bool(first is not None and span>=86100 and now-last<=120 and minutes>=1152)
        result[sym]={'observed_seconds':span,'sample_count':count,
                     'observed_minutes':minutes,'coverage_percent':round(percent,1),
                     'minimum_samples':1152,'window_seconds':86400,
                     'ready':ready,'last_sample_ts':last}
    history_progress_cache.update(ts=now,data={**(history_progress_cache['data'] if 0<=now-history_progress_cache['ts']<30 else {}),**result})
    return result

@app.get('/api/status')
async def status(x_token:str|None=Header(default=None)):
    auth(x_token)
    pos=engine.positions(); balance=engine.balance()
    unreal=D(0)
    for p in pos:
        q=quotes.get(p['symbol'])
        if q:
            px=D(q['bid'] if p['side']=='LONG' else q['ask'])
            unreal+=(D('1') if p['side']=='LONG' else D('-1'))*D(p['qty'])*(px-D(p['entry']))-D(p['fee_open'])-D(p['qty'])*px*CFG.fee_rate
    progress = history_progress([c['symbol'] for c in state['candidates']])
    candidates = [{**c, 'history': c.get('trade_history'), 'history_24h': progress.get(c['symbol'])} for c in state['candidates']]
    return {**state, 'candidates': candidates, 'history_window_hours': 6, 'history_required_coverage_pct': 80, 'trade_history_required_minutes': TRADE_REQUIRED_MINUTES, 'history_diagnostics_window_hours':24, 'market_data':{**market_health,'ticker_age_seconds':int(time.time())-market_health['ticker_last_success'] if market_health['ticker_last_success'] else None}, 'mode':'RECOVERING' if recovery_pending else ('PAPER_ACTIVE' if setting(db,'paper_enabled','0')=='1' else 'COLLECTING'),'paper_execution':'ENABLED' if setting(db,'paper_enabled','0')=='1' else 'PAUSED','balance':str(balance),'equity':str(balance+unreal),'realized_pnl':str(engine.realized()),'unrealized_pnl':str(unreal),'open_positions':len(pos),'strategy':'premium-mean-reversion','live_execution':'DISABLED'}

@app.get('/api/trades')
async def trades(x_token:str|None=Header(default=None)):
    auth(x_token);return [dict(r) for r in db.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 200')]
@app.get('/api/trade-chart/{trade_id}')
async def trade_chart(trade_id:int,minutes:int=15,x_token:str|None=Header(default=None)):
    """Read-only trade context. Samples are minute Mark/Index snapshots, NOT executable quotes."""
    auth(x_token)
    if minutes not in (5,15,30,60):raise HTTPException(400,'minutes must be 5, 15, 30 or 60')
    trade=db.execute('SELECT * FROM trades WHERE id=?',(trade_id,)).fetchone()
    if trade is None:raise HTTPException(404,'Trade not found')
    t=dict(trade)
    opened=int(t['opened']);closed=int(t['closed']) if t['closed'] is not None else int(time.time())
    start=opened-minutes*60;end=closed+minutes*60
    # Limit response size and avoid loading an unbounded period for long-lived positions.
    rows=db.execute("""SELECT ts,mark,premium FROM samples
      WHERE symbol=? AND ts BETWEEN ? AND ? ORDER BY ts LIMIT 3000""",(t['symbol'],start,end)).fetchall()
    return {'trade':t,'points':[{'ts':r['ts'],'mark':r['mark'],'premium':r['premium']} for r in rows],
            'window_minutes':minutes,'truncated':len(rows)>=3000,
            'note':'Mark Price is a sampled reference, not executable Bid/Ask. Entry/exit are actual Paper fill prices. Short trades may have no samples between fills.'}

@app.get('/api/events')
async def events(x_token:str|None=Header(default=None)):
    auth(x_token);return [dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]
@app.get('/api/signals')
async def signals(x_token:str|None=Header(default=None)):
    auth(x_token);return [dict(r) for r in db.execute('SELECT * FROM signal_log ORDER BY id DESC LIMIT 100')]
@app.post('/api/paper/{enabled}')
async def paper(enabled:int,x_token:str|None=Header(default=None)):
    auth(x_token)
    if enabled not in (0,1):raise HTTPException(400,'enabled must be 0 or 1')
    async with cycle_lock:
        set_setting(db,'paper_enabled',enabled);event('INFO',f'Paper intent {enabled}')
    return {'paper_requested':bool(enabled),'execution':'PAPER_ONLY','recovery_pending':recovery_pending}

@app.get('/api/intelligence')
async def intelligence(x_token:str|None=Header(default=None)):
    auth(x_token)
    recent=[{'symbol':c['symbol'],'reason':c.get('reason','UNKNOWN'),'z':c.get('z'),
         'premium':c.get('premium'),'delta':c.get('delta'),
         'spread':c.get('spread'),'funding_minutes':c.get('funding_minutes'),
         'failures':c.get('failures',[]),'data_ts':c.get('data_ts'),
         'eligible':c.get('eligible',False),'book_source':c.get('book_source'),'book_age_seconds':c.get('book_age_seconds')} for c in state['candidates']]
    return {'reasons':signal_summary(db,24), 'reasons_1h':signal_summary(db,1),
            'reasons_24h':signal_summary(db,24),
            'recent':recent,'snapshot_ts':state['last_update'],
            'threshold_preview':threshold_preview(recent),
            'note':'Пороги — лише поточний зріз, не backtest і не дозвіл Paper.'}

@app.get('/api/radar')
async def radar(x_token:str|None=Header(default=None)):
    auth(x_token)
    return sorted([{'symbol':c['symbol'],'premium':c['premium'],'z':c['z'],
                    'volume':c['volume'],'spread':c.get('spread'),
                    'reason':c.get('reason'),'history_ready':bool(c.get('history',{}).get('ready'))}
                   for c in state['candidates']],
                  key=lambda c:abs(c['z']) if c['z'] is not None else -1,reverse=True)

@app.get('/api/premium/{symbol}')
async def premium_chart(symbol:str,hours:int=24,x_token:str|None=Header(default=None)):
    auth(x_token)
    if not (1<=len(symbol)<=35 and all(c.isupper() or c.isdigit() or c=='-' for c in symbol)):
        raise HTTPException(400,'Invalid symbol')
    if hours<1 or hours>168:raise HTTPException(400,'hours must be 1..168')
    return {'symbol':symbol,'hours':hours,'points':premium_series(db,symbol,hours)}

@app.get('/api/health')
async def health(x_token:str|None=Header(default=None)):
    auth(x_token)
    return system_health(db,state,startup_time,state['candidates'])

@app.get('/api/daily')
async def daily(x_token:str|None=Header(default=None)):
    auth(x_token)
    return daily_report(db)

@app.get('/api/history/{symbol}')
async def history_intelligence(symbol:str,x_token:str|None=Header(default=None)):
    auth(x_token)
    try:return history_detail(db,symbol)
    except ValueError:raise HTTPException(400,'Invalid symbol')

@app.get('/api/research/{symbol}')
async def research(symbol:str,x_token:str|None=Header(default=None)):
    auth(x_token)
    try:return research_report(db,symbol)
    except ValueError:raise HTTPException(400,'Invalid symbol')


@app.get('/api/exit-intelligence')
async def exit_intelligence(x_token:str|None=Header(default=None)):
    auth(x_token)
    decisions=[dict(r) for r in db.execute('''SELECT e.*,t.symbol,t.side,t.status FROM exit_decisions e
      LEFT JOIN trades t ON t.id=e.trade_id ORDER BY e.id DESC LIMIT 200''')]
    return {'decisions':decisions,'note':'Read-only audit. NORMALIZED remains the original exit rule; no profit gating.'}

@app.get('/api/exit-research')
async def exit_research(x_token:str|None=Header(default=None)):
    auth(x_token)
    # Observational replay only: samples contain mark/index, NOT executable bid/ask.
    # Do not present mark-based hypothetical returns as realized PnL.
    from .exit_research import replay
    return replay(db,CFG)

@app.post('/api/paper-reset/{mode}')
async def paper_reset(mode:str,x_token:str|None=Header(default=None)):
    auth(x_token)
    if mode not in ('balance','trades','all'):raise HTTPException(400,'Invalid reset mode')
    async with cycle_lock:
        if setting(db,'paper_enabled','0')=='1':raise HTTPException(409,'Pause Paper Trading first')
        if engine.positions() or recovery_pending:raise HTTPException(409,'Close all paper positions first')
        before=engine.balance()
        backup_dir=Path('data/backups');backup_dir.mkdir(parents=True,exist_ok=True)
        backup=backup_dir/('paper_before_reset_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite')
        # SQLite online backup includes WAL-committed records, no copying live .sqlite files.
        with sqlite3.connect(str(backup)) as target:db.backup(target)
        try:
            with db:
                if mode in ('trades','all'):
                    for table in ('paper_fills','funding_events','signal_log','exit_decisions','trades'):
                        db.execute('DELETE FROM '+table)
                # balance only: retain historical PnL but offset it to exactly initial_balance
                if mode=='balance':offset=-engine.realized()
                elif mode=='trades':offset=before-CFG.initial_balance
                else:offset=D(0)
                db.execute("INSERT INTO paper_state(key,value) VALUES('balance_offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(offset),))
                db.execute('INSERT INTO paper_resets(ts,mode,balance_before,backup_path) VALUES(?,?,?,?)',
                           (int(time.time()),mode,str(before),str(backup)))
                db.execute('INSERT INTO events(ts,level,message) VALUES(?,?,?)',
                           (int(time.time()),'WARNING',f'Paper reset {mode}; backup={backup}'))
        except Exception:
            raise
        return {'ok':True,'mode':mode,'balance':str(engine.balance()),'backup':str(backup),'paper_enabled':False}
