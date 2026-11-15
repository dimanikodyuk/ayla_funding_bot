import httpx

BASE='https://open-api.bingx.com'

class BingX:
    def __init__(self): self.client=httpx.AsyncClient(base_url=BASE,timeout=12)
    async def get(self,path,params=None):
        r=await self.client.get(path,params=params); r.raise_for_status()
        body=r.json()
        if str(body.get('code','0'))!='0': raise RuntimeError(f'BingX: {body.get("msg",body.get("code"))}')
        return body.get('data')
    async def contracts(self): return await self.get('/openApi/swap/v2/quote/contracts')
    async def premiums(self): return await self.get('/openApi/swap/v2/quote/premiumIndex')
    async def ticker(self): return await self.get('/openApi/swap/v2/quote/ticker')
    async def book(self,symbol): return await self.get('/openApi/swap/v2/quote/bookTicker',{'symbol':symbol})
    async def close(self): await self.client.aclose()
