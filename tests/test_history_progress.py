import sqlite3
from unittest.mock import patch
from ayla import app as appmod


def test_history_progress_minute_coverage(tmp_path):
    db=sqlite3.connect(tmp_path/'history.sqlite'); db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE samples(symbol TEXT, ts INTEGER, premium REAL, mark REAL, idx REAL, PRIMARY KEY(symbol, ts))')
    now=200000
    rows=[('BTC-USDT',now-86400+i*60,0.,100.,100.) for i in range(1440)]
    rows.append(('BTC-USDT',now-86400+31,0.,100.,100.))
    db.executemany('INSERT INTO samples VALUES (?,?,?,?,?)',rows)
    with patch.object(appmod,'db',db):
        appmod.history_progress_cache.update(ts=0,data={})
        h=appmod.history_progress(['BTC-USDT'],now)['BTC-USDT']
        assert h['ready'] and h['coverage_percent']>=99
        assert h['observed_minutes']==1440
        db.execute('DELETE FROM samples WHERE ts BETWEEN ? AND ?', (now-30000,now-500))
        appmod.history_progress_cache.update(ts=0,data={})
        h=appmod.history_progress(['BTC-USDT'],now)['BTC-USDT']
        assert not h['ready']
    db.close()
