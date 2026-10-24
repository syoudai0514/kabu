import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {STOCKS,calendar,barAt,newAccount,strategyOrders,advance,equity,metrics} from '../src/engine.js';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const source=JSON.parse(fs.readFileSync(path.join(root,'research/history-2026-10-23/market-data.json'),'utf8'));
const end='2026-10-22';
const prices=source.series.filter(s=>s.symbol!=='JPY=X');
const fx=source.series.find(s=>s.symbol==='JPY=X').bars;
function splitFactor(series,date){return series.splits.filter(s=>s.date>date&&s.date<=end).reduce((f,s)=>f*s.ratio,1);}
function forex(date){const b=fx.findLast(b=>b.date<=date);if(!b)throw Error('FX unavailable: '+date);return b.close;}
for(const series of source.series){
  if(series.bars.at(-1).date!==end)throw Error('Not current: '+series.symbol);
  if(new Set(series.bars.map(b=>b.date)).size!==series.bars.length)throw Error('Duplicate dates');
  for(let i=1;i<series.bars.length;i++)if(series.bars[i].date<=series.bars[i-1].date)throw Error('Unsorted');
}
const base={source:'historical',assets:STOCKS.map(stock=>{
  const series=prices.find(s=>s.symbol===(stock.market==='JP'?stock.symbol+'.T':stock.symbol));
  if(!series)throw Error('Missing symbol');
  return {...stock,splits:series.splits,bars:series.bars.filter(b=>b.date<=end).map(b=>({...b,fx:stock.market==='US'?forex(b.date):1}))};
})};
const dates=calendar(base);
const costs={feeBps:10,slipBps:5,fxBps:10};
const specs=[{label:'5年前',start:'2021-10-23'},{label:'1年前',start:'2025-10-23'},{label:'半年前',start:'2026-04-23'}];
const runs=[];
for(const risk of ['high','medium'])for(const spec of specs){
  const data=structuredClone(base);
  const previous=dates.findLast(d=>d<spec.start);
  if(data.assets.some(a=>barAt(a,previous).index<59))throw Error('Insufficient warmup');
  const account=newAccount(1000000,risk,previous);
  account.history.push({date:previous,value:account.initial,cash:account.cash});
  for(const date of dates.filter(d=>d>=spec.start)){
    // Yahoo quote OHLC is already split-adjusted. A historical lot must be
    // expressed in the same share units. Otherwise a future split enables
    // impossible fractional/cheap historical purchases (e.g. Sony 5-for-1).
    for(const asset of data.assets)asset.lot=(asset.market==='JP'?100:1)*splitFactor(asset,account.date);
    strategyOrders(account,data,account.date);
    advance(account,data,date,costs);
  }
  const filled=account.trades.filter(t=>t.status==='filled');
  const summary={label:spec.label,risk,requestedStart:spec.start,firstSession:dates.find(d=>d>=spec.start),firstFill:filled[0]?.date||null,end,
    initial:account.initial,final:equity(account,data,end),profit:equity(account,data,end)-account.initial,...metrics(account.history,account.initial),
    cash:account.cash,filled:filled.length,buys:filled.filter(t=>t.side==='BUY').length,sells:filled.filter(t=>t.side==='SELL').length,
    rejected:account.trades.length-filled.length,fees:filled.reduce((s,t)=>s+t.fee,0),halted:account.halted,
    holdings:Object.entries(account.positions).map(([symbol,p])=>({symbol,...p,value:p.qty*barAt(data.assets.find(s=>s.symbol===symbol),end).close*barAt(data.assets.find(s=>s.symbol===symbol),end).fx})),
    tradedSymbols:[...new Set(filled.map(t=>t.symbol))],pendingOrders:account.orders.length};
  // Ledger uses contemporaneous share counts and prices, not normalized units.
  const ledger=account.trades.map(t=>{const factor=splitFactor(data.assets.find(s=>s.symbol===t.symbol),t.date);return {...t,normalizedQty:t.qty,normalizedPrice:t.price,qty:t.qty/factor,price:t.price===null?null:t.price*factor};});
  runs.push({summary,history:account.history,trades:ledger});
  console.log(JSON.stringify(summary));
}
const report={asOf:'2026-10-23',end,initial:1000000,costs,sourceRetrievedAt:source.retrieved_at,engineGitRevision:'acbe4f1',
  sources:source.series.map(({symbol,url,sha256,bars,splits})=>({symbol,url,sha256,rows:bars.length,first:bars[0].date,last:bars.at(-1).date,splits})),runs};
fs.writeFileSync(path.join(root,'research/history-2026-10-23/results.json'),JSON.stringify(report,null,2));
// Publish derived account summaries and provenance, keeping price data local.
fs.mkdirSync(path.join(root,'reports'),{recursive:true});
fs.writeFileSync(path.join(root,'reports/historical-2026-10-23-summary.json'),JSON.stringify({...report,runs:runs.map(({summary})=>({summary}))},null,2)+'\n');
