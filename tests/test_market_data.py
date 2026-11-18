from ayla.market_data import ticker_snapshot,fresh_quote,spread_pct

def test_valid_and_invalid():
    books,vol,bad=ticker_snapshot([{'symbol':'BTC-USDT','bidPrice':'100','askPrice':'101','quoteVolume':'1234'},{'symbol':'BAD-USDT','bidPrice':'3','askPrice':'2'}],100)
    assert len(books)==1 and bad==1 and float(vol['BTC-USDT'])==1234
    assert round(spread_pct(books['BTC-USDT']),3)==0.995

def test_freshness_and_invalid():
    books,_,_=ticker_snapshot([{'symbol':'BTC-USDT','bidPrice':'100','askPrice':'101'}],100)
    assert fresh_quote(books,'BTC-USDT',110)
    assert fresh_quote(books,'BTC-USDT',111) is None
    assert fresh_quote(books,'BTC-USDT',99) is None
    assert fresh_quote(books,'OTHER',100) is None
