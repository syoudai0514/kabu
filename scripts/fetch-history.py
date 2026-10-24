"""Fetch split-only daily Yahoo prices; cache is local and excluded from Git."""
import concurrent.futures,datetime,hashlib,json,pathlib,urllib.parse,urllib.request,zoneinfo
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'research/history-2026-10-23'
TICKERS=['7203.T','6758.T','8306.T','AAPL','MSFT','NVDA','JPY=X']
START='2021-05-01';END_EXCLUSIVE='2026-10-23'
def stamp(d):return int(datetime.datetime.fromisoformat(d).replace(tzinfo=datetime.timezone.utc).timestamp())
def fetch(ticker):
    params={'period1':stamp(START),'period2':stamp(END_EXCLUSIVE),'interval':'1d','events':'div,splits'}
    url='https://query1.finance.yahoo.com/v8/finance/chart/'+urllib.parse.quote(ticker,safe='')+'?'+urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=45) as response:
        raw=response.read()
    result=json.loads(raw)['chart']['result'][0]
    if result['meta']['symbol']!=ticker:raise ValueError(f'Unexpected symbol: {ticker} -> {result["meta"]["symbol"]}')
    (OUT/'raw'/f'{ticker}.json').write_bytes(raw)
    quote=result['indicators']['quote'][0]
    tz=zoneinfo.ZoneInfo(result['meta']['exchangeTimezoneName'])
    rows=[]
    for i,t in enumerate(result['timestamp']):
        if quote['open'][i] is None or quote['close'][i] is None:continue
        date=datetime.datetime.fromtimestamp(t,tz).date().isoformat()
        if date>=END_EXCLUSIVE:continue
        o,c=quote['open'][i],quote['close'][i]
        if not (o>0 and c>0):raise ValueError('Nonpositive price')
        rows.append({'date':date,'open':o,'close':c})
    splits=[]
    for split in result.get('events',{}).get('splits',{}).values():
        splits.append({'date':datetime.datetime.fromtimestamp(split['date'],tz).date().isoformat(),'ratio':split['numerator']/split['denominator']})
    return {'symbol':ticker,'timezone':str(tz),'currency':result['meta']['currency'],'bars':rows,'splits':sorted(splits,key=lambda x:x['date']),'url':url,'sha256':hashlib.sha256(raw).hexdigest()}
OUT.mkdir(parents=True,exist_ok=True);(OUT/'raw').mkdir(exist_ok=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:results=list(pool.map(fetch,TICKERS))
for d in results:print(json.dumps({'symbol':d['symbol'],'rows':len(d['bars']),'first':d['bars'][0],'last':d['bars'][-1],'splits':d['splits']},ensure_ascii=False))
manifest={'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'requested_start':START,'requested_end_exclusive':END_EXCLUSIVE,'series':results}
(OUT/'market-data.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
