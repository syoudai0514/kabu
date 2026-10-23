import test from 'node:test';
import assert from 'node:assert/strict';
import {demoData,parseCSV,calendar,newAccount,queueOrder,advance,equity,signal,backtest,monteCarlo,metrics} from '../src/engine.js';
const asset=(symbol='TEST',market='US',bars=[{date:'2024-01-01',open:10,close:10,fx:market==='US'?100:1},{date:'2024-01-02',open:12,close:14,fx:market==='US'?110:1},{date:'2024-01-03',open:11,close:13,fx:market==='US'?120:1}])=>({symbol,name:symbol,market,currency:market==='US'?'USD':'JPY',lot:market==='US'?1:100,bars});
const costs={feeBps:0,slipBps:0,fxBps:0};
test('buy and sell use next open, currency conversion, and exact fees',()=>{
 const data={assets:[asset()]},a=newAccount(100000,'high','2024-01-01');queueOrder(a,data,'TEST','BUY',10);assert.equal(a.cash,100000);
 advance(a,data,'2024-01-02',{feeBps:10,slipBps:5,fxBps:10});
 const gross=12*1.0005*110*10;assert.ok(Math.abs(a.cash-(100000-gross*1.002))<1e-7);assert.equal(a.positions.TEST.qty,10);
 assert.ok(Math.abs(equity(a,data,a.date)-(a.cash+15400))<1e-7);
 queueOrder(a,data,'TEST','SELL',10);advance(a,data,'2024-01-03',costs);assert.equal(a.positions.TEST,undefined);assert.ok(Math.abs(a.trades[1].pnl-(13200-gross*1.002))<1e-7);
});
test('invalid lots, shorts, duplicate orders, exposure and insufficient cash are blocked',()=>{
 const data={assets:[asset(),asset('JP','JP')]},a=newAccount(100000,'medium','2024-01-01');
 assert.throws(()=>queueOrder(a,data,'JP','BUY',1));assert.throws(()=>queueOrder(a,data,'TEST','BUY',-1));assert.throws(()=>queueOrder(a,data,'TEST','BUY',1.5));assert.throws(()=>queueOrder(a,data,'TEST','SELL',1));
 queueOrder(a,data,'TEST','BUY',20);assert.throws(()=>queueOrder(a,data,'TEST','BUY',1));advance(a,data,'2024-01-02',costs);assert.equal(a.trades[0].message,'1銘柄の比率上限');assert.equal(a.cash,100000);
 queueOrder(a,data,'TEST','BUY',1000);advance(a,data,'2024-01-03',costs);assert.equal(a.trades.at(-1).message,'買付余力不足');
});
test('orders wait across a market holiday and use that market next open',()=>{
 const data={assets:[asset('TEST','US',[{date:'2024-01-01',open:10,close:10,fx:100},{date:'2024-01-03',open:11,close:12,fx:100}])]},a=newAccount(100000,'high','2024-01-01');
 queueOrder(a,data,'TEST','BUY',1);advance(a,data,'2024-01-02',costs);assert.equal(a.orders.length,1);assert.equal(a.trades.length,0);advance(a,data,'2024-01-03',costs);assert.equal(a.cash,98900);
});
test('close-based stop queues a next-open exit and can gap beyond stop',()=>{
 const bars=[{date:'2024-01-01',open:10,close:10,fx:100},{date:'2024-01-02',open:10,close:8,fx:100},{date:'2024-01-03',open:6,close:7,fx:100}];
 const data={assets:[asset('TEST','US',bars)]},a=newAccount(100000,'high','2024-01-01');queueOrder(a,data,'TEST','BUY',10);advance(a,data,'2024-01-02',costs);assert.equal(a.orders[0].reason,'損切り');assert.equal(a.positions.TEST.qty,10);advance(a,data,'2024-01-03',costs);assert.equal(a.trades.at(-1).price,6);assert.equal(a.trades.at(-1).pnl,-4000);
});
test('drawdown circuit halts new buying',()=>{
 const data={assets:[asset()]},a=newAccount(100000,'medium','2024-01-01');a.peak=200000;advance(a,data,'2024-01-02',costs);assert.equal(a.halted,true);assert.throws(()=>queueOrder(a,data,'TEST','BUY',1));
});
test('total exposure cap is enforced across holdings',()=>{
 const assets=Array.from({length:5},(_,i)=>asset('T'+i,'US',[{date:'2024-01-01',open:10,close:10,fx:100},{date:'2024-01-02',open:10,close:10,fx:100}]));const data={assets},a=newAccount(100000,'medium','2024-01-01');
 for(const s of assets)queueOrder(a,data,s.symbol,'BUY',20);advance(a,data,'2024-01-02',costs);assert.equal(a.trades.at(-1).message,'株式比率の上限');assert.equal(a.cash,20000);
});
test('future price changes cannot change past signals or fills',()=>{
 const data=demoData(),dates=calendar(data),cut=dates[300],changed=structuredClone(data);
 changed.assets.forEach(s=>s.bars.filter(b=>b.date>cut).forEach(b=>{b.open*=50;b.close*=50;}));
 for(let i=0;i<data.assets.length;i++)assert.deepEqual(signal(data.assets[i],cut),signal(changed.assets[i],cut));
 const before=backtest(data).account.trades.filter(t=>t.date<=cut),after=backtest(changed).account.trades.filter(t=>t.date<=cut);assert.deepEqual(after,before);
});
test('sample replay is deterministic and account equity reconciles',()=>{
 const data=demoData(),a=backtest(data),b=backtest(data);assert.deepEqual(a,b);assert.ok(a.account.trades.length>0);assert.ok(a.account.cash>=0);assert.equal(a.account.history.at(-1).value,equity(a.account,data,a.account.date));assert.equal(a.benchmark.length,a.account.history.length-1);
});
test('maximum drawdown and CAGR include the initial capital',()=>{
 const m=metrics([{date:'2024-01-01',value:100},{date:'2024-06-01',value:120},{date:'2025-01-01',value:90}],100);assert.ok(Math.abs(m.maxDD+.25)<1e-10);assert.ok(Math.abs(m.return+.1)<1e-10);
});
const csv=()=>['date,symbol,market,open,close,fx',...demoData().assets[0].bars.slice(0,65).map(b=>`${b.date},7203,JP,100,110,1`)].join('\n');
test('CSV validates dates, duplicates, prices, FX, symbols and row counts',()=>{
 assert.equal(parseCSV('\uFEFF'+csv()).assets[0].bars.length,65);
 assert.throws(()=>parseCSV(csv().replace('2023-01-03','2023-02-30')));
 assert.throws(()=>parseCSV(csv().replace('2023-01-04','2023-01-03')));
 assert.throws(()=>parseCSV(csv().replace('JP,100,110,1','JP,0,110,1')));
 assert.throws(()=>parseCSV(csv().replace('JP,100,110,1','JP,100,110,150')));
 assert.throws(()=>parseCSV(csv().replace('7203','<script>')));
 assert.throws(()=>parseCSV('date,symbol,market,open,close,fx\n2024-01-01,A,US,1,1,1'));
});
test('Monte Carlo has reproducible probabilities, percentile order, deposits and correct goal',()=>{
 const m=monteCarlo(),n=monteCarlo();assert.deepEqual(m,n);assert.equal(m.goal,100000000);assert.ok(Math.abs(m.requiredCagr-.58489319246)<1e-10);assert.equal(m.bands.length,121);
 assert.ok(m.bands.every(b=>b.low<=b.median&&b.median<=b.high));assert.ok(m.probability>=0&&m.probability<=1);assert.ok(m.lossProbability>0);
 const deposited=monteCarlo({monthly:10000});assert.equal(deposited.contributed,2200000);assert.ok(deposited.bands.at(-1).median>m.bands.at(-1).median);assert.throws(()=>monteCarlo({initial:-1}));assert.throws(()=>monteCarlo({vol:NaN}));
});
