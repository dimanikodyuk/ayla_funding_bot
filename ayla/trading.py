"""Paper-only execution engine. Never sends signed requests or real orders."""
import time
from decimal import Decimal
from .core import D, trade_pnl, funding_cashflow

class PaperEngine:
    def __init__(self, db, cfg):
        self.db,self.cfg=db,cfg
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS paper_fills(id INTEGER PRIMARY KEY, trade_id INTEGER, ts INTEGER, action TEXT, price TEXT, qty TEXT, fee TEXT, UNIQUE(trade_id,action));
        CREATE TABLE IF NOT EXISTS signal_log(id INTEGER PRIMARY KEY, ts INTEGER, symbol TEXT, side TEXT, z REAL, premium REAL, reason TEXT);
        CREATE TABLE IF NOT EXISTS paper_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS exit_decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,trade_id INTEGER NOT NULL,ts INTEGER NOT NULL,
            event TEXT NOT NULL,z REAL,premium REAL,bid TEXT,ask TEXT,
            net_pnl TEXT,hold_seconds INTEGER,reason TEXT,
            UNIQUE(trade_id,ts,event));
        CREATE INDEX IF NOT EXISTS ix_exit_decisions_trade ON exit_decisions(trade_id,ts);
        CREATE TABLE IF NOT EXISTS paper_resets(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,mode TEXT,balance_before TEXT,backup_path TEXT);
        ''')
        self.db.commit()
    def positions(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY id")]
    def closed(self, limit=200):
        return [dict(r) for r in self.db.execute("SELECT * FROM trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?",(limit,))]
    def realized(self):
        return sum((D(r['pnl']) for r in self.db.execute("SELECT pnl FROM trades WHERE status='CLOSED' AND pnl IS NOT NULL")),D(0))
    def balance(self):
        row=self.db.execute("SELECT value FROM paper_state WHERE key='balance_offset'").fetchone()
        return self.cfg.initial_balance + (D(row['value']) if row else D(0)) + self.realized()
    def net_exit_pnl(self,trade,bid,ask):
        bid,ask=D(bid),D(ask)
        px=bid if trade['side']=='LONG' else ask
        return trade_pnl(trade['side'],D(trade['qty']),D(trade['entry']),px)-D(trade['fee_open'])-D(trade['qty'])*px*self.cfg.fee_rate+D(trade['funding'] or 0)
    def record_exit(self,trade,q,st,now,event,reason,net):
        with self.db:
            self.db.execute('''INSERT OR IGNORE INTO exit_decisions
                (trade_id,ts,event,z,premium,bid,ask,net_pnl,hold_seconds,reason)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (trade['id'],now,event,st.get('z'),st.get('premium'),str(q['bid']),str(q['ask']),
                 str(net),max(0,now-trade['opened']),reason))

    def decision(self,trade_id,event):
        row=self.db.execute('SELECT * FROM exit_decisions WHERE trade_id=? AND event=? ORDER BY id DESC LIMIT 1',(trade_id,event)).fetchone()
        return dict(row) if row else {}

    def trade_by_id(self,trade_id):
        row=self.db.execute('SELECT * FROM trades WHERE id=?',(trade_id,)).fetchone()
        return dict(row) if row else None

    def today_pnl(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now=datetime.now(ZoneInfo('Europe/Kyiv'))
        start=int(now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
        return sum((D(r['pnl']) for r in self.db.execute("SELECT pnl FROM trades WHERE status='CLOSED' AND closed>=?",(start,))),D(0))
    def can_open(self, symbol):
        if len(self.positions())>=self.cfg.max_positions:return False,'MAX_POSITIONS'
        if any(p['symbol']==symbol for p in self.positions()):return False,'ALREADY_OPEN'
        if self.today_pnl()<=-self.cfg.daily_loss_limit:return False,'DAILY_LOSS_LIMIT'
        if self.balance()<self.cfg.margin:return False,'LOW_BALANCE'
        prev=self.db.execute("SELECT closed FROM trades WHERE symbol=? AND status='CLOSED' ORDER BY id DESC LIMIT 1",(symbol,)).fetchone()
        if prev and int(time.time())-prev['closed']<900:return False,'COOLDOWN'
        last=self.db.execute("SELECT pnl FROM trades WHERE status='CLOSED' ORDER BY id DESC LIMIT 4").fetchall()
        if len(last)==4 and all(D(x['pnl'])<0 for x in last):return False,'LOSS_STREAK_LOCK'
        return True,'OK'
    def open(self,symbol,side,bid,ask,ts=None):
        ok,why=self.can_open(symbol)
        if not ok:return None,why
        bid,ask=D(bid),D(ask)
        if bid<=0 or ask<=0 or ask<bid:return None,'INVALID_BOOK'
        price=ask if side=='LONG' else bid
        notional=self.cfg.margin*self.cfg.leverage
        qty=notional/price
        fee=notional*self.cfg.fee_rate
        ts=ts or int(time.time())
        with self.db:
            cur=self.db.execute("INSERT INTO trades(symbol,side,qty,entry,opened,fee_open,funding,status) VALUES(?,?,?,?,?,?,?,?)",(symbol,side,str(qty),str(price),ts,str(fee),'0','OPEN'))
            trade_id=cur.lastrowid
            self.db.execute("INSERT INTO paper_fills(trade_id,ts,action,price,qty,fee) VALUES(?,?,?,?,?,?)",(trade_id,ts,'OPEN',str(price),str(qty),str(fee)))
        return trade_id,'OPENED'
    def close(self,trade,bid,ask,reason,ts=None):
        bid,ask=D(bid),D(ask)
        if bid<=0 or ask<=0 or ask<bid:return None
        exit_price=bid if trade['side']=='LONG' else ask
        qty=D(trade['qty']); fee=qty*exit_price*self.cfg.fee_rate
        pnl=trade_pnl(trade['side'],qty,D(trade['entry']),exit_price)-D(trade['fee_open'])-fee+D(trade['funding'] or 0)
        ts=ts or int(time.time())
        with self.db:
            changed=self.db.execute("UPDATE trades SET exit=?,closed=?,fee_close=?,pnl=?,reason=?,status='CLOSED' WHERE id=? AND status='OPEN'",(str(exit_price),ts,str(fee),str(pnl),reason,trade['id'])).rowcount
            if not changed:return None
            self.db.execute("INSERT OR IGNORE INTO paper_fills(trade_id,ts,action,price,qty,fee) VALUES(?,?,?,?,?,?)",(trade['id'],ts,'CLOSE',str(exit_price),str(qty),str(fee)))
        return pnl
    def manage(self,quotes,stats,now=None):
        now=now or int(time.time()); results=[]
        for trade in self.positions():
            q=quotes.get(trade['symbol']); st=stats.get(trade['symbol'],{})
            if not q or now-q['ts']>10:continue
            bid,ask=D(q['bid']),D(q['ask'])
            exit_price=bid if trade['side']=='LONG' else ask
            movement=(exit_price/D(trade['entry'])-1)*(1 if trade['side']=='LONG' else -1)
            reason=None
            if movement<=-self.cfg.stop_pct:reason='STOP_LOSS'
            elif now-trade['opened']>=self.cfg.max_minutes*60:reason='TIME_LIMIT'
            elif st.get('z') is not None and abs(st['z'])<=0.5:reason='NORMALIZED'
            if reason=='NORMALIZED':
                net=self.net_exit_pnl(trade,bid,ask)
                self.record_exit(trade,q,st,now,'NORMALIZATION_OBSERVED',reason,net)
            if reason:
                net=self.net_exit_pnl(trade,bid,ask)
                self.record_exit(trade,q,st,now,'EXIT',reason,net)
                pnl=self.close(trade,bid,ask,reason,now)
                if pnl is not None:results.append((trade['symbol'],reason,str(pnl)))
        return results
    def recovery(self,quotes,now=None):
        """Gap positions close at first verified quote; no fictitious stop fills."""
        now=now or int(time.time()); out=[]
        for trade in self.positions():
            q=quotes.get(trade['symbol'])
            if q and now-q['ts']<=10:
                pnl=self.close(trade,q['bid'],q['ask'],'RECOVERY_GAP',now)
                if pnl is not None:out.append((trade['symbol'],str(pnl)))
        return out
    def log_signal(self,symbol,side,z,p,reason):
        with self.db:self.db.execute('INSERT INTO signal_log(ts,symbol,side,z,premium,reason) VALUES(?,?,?,?,?,?)',(int(time.time()),symbol,side,z,p,reason))
