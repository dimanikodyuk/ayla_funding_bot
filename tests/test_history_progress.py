import sqlite3
from unittest.mock import patch
from ayla import app as appmod


def test_history_progress_uses_persisted_samples_and_gaps(tmp_path):
    db = sqlite3.connect(tmp_path / 'history.sqlite')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE samples(symbol TEXT, ts INTEGER, premium REAL, mark REAL, idx REAL, PRIMARY KEY(symbol, ts))')
    now = 200000
    rows = [('BTC-USDT', now-86400+i*5, 0.0, 100.0, 100.0) for i in range(13825)]
    db.executemany('INSERT INTO samples VALUES (?,?,?,?,?)', rows)
    with patch.object(appmod, 'db', db):
        appmod.history_progress_cache.update(ts=0, data={})
        h = appmod.history_progress(['BTC-USDT'], now)['BTC-USDT']
        assert h['sample_count'] == 13825
        assert h['ready'] is True
        assert h['coverage_percent'] >= 80
        db.execute('DELETE FROM samples WHERE ts < ?', (now-10000,))
        appmod.history_progress_cache.update(ts=0, data={})
        h = appmod.history_progress(['BTC-USDT'], now)['BTC-USDT']
        assert h['ready'] is False
        assert h['observed_seconds'] <= 10000
    db.close()
