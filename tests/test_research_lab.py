import unittest
from ayla.research_lab import analyze, research_report
from ayla.db import connect

class ResearchLabTests(unittest.TestCase):
    def test_no_lookahead_and_gaps(self):
        rows=[(i,0.0,100.0,100.0) for i in range(100)]
        rows[90]=(90,0.3,100.0,100.0)
        report=analyze(rows,60)
        self.assertGreaterEqual(report['candidate_signals'],1)
        self.assertEqual(report['horizons'][0]['completed'],1)
        rows=[r for r in rows if r[0]!=96]
        report=analyze(rows,60)
        self.assertEqual(report['horizons'][0]['completed'],0)

    def test_read_only(self):
        db=connect(':memory:')
        with db:
            db.executemany('INSERT INTO samples(symbol,ts,premium,mark,idx) VALUES(?,?,?,?,?)',
                           [('BTC-USDT',i*60,0.0,100.0,100.0) for i in range(80)])
        before=db.total_changes
        result=research_report(db,'BTC-USDT',now=80*60)
        self.assertEqual(db.total_changes,before)
        self.assertEqual(result['execution'],'READ_ONLY_SHADOW')
        self.assertEqual(len(result['results']),3)
        db.close()

if __name__=='__main__':unittest.main()
