"""Cache reference price indexes for the same frozen dates as the first backtest."""
import concurrent.futures,datetime,hashlib,json,pathlib,urllib.parse,urllib.request,zoneinfo
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'research/history-2026-10-23'
def stamp(d):return int(datetime.datetime.fromisoformat(d).replace(tzinfo=datetime.timezone.utc).timestamp())
def fetch(symbol):
    params={'period1':stamp('2021-05-01'),'period2':stamp('2026-10-23'),'interval':'1d'}
    url='https://query1.finance.yahoo.com/v8/finance/chart/'+urllib.parse.quote(symbol,safe='')+'?'+urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=45) as response:raw=response.read()
    result=json.loads(raw)['chart']['result'][0]
    assert result['meta']['symbol']==symbol
    (OUT/'raw'/f'{symbol}.json').write_bytes(raw)
    q=result['indicators']['quote'][0];tz=zoneinfo.ZoneInfo(result['meta']['exchangeTimezoneName']);bars=[]
    for i,t in enumerate(result['timestamp']):
        date=datetime.datetime.fromtimestamp(t,tz).date().isoformat()
        if date>'2026-10-22' or q['open'][i] is None or q['close'][i] is None:continue
        assert q['open'][i]>0 and q['close'][i]>0
        bars.append({'date':date,'open':q['open'][i],'close':q['close'][i]})
    assert bars[-1]['date']=='2026-10-22'
    return {'symbol':symbol,'currency':result['meta']['currency'],'timezone':str(tz),'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'bars':bars}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:series=list(pool.map(fetch,['^GSPC','^N225']))
output={'retrievedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'series':series}
(OUT/'benchmarks.json').write_text(json.dumps(output),encoding='utf-8')
for s in series:print(json.dumps({'symbol':s['symbol'],'rows':len(s['bars']),'first':s['bars'][0],'last':s['bars'][-1]}))
