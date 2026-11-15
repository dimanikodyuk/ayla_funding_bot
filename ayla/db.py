import sqlite3
from pathlib import Path

SCHEMA='''
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS samples(symbol TEXT NOT NULL,ts INTEGER NOT NULL,premium REAL NOT NULL,mark REAL NOT NULL,idx REAL NOT NULL,PRIMARY KEY(symbol,ts));
CREATE INDEX IF NOT EXISTS ix_samples_time ON samples(ts);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,side TEXT,qty TEXT,entry TEXT,exit TEXT,opened INTEGER,closed INTEGER,fee_open TEXT,fee_close TEXT,funding TEXT DEFAULT '0',pnl TEXT,reason TEXT,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,level TEXT,message TEXT);
CREATE TABLE IF NOT EXISTS funding_events(symbol TEXT,settlement INTEGER,trade_id INTEGER,rate TEXT,cashflow TEXT,PRIMARY KEY(symbol,settlement,trade_id));
'''

def connect(path='data/ayla.sqlite'):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,check_same_thread=False)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('PRAGMA busy_timeout=5000')
    db.executescript(SCHEMA)
    db.commit()
    return db

def setting(db,key,default=None):
    row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return row['value'] if row else default

def set_setting(db,key,value):
    with db: db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
