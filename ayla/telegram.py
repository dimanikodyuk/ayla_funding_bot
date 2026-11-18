"""Telegram notifications. Widget API may not be available on every bot/client."""
import html as html_lib
import os
import zlib
import logging
import asyncio
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx

log=logging.getLogger(__name__)

def _fmt(value, places=5):
    if value is None:return '—'
    try:return f'{float(value):+.{places}f}'
    except (TypeError,ValueError):return '—'

def _clock(ts):
    if not ts:return '—'
    return datetime.fromtimestamp(int(ts),ZoneInfo('Europe/Kyiv')).strftime('%d.%m %H:%M:%S')

def trade_card(trade, entry=None, exit=None):
    """Return a small self-contained profile-1 HTML widget. Never embed secrets."""
    entry=entry or {};exit=exit or {}
    closed=trade.get('status')=='CLOSED'
    pnl=trade.get('pnl') if closed else None
    profit=(float(pnl or 0)>=0) if closed else None
    title='ЗАКРИТО' if closed else 'ВІДКРИТО'
    symbol=html_lib.escape(str(trade.get('symbol','?')))
    side=html_lib.escape(str(trade.get('side','?')))
    reason=html_lib.escape(str(trade.get('reason') or ('PAPER_OPEN' if not closed else '—')))
    hold=max(0,int(trade.get('closed') or 0)-int(trade.get('opened') or 0)) if closed else None
    fee=sum(float(trade.get(k) or 0) for k in ('fee_open','fee_close'))
    def v(x):return html_lib.escape(str(x if x is not None else '—'))
    def row(label,value):return f'<div class="r"><span>{html_lib.escape(label)}</span><b>{v(value)}</b></div>'
    rows=''.join([
      row('Вхід Z',_fmt(entry.get('z'),2)),row('Вихід Z',_fmt(exit.get('z'),2)),
      row('Премія вхід',_fmt(entry.get('premium'),4)+'%'),row('Премія вихід',_fmt(exit.get('premium'),4)+'%'),
      row('Вхід',trade.get('entry')),row('Вихід',trade.get('exit') if closed else '—'),
      row('Комісії',f'{fee:.6f} USDT'),row('Утримання',f'{hold} с' if hold is not None else '—'),
      row('Відкрито',_clock(trade.get('opened'))),row('Закрито',_clock(trade.get('closed'))),
      row('Причина',reason),
    ])
    amount=(_fmt(pnl,5)+' USDT') if closed else 'Очікує закриття'
    color='#5bd6a0' if profit else '#ff8585' if closed else '#a6c9ff'
    markup=f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;padding:12px;background:#15212d;color:#f1f5fa;font:14px Arial,sans-serif}}
