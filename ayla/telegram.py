import os
import httpx

async def notify(message):
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); chat=os.getenv('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat:return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':message[:3500]})
    except Exception:pass
