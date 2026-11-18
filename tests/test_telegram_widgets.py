import zlib
from ayla.telegram import trade_card

def test_open_widget():
    html=trade_card({'id':4,'symbol':'EVAA-USDT','side':'LONG','entry':'3.4','opened':1794996267,'status':'OPEN'}, {'z':-5.79,'premium':-0.22})
    assert 'EVAA-USDT' in html and 'Деталі угоди' in html
    assert len(zlib.compress(html.encode(),9))<=4096

def test_closed_widget_escapes_and_displays_pnl():
    html=trade_card({'id':5,'symbol':'<bad>','side':'SHORT','entry':'0.1','exit':'0.09','opened':1794996267,'closed':1794996292,'status':'CLOSED','pnl':'0.01','fee_open':'0.01','fee_close':'0.01','reason':'NORMALIZED'}, {'z':3.3}, {'z':0.2})
    assert '&lt;bad&gt;' in html and '<bad>' not in html
    assert '+0.01000' in html and 'NORMALIZED' in html