.top{{color:#8fa8bf;font-size:11px;letter-spacing:1px}}h2{{margin:9px 0 4px;font-size:21px}}
.p{{color:{color};font-size:22px;font-weight:bold;margin:10px 0}}.sub{{color:#a9bacb;font-size:12px}}
.r{{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #304152;gap:8px}}
.r span{{color:#a9bacb}}.r b{{text-align:right}}button{{margin-top:10px;padding:9px;border:1px solid #536579;border-radius:7px;background:#263849;color:white;width:100%}}
#detail{{display:none}}
</style></head><body><div class="top">AYLA FUNDING BOT · PAPER · {title}</div>
<h2>{symbol}</h2><div class="sub">{side} · #{v(trade.get('id'))} · {reason}</div>
<div class="p">{html_lib.escape(amount)}</div>
<button id="toggle">Деталі угоди ▾</button><div id="detail">{rows}</div>
<script>var b=document.getElementById('toggle'),d=document.getElementById('detail');b.addEventListener('click',function(){{var o=d.style.display==='block';d.style.display=o?'none':'block';b.textContent=o?'Деталі угоди ▾':'Згорнути ▴'}});</script>
</body></html>"""
    raw=markup.encode('utf-8')
    if len(raw)>32768 or len(zlib.compress(raw,9))>4096:raise ValueError('Telegram widget size limit exceeded')
    return markup

async def _send(method,payload):
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip();chat=os.getenv('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat:return False,'not_configured'
    payload={'chat_id':chat,**payload}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response=await client.post(f'https://api.telegram.org/bot{token}/{method}',json=payload)
            result=response.json()
            if response.status_code==200 and result.get('ok'):return True,None
            return False,str(result.get('description') or f'HTTP {response.status_code}')[:180]
    except (httpx.HTTPError,ValueError) as exc:return False,type(exc).__name__

async def notify(message, markup=None):
    payload={'text':str(message)[:3500]}
    if markup:payload['reply_markup']=markup
    ok,error=await _send('sendMessage',payload)
    if not ok and error!='not_configured':log.warning('Telegram text delivery failed: %s',error)
    return ok

async def notify_trade(trade,entry=None,exit=None):
    """Try a true message widget; fall back to a readable text message.

    Do not silently substitute an HTML document attachment or a Mini App.
    """
    closed=trade.get('status')=='CLOSED'
    pnl=_fmt(trade.get('pnl'),5) if closed else '—'
    text=(f"🐾 PAPER {'CLOSE' if closed else 'OPEN'} #{trade.get('id')} "
          f"{trade.get('side')} {trade.get('symbol')}\n"
          f"Z: {_fmt((entry or {}).get('z'),2)} → {_fmt((exit or {}).get('z'),2)}\n"
          f"Premium: {_fmt((entry or {}).get('premium'),4)}% → {_fmt((exit or {}).get('premium'),4)}%\n"
          f"Entry: {trade.get('entry')} | Exit: {trade.get('exit') or '—'}\n"
          f"PnL: {pnl} USDT | {trade.get('reason') or 'PAPER_OPEN'}")
    if os.getenv('TELEGRAM_WIDGETS_ENABLED','0').strip().lower() not in ('0','false','no'):
        try:
            card=trade_card(trade,entry,exit)
            ok,error=await _send('sendWidget',{'html':card,'width':320,'height':250,'caption':text[:900]})
            if ok:return 'widget'
            if error!='not_configured':log.warning('Telegram sendWidget unavailable (%s); text fallback',error)
        except Exception as exc:log.warning('Telegram widget render failed: %s',type(exc).__name__)
    # Optional standard Telegram inline URL button to a publicly reachable HTTPS dashboard.
    # Never expose a local/private dashboard URL or API token in Telegram.
    public_url=os.getenv('TELEGRAM_PUBLIC_DASHBOARD_URL','').strip()
    if public_url.startswith('https://'):
        from urllib.parse import urlsplit
        host=urlsplit(public_url).hostname or ''
        if host not in ('localhost','127.0.0.1') and not host.endswith('.local'):
            ok,error=await _send('sendMessage',{'text':text,'reply_markup':{'inline_keyboard':[[{'text':'📈 Відкрити Trade Replay','url':public_url.rstrip('/')+'/#trades'}]]}})
            if ok:return 'inline_url'
            log.warning('Telegram inline URL failed (%s); text fallback',error)
    await notify(text, {'inline_keyboard':[[
        {'text':'📊 Деталі та витрати','callback_data':f"ayla:cost:{int(trade['id'])}"},
        {'text':'📈 Аналіз сигналу','callback_data':f"ayla:signal:{int(trade['id'])}"}
    ]]})
    return 'text'


# Telegram Bot API polling is separate from trading. Only read-only callbacks
# are accepted; no callback can enable Paper Trading or place orders.
def _money(v, places=4):
    try:return f'{Decimal(str(v or 0)):+.{places}f}'
    except (InvalidOperation,TypeError,ValueError):return '—'

def _trade_detail(db, trade_id, section):
    row=db.execute('SELECT * FROM trades WHERE id=?',(trade_id,)).fetchone()
    if row is None:return '✅ Кнопки Telegram працюють. Угоди можна переглядати після наступного Paper-входу.'
    t=dict(row)
    title=f"🐾 PAPER #{t['id']} · {t['side']} {t['symbol']}"
    if section=='cost':
        qty=Decimal(str(t['qty']));entry=Decimal(str(t['entry']))
        exit_price=Decimal(str(t['exit'])) if t.get('exit') is not None else None
        gross=((exit_price-entry)*qty*(1 if t['side']=='LONG' else -1)) if exit_price is not None else None
        fee_open=Decimal(str(t.get('fee_open') or 0));fee_close=Decimal(str(t.get('fee_close') or 0))
        funding=Decimal(str(t.get('funding') or 0))
        return (f'{title}\n\n💵 Витрати та результат\n'
            f'Gross PnL: {_money(gross) if gross is not None else "—"} USDT\n'
            f'Комісія входу: {_money(-fee_open)} USDT\n'
            f'Комісія виходу: {_money(-fee_close) if exit_price is not None else "—"} USDT\n'
            f'Funding: {_money(funding)} USDT\n'
            f'Net PnL: {_money(t.get("pnl")) if exit_price is not None else "—"} USDT\n'
            'Спред уже врахований у цінах Bid/Ask; вдруге не віднімається.')
    rows=db.execute("SELECT event,z,premium,ts,reason FROM exit_decisions WHERE trade_id=? AND event IN ('ENTRY','EXIT') ORDER BY ts",(trade_id,)).fetchall()
    decisions={r['event']:dict(r) for r in rows}
    def fmt(event,key):
        value=decisions.get(event,{}).get(key)
        return f'{float(value):+.2f}' if value is not None else '—'
    return (f'{title}\n\n📈 Сигнал\n'
        f'Z-score: {fmt("ENTRY","z")} → {fmt("EXIT","z")}\n'
        f'Premium: {fmt("ENTRY","premium")}% → {fmt("EXIT","premium")}%\n'
        f'Вхід: {t["entry"]}\nВихід: {t.get("exit") or "—"}\n'
        f'Причина: {t.get("reason") or "PAPER_OPEN"}\n'
        'Графік доступний у локальній вебпанелі → Угоди → 📈. '
        'Для відкриття через Telegram потрібна доступна HTTPS-адреса.')

async def _bot_api(client,method,payload):
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    response=await client.post(f'https://api.telegram.org/bot{token}/{method}',json=payload,timeout=35)
    data=response.json()
    if not data.get('ok'):raise RuntimeError(str(data.get('description','Bot API error'))[:180])
    return data['result']

async def telegram_poll(db):
    """Read-only Telegram callbacks. Errors never interrupt market collection."""
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    chat=os.getenv('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat:return
    if os.getenv('TELEGRAM_INTERACTIVE_ENABLED','1').lower() in ('0','false','no'):return
    offset=None
    async with httpx.AsyncClient(timeout=40) as client:
        while True:
            try:
                updates=await _bot_api(client,'getUpdates',{
                    'offset':offset,'timeout':25,'allowed_updates':['message','callback_query']})
                for update in updates:
                    offset=update['update_id']+1
                    callback=update.get('callback_query')
                    if callback:
                        msg=callback.get('message') or {}
                        source_chat=str((msg.get('chat') or {}).get('id',''))
                        data=str(callback.get('data',''))
                        if source_chat!=chat or not data.startswith('ayla:'):
                            continue
                        await _bot_api(client,'answerCallbackQuery',{'callback_query_id':callback['id']})
                        parts=data.split(':')
                        if len(parts)==3 and parts[1] in ('cost','signal') and parts[2].isdigit():
                            details=_trade_detail(db,int(parts[2]),parts[1])
                            await _bot_api(client,'sendMessage',{'chat_id':chat,'text':details[:3500]})
                    else:
                        msg=update.get('message') or {}
                        if str((msg.get('chat') or {}).get('id',''))!=chat:continue
                        command=(msg.get('text') or '').split(' ',1)[0].split('@')[0]
                        if command=='/widget_test':
                            await _bot_api(client,'sendMessage',{'chat_id':chat,
                                'text':'🐾 Ayla Funding Bot — перевірка інтерактивних кнопок',
                                'reply_markup':{'inline_keyboard':[[{'text':'✅ Перевірити кнопку','callback_data':'ayla:signal:0'}]]}})
            except asyncio.CancelledError:raise
            except Exception as exc:
                log.warning('Telegram polling failed; retry in 10s: %s',str(exc)[:160])
                await asyncio.sleep(10)
