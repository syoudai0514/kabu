import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {metrics} from '../src/engine.js';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const dir=path.join(root,'research/history-2026-10-23');
const strategy=JSON.parse(fs.readFileSync(path.join(dir,'results.json'),'utf8'));
const benchmarkSource=JSON.parse(fs.readFileSync(path.join(dir,'benchmarks.json'),'utf8'));
const stocks=JSON.parse(fs.readFileSync(path.join(dir,'market-data.json'),'utf8'));
const fx=stocks.series.find(s=>s.symbol==='JPY=X').bars;
const lastAt=(rows,date)=>rows.findLast(b=>b.date<=date);
const forex=date=>lastAt(fx,date).close;
const average=xs=>xs.reduce((s,x)=>s+x,0)/xs.length;
function summary(history){return {final:history.at(-1).value,...metrics(history,1000000)};}
const comparisons=[];
for(const run of strategy.runs.filter(r=>r.summary.risk==='high')){
  const dates=run.history.map(h=>h.date),initial=run.summary.initial;
  const refs={};
  for(const s of benchmarkSource.series){
    const first=s.bars.find(b=>b.date>=run.summary.requestedStart);
    const startFX=s.symbol==='^GSPC'?forex(first.date):1;
    const history=dates.map(date=>{const b=lastAt(s.bars,date);return {date,value:date<first.date?initial:initial*b.close/first.open*(s.symbol==='^GSPC'?forex(date)/startFX:1)};});
    const localHistory=dates.map(date=>{const b=lastAt(s.bars,date);return {date,value:date<first.date?initial:initial*b.close/first.open};});
    refs[s.symbol]={entryDate:first.date,entryOpen:first.open,entryFX:startFX,lastClose:lastAt(s.bars,strategy.end).close,endFX:s.symbol==='^GSPC'?forex(strategy.end):1,...summary(history),localCurrencyReturn:summary(localHistory).return};
    if(s.symbol==='^GSPC')refs.spHistory=history;else refs.nkHistory=history;
  }
  const blend=refs.spHistory.map((h,i)=>({date:h.date,value:.5*h.value+.5*refs.nkHistory[i].value}));
  const diluted=blend.map(h=>({date:h.date,value:initial*.7+h.value*.3}));
  const exposures=run.history.slice(1).map(h=>(h.value-h.cash)/h.value);
  const inventory={};const contributions={};const holdingDays=[];let slipCost=0,turnover=0;
  for(const t of run.trades.filter(t=>t.status==='filled')){
    const gross=t.qty*t.price*t.fx;turnover+=gross;slipCost+=gross*.0005/(t.side==='BUY'?1.0005:.9995);
    if(t.side==='BUY')inventory[t.symbol]={date:t.date};
    else{const held=inventory[t.symbol];if(held){holdingDays.push((Date.parse(t.date)-Date.parse(held.date))/86400000);delete inventory[t.symbol];}contributions[t.symbol]=(contributions[t.symbol]||0)+t.pnl;}
  }
  for(const h of run.summary.holdings)contributions[h.symbol]=(contributions[h.symbol]||0)+h.value-h.cost;
  const item={...run.summary,benchmarks:{sp500JPY:refs['^GSPC'],nikkei225JPY:refs['^N225'],halfJapanHalfUS:summary(blend),stocks30Cash70:summary(diluted)},
    diagnostics:{averageStockExposure:average(exposures),maxStockExposure:Math.max(...exposures),endStockExposure:(run.summary.final-run.summary.cash)/run.summary.final,
    fractionFullyCash:exposures.filter(x=>x===0).length/exposures.length,explicitFees:run.summary.fees,estimatedSlippageCost:slipCost,
    grossTradedNotional:turnover,notionalToAverageEquity:turnover/average(run.history.map(h=>h.value)),
    meanClosedHoldingDays:average(holdingDays),medianClosedHoldingDays:[...holdingDays].sort((a,b)=>a-b)[Math.floor(holdingDays.length/2)],
    contributionsBySymbol:contributions}};
  comparisons.push(item);
  console.log(JSON.stringify(item));
}
const result={end:strategy.end,initial:strategy.initial,benchmarkRetrievedAt:benchmarkSource.retrievedAt,
  methodology:'Enter first available session open on/after each requested start; price-only index; S&P converted to JPY with same daily FX convention as strategy; no index costs or dividends; cash 0 interest; blend/diluted references fixed initial allocations held without rebalancing.',
  sources:benchmarkSource.series.map(({symbol,url,sha256,bars})=>({symbol,url,sha256,rows:bars.length,first:bars[0].date,last:bars.at(-1).date})),comparisons};
fs.writeFileSync(path.join(root,'reports/benchmark-review-2026-10-24-summary.json'),JSON.stringify(result,null,2)+'\n');
