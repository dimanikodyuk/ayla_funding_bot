import asyncio, os, time, statistics
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from decimal import Decimal
from fastapi import FastAPI,HTTPException,Header
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from .db import connect,setting,set_setting
from .core import D,premium,zscore,signal,Config
from .bingx import BingX
from .trading import PaperEngine
from .telegram import notify

load_dotenv(); CFG=Config();db=connect();client=BingX();engine=PaperEngine(db,CFG)
state={'connected':False,'last_update':0,'error':None,'markets':0,'candidates':[],'last_cycle':0,'qualified':0}
hist=defaultdict(lambda:deque(maxlen=17280)); quotes={};stats={};momentum=defaultdict(lambda:deque(maxlen=15))
history_progress_cache={'ts':0,'data':{}}
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
    global last_volume,recovery_pending
    await asyncio.sleep(1)
    tick=0
    while True:
        try:
            now=int(time.time())
            raw=normalize(await client.premiums())
            if now-last_volume>=60:
                tickers=normalize(await client.ticker())
                volumes.clear()
                volumes.update({str(x.get('symbol')):D(x.get('quoteVolume') or x.get('quoteVolume24h') or '0') for x in tickers if isinstance(x,dict)})
                last_volume=now
            candidates=[]; count=0; eligible=[]
            for item in raw:
                try:
                    sym=str(item['symbol']);mark=D(item['markPrice']);idx=D(item['indexPrice'])
                    if not sym.endswith('-USDT') or mark<=0 or idx<=0:continue
                    count+=1;p=premium(mark,idx);history=hist[sym]
                    vol=volumes.get(sym,D(0))
                    z=zscore(p,list(history)) if vol>=CFG.min_volume and len(history)>=13824 and history_progress([sym], now)[sym]['ready'] else None
                    mean=sum(history)/len(history) if z is not None else p
                    delta=p-mean
                    history.append(p)
                    if now%5==0:db.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?)',(sym,now,float(p),float(mark),float(idx)))
                    momentum[sym].append((now,idx))
                    old=next((v for t,v in momentum[sym] if now-t>=55),None)
                    mom=float((idx/old-1)*100) if old else 0.0
                    stats[sym]={'z':z,'premium':float(p),'delta':float(delta),'momentum':mom}
                    if vol>=CFG.min_volume:
                        eligible.append((sym,abs(float(delta)),z,p,vol,mom))
                        candidates.append({'symbol':sym,'premium':round(float(p),4),'z':round(z,3) if z is not None else None,'volume':float(vol),'mark':str(mark),'signal':'WARMING_UP' if z is None else 'OBSERVE'})
                except (KeyError,TypeError,ValueError,ArithmeticError,ZeroDivisionError):continue
            db.commit()
            # Book requests are limited to top 10 active/interesting symbols per cycle.
            targets=[x[0] for x in sorted(eligible,key=lambda x:x[1],reverse=True)[:8]]
            targets=list(dict.fromkeys([p['symbol'] for p in engine.positions()]+targets))[:12]
            for sym in targets:
                try:
                    book=book_prices(await client.book(sym))
                    if book:
                        bid,ask=book;quotes[sym]={'bid':str(bid),'ask':str(ask),'ts':int(time.time())}
                        spread=(ask-bid)/((ask+bid)/2)*100
                        stats[sym]['spread']=float(spread)
                except Exception:continue
            if recovery_pending:
                recovered=engine.recovery(quotes)
                for sym,pnl in recovered:
                    event('WARNING',f'RECOVERY_GAP {sym} PnL={pnl}');await notify(f'⚠️ Recovery gap: {sym} PnL {pnl}')
                if not engine.positions():recovery_pending=False
            for sym,reason,pnl in engine.manage(quotes,stats):
                event('INFO',f'CLOSE {sym} {reason} PnL={pnl}');await notify(f'Закрито {sym}: {reason}; PnL {pnl} USDT')
            if setting(db,'paper_enabled','0')=='1' and not recovery_pending:
                for sym,_,z,p,vol,mom in sorted(eligible,key=lambda x:x[1],reverse=True):
                    if z is None or sym not in quotes:continue
                    q=quotes[sym]
                    if now-q['ts']>5:continue
                    spread=stats[sym].get('spread',999)
                    # Conservative: unknown funding schedule means no entries.
                    funding_time=item_funding_minutes(sym,raw,now)
                    # No funding settlement simulation yet: reject trades that could cross settlement.
                    if funding_time <= CFG.max_minutes + 5:continue
                    side=signal(p,z,vol,spread,mom,funding_time,delta=stats[sym]['delta'])
                    if side:
                        trade_id,why=engine.open(sym,side,q['bid'],q['ask'])
                        if trade_id:
                            engine.log_signal(sym,side,z,float(p),'PAPER_OPEN')
                            event('INFO',f'OPEN {side} {sym} #{trade_id} z={z:.2f}')
                            await notify(f'PAPER {side} {sym} #{trade_id}; Z={z:.2f}')
                            break
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
    for row in db.execute('SELECT symbol,premium FROM samples WHERE ts>=? ORDER BY ts',(since,)):
        hist[row['symbol']].append(D(row['premium']))
    event('INFO','Service start; paper broker only; historical gaps require recovery')
    task=asyncio.create_task(collect())
    yield
    task.cancel()
    try:await task
    except asyncio.CancelledError:pass
    await client.close();db.close()

app=FastAPI(title='Ayla Funding Bot',lifespan=lifespan)
def auth(x_token):
    import hmac
    token=os.getenv('WEB_TOKEN','change-me')
    if token=='change-me' or not x_token or not hmac.compare_digest(x_token,token):raise HTTPException(401,'Set WEB_TOKEN and provide X-Token')

@app.get('/',response_class=HTMLResponse)
async def home():return (Path(__file__).resolve().parent.parent/'static/index.html').read_text(encoding='utf8')

def history_progress(symbols, now=None):
    """Observed coverage of the previous 24h, using persisted sample timestamps.

    80% is the minimum sample coverage, but the observation window must also
    span the full 24h; restarting the service never resets this progress.
    """
    now = int(time.time()) if now is None else int(now)
    if now - history_progress_cache['ts'] < 30 and set(symbols).issubset(history_progress_cache['data']):
        return {sym: history_progress_cache['data'][sym] for sym in symbols}
    result = {}
    start = now - 86400
    for sym in symbols:
        r = db.execute('SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts, COUNT(*) AS n '
                       'FROM samples WHERE symbol=? AND ts>=? AND ts<=?',
                       (sym, start, now)).fetchone()
        count = int(r['n'])
        first = r['first_ts']
        last = r['last_ts']
        # Report both coverage and age: gaps must not look like continuous collection.
        span = max(0, min(86400, now - first)) if first is not None else 0
        covered = min(86400, count * 5)
        percent = min(100.0, 100.0 * covered / 86400)
        ready = bool(first is not None and span >= 86395 and count >= 13824)
        result[sym] = {'observed_seconds': span, 'sample_count': count,
                       'coverage_percent': round(percent, 1),
                       'minimum_samples': 13824, 'window_seconds': 86400,
                       'ready': ready, 'last_sample_ts': last}
    history_progress_cache.update(ts=now, data={**(history_progress_cache['data'] if now-history_progress_cache['ts']<30 else {}), **result})
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
    candidates = [{**c, 'history': progress.get(c['symbol'])} for c in state['candidates']]
    return {**state, 'candidates': candidates, 'history_window_hours': 24, 'history_required_coverage_pct': 80, 'mode':'RECOVERING' if recovery_pending else ('PAPER_ACTIVE' if setting(db,'paper_enabled','0')=='1' else 'COLLECTING'),'paper_execution':'ENABLED' if setting(db,'paper_enabled','0')=='1' else 'PAUSED','balance':str(balance),'equity':str(balance+unreal),'realized_pnl':str(engine.realized()),'unrealized_pnl':str(unreal),'open_positions':len(pos),'strategy':'premium-mean-reversion','live_execution':'DISABLED'}

@app.get('/api/trades')
async def trades(x_token:str|None=Header(default=None)):
    auth(x_token);return [dict(r) for r in db.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 200')]
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
    set_setting(db,'paper_enabled',enabled);event('INFO',f'Paper intent {enabled}')
    return {'paper_requested':bool(enabled),'execution':'PAPER_ONLY','recovery_pending':recovery_pending}
