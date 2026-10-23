// All prices are synthetic unless the user explicitly imports a CSV.
export const PRESETS = {
  medium: {label:'中リスク', cap:0.2, exposure:0.8, stop:0.1, risk:0.01, circuit:0.25, mu:0.08, vol:0.2},
  high: {label:'高リスク', cap:0.3, exposure:0.9, stop:0.15, risk:0.02, circuit:0.4, mu:0.12, vol:0.35},
};
export const STOCKS = [
  {symbol:'7203',name:'トヨタ自動車',market:'JP',currency:'JPY',lot:100,base:1900},
  {symbol:'6758',name:'ソニーグループ',market:'JP',currency:'JPY',lot:100,base:2400},
  {symbol:'8306',name:'三菱UFJ FG',market:'JP',currency:'JPY',lot:100,base:850},
  {symbol:'AAPL',name:'Apple',market:'US',currency:'USD',lot:1,base:140},
  {symbol:'MSFT',name:'Microsoft',market:'US',currency:'USD',lot:1,base:240},
  {symbol:'NVDA',name:'NVIDIA',market:'US',currency:'USD',lot:1,base:35},
];
export function rng(seed=42){ let a=seed>>>0; return ()=>{a+=0x6D2B79F5;let t=a;t=Math.imul(t^t>>>15,t|1);t^=t+Math.imul(t^t>>>7,t|61);return ((t^t>>>14)>>>0)/4294967296;}; }
export function normal(r){return Math.sqrt(-2*Math.log(Math.max(r(),1e-12)))*Math.cos(2*Math.PI*r());}
export function demoData(){
  const r=rng(812); const prices=STOCKS.map(s=>s.base); let fx=132;
  const data=STOCKS.map(s=>({...s,bars:[]})); let date=new Date('2023-01-03T00:00:00Z');
  for(let i=0;i<720;i++){
    while([0,6].includes(date.getUTCDay())) date.setUTCDate(date.getUTCDate()+1);
    const day=date.toISOString().slice(0,10), common=normal(r); fx*=Math.exp(0.00005+0.003*normal(r));
    data.forEach((s,j)=>{const v=j===5?0.033:0.017;const open=prices[j]*Math.exp(0.004*normal(r));const close=open*Math.exp(0.00035+v*(0.55*common+0.835*normal(r)));prices[j]=close;s.bars.push({date:day,open,close,fx:s.market==='US'?fx:1});});
    date.setUTCDate(date.getUTCDate()+1);
  }
  return {source:'demo',label:'合成データ（実際の相場ではありません）',assets:data};
}
export function validNumber(value,min,max,name){const n=Number(value);if(value===''||!Number.isFinite(n)||n<min||n>max)throw new Error(`${name}は ${min}〜${max} で入力してください。`);return n;}
export function parseCSV(text){
  const lines=text.replace(/^\uFEFF/,'').trim().split(/\r?\n/);
  if(lines.length<62||lines.length>30001)throw new Error('CSVはヘッダーと61〜30,000行の価格が必要です。');
  const cols=lines.shift().trim().split(',').map(s=>s.trim());
  const required=['date','symbol','market','open','close','fx'];
  if(cols.join(',')!==required.join(','))throw new Error('ヘッダーは date,symbol,market,open,close,fx の順にしてください。');
  const groups=new Map();
  lines.forEach((line,i)=>{
    const parts=line.split(',').map(x=>x.trim());if(parts.length!==6)throw new Error(`${i+2}行目: 列数が一致しません。`);
    const [date,symbol,market,o,c,f]=parts;
    if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(Date.parse(date))||new Date(date).toISOString().slice(0,10)!==date)throw new Error(`${i+2}行目: 日付が不正です。`);
    if(!/^[A-Z0-9.\-]{1,16}$/.test(symbol)||!['JP','US'].includes(market))throw new Error(`${i+2}行目: 銘柄または市場が不正です。`);
    const open=validNumber(o,0.0001,1e9,'始値'),close=validNumber(c,0.0001,1e9,'終値'),fx=validNumber(f,0.0001,1e5,'為替');
    if(market==='JP'&&fx!==1)throw new Error('日本株の fx は1にしてください。');
    if(!groups.has(symbol))groups.set(symbol,{symbol,name:STOCKS.find(s=>s.symbol===symbol)?.name||symbol,market,currency:market==='JP'?'JPY':'USD',lot:market==='JP'?100:1,bars:[]});
    const asset=groups.get(symbol);if(asset.market!==market)throw new Error('同じ銘柄の市場が混在しています。');
    if(asset.bars.length&&asset.bars.at(-1).date>=date)throw new Error('各銘柄の日付は重複なしの昇順にしてください。');
    asset.bars.push({date,open,close,fx});
  });
  const assets=[...groups.values()];if(assets.length>30)throw new Error('銘柄は30個以内にしてください。');
  // Keep each market's own calendar; do not fabricate fills on closed-market dates.
  if(assets.some(s=>s.bars.length<61))throw new Error('各銘柄に61本以上の価格が必要です。');
  return {source:'csv',label:'インポートCSV（内容は利用者提供・未検証）',assets};
}
export function calendar(data){return [...new Set(data.assets.flatMap(s=>s.bars.map(b=>b.date)))].sort();}
export function barAt(asset,date){let lo=0,hi=asset.bars.length-1,found=-1;while(lo<=hi){const mid=(lo+hi)>>1;if(asset.bars[mid].date<=date){found=mid;lo=mid+1;}else hi=mid-1;}return found<0?null:{...asset.bars[found],index:found};}
export function signal(asset,date){const b=barAt(asset,date);if(!b||b.index<59)return {action:'WAIT',momentum:0};const bars=asset.bars.slice(b.index-59,b.index+1);const slow=bars.reduce((a,b)=>a+b.close,0)/60,fast=bars.slice(-20).reduce((a,b)=>a+b.close,0)/20;const momentum=b.close/bars.at(-21).close-1;return {action:b.close>slow&&fast>slow&&momentum>0?'BUY':'SELL',momentum,fast,slow};}
export function newAccount(initial=1000000,risk='high',date=null){initial=validNumber(initial,10000,1e10,'初期資金');if(!PRESETS[risk])throw new Error('リスク設定が不正です。');return {initial,cash:initial,risk,positions:{},orders:[],trades:[],history:[],date,peak:initial,halted:false};}
export function equity(account,data,date,field='close'){return account.cash+Object.entries(account.positions).reduce((v,[symbol,p])=>{const asset=data.assets.find(s=>s.symbol===symbol),b=asset&&barAt(asset,date);return v+(b?p.qty*b[field]*b.fx:0);},0);}
export function queueOrder(account,data,symbol,side,qty){
  const asset=data.assets.find(s=>s.symbol===symbol);if(!asset||!['BUY','SELL'].includes(side)||!Number.isSafeInteger(qty)||qty<=0||qty%asset.lot)throw new Error('数量は日本株100株、米国株1株単位の正の整数で指定してください。');
  if(account.orders.some(o=>o.symbol===symbol))throw new Error('この銘柄には未約定注文があります。');
  if(side==='BUY'&&account.halted)throw new Error('損失上限に達したため新規買付は停止中です。');
  if(side==='SELL'&&qty>(account.positions[symbol]?.qty||0))throw new Error('保有数量を超えて売却できません。');
  account.orders.push({id:`${account.date}-${symbol}-${account.trades.length}`,symbol,side,qty,placed:account.date,reason:'手動'});
}
const defaultCosts={feeBps:10,slipBps:5,fxBps:10};
export function advance(account,data,date,costs=defaultCosts){
  if(date<=account.date)throw new Error('次の日付を指定してください。');
  const cfg={...defaultCosts,...costs};Object.values(cfg).forEach(x=>validNumber(x,0,1000,'コスト(bps)'));
  const risk=PRESETS[account.risk],waiting=[];
  // Orders created with a prior close execute only on the next available OPEN.
  for(const order of account.orders){
    const asset=data.assets.find(s=>s.symbol===order.symbol);const b=asset.bars.find(b=>b.date===date);
    if(!b){waiting.push(order);continue;}
    let reason='';const slip=cfg.slipBps/10000;const price=b.open*(order.side==='BUY'?1+slip:1-slip);const gross=price*b.fx*order.qty;const fee=gross*(cfg.feeBps+(asset.market==='US'?cfg.fxBps:0))/10000;const total=gross+fee;
    const pos=account.positions[order.symbol];const totalEquity=equity(account,data,date,'open');
    if(order.side==='BUY'){
      const exposure=totalEquity-account.cash+gross;
      if(account.halted)reason='新規買付停止';else if(total>account.cash+1e-7)reason='買付余力不足';
      else if((pos?.qty||0)*b.open*b.fx+gross>totalEquity*risk.cap+1e-7)reason='1銘柄の比率上限';
      else if(exposure>totalEquity*risk.exposure+1e-7)reason='株式比率の上限';
      if(!reason){account.cash-=total;account.positions[order.symbol]={qty:(pos?.qty||0)+order.qty,cost:(pos?.cost||0)+total};}
    }else{
      if(!pos||pos.qty<order.qty)reason='保有数量不足';
      else{const basis=pos.cost*order.qty/pos.qty;account.cash+=gross-fee;pos.qty-=order.qty;pos.cost-=basis;order.pnl=gross-fee-basis;if(!pos.qty)delete account.positions[order.symbol];}
    }
    account.trades.push({...order,date,price:reason?null:price,fx:b.fx,fee:reason?0:fee,status:reason?'rejected':'filled',message:reason,pnl:order.pnl??null});
  }
  account.orders=waiting;account.date=date;
  const value=equity(account,data,date);account.peak=Math.max(account.peak,value);
  if(value/account.peak-1<=-risk.circuit)account.halted=true;
  // Stop levels are close-based; gaps can exceed the configured loss threshold.
  for(const [symbol,pos] of Object.entries(account.positions)){
    const asset=data.assets.find(s=>s.symbol===symbol),b=barAt(asset,date);
    if(!b||(!account.halted&&b.date!==date))continue;
    if(account.halted||pos.qty*b.close*b.fx<=pos.cost*(1-risk.stop)){
      account.orders=account.orders.filter(o=>o.symbol!==symbol);
      account.orders.push({id:`stop-${date}-${symbol}`,symbol,side:'SELL',qty:pos.qty,placed:date,reason:account.halted?'損失上限':'損切り'});
    }
  }
  account.history.push({date,value,cash:account.cash});return account;
}
export function strategyOrders(account,data,date){
  const risk=PRESETS[account.risk];
  const ranked=data.assets.map(s=>({asset:s,...signal(s,date)})).sort((a,b)=>b.momentum-a.momentum);
  for(const {asset,action} of ranked){
    const b=barAt(asset,date);if(!b||b.date!==date||b.index<59||account.orders.some(o=>o.symbol===asset.symbol))continue;
    const pos=account.positions[asset.symbol];let side,qty;
    if(pos&&action==='SELL'){side='SELL';qty=pos.qty;}
    else if(!pos&&action==='BUY'&&!account.halted){side='BUY';const budget=Math.min(equity(account,data,date)*Math.min(risk.cap,risk.risk/risk.stop)*0.97,account.cash*0.97);qty=Math.floor(budget/(b.close*b.fx*asset.lot))*asset.lot;}
    if(qty>0){queueOrder(account,data,asset.symbol,side,qty);account.orders.at(-1).reason='MA20/60 + モメンタム';}
  }
}
// One automatic session: inspect the previous close, then execute at the next open.
export function automaticStep(account, data, costs = defaultCosts) {
  const dates = calendar(data);
  const index = dates.indexOf(account.date);
  if (index < 0) throw new Error('口座の日付が価格データにありません。');
  if (index === dates.length - 1) return null;
  Object.values({...defaultCosts,...costs}).forEach(v => validNumber(v,0,1000,'コスト(bps)'));
  if (!account.history.length) account.history.push({date:account.date,value:equity(account,data,account.date),cash:account.cash});
  const before = account.trades.length;
  strategyOrders(account, data, account.date);
  advance(account, data, dates[index + 1], costs);
  const trades = account.trades.slice(before);
  return {date:account.date,filled:trades.filter(t=>t.status==='filled').length,rejected:trades.filter(t=>t.status==='rejected').length};
}

export function metrics(history,initial){
  if(!history.length)return {return:0,cagr:0,maxDD:0};let peak=initial,maxDD=0;
  for(const h of history){peak=Math.max(peak,h.value);maxDD=Math.min(maxDD,h.value/peak-1);}
  const years=Math.max(1,(Date.parse(history.at(-1).date)-Date.parse(history[0].date))/86400000)/365.25;
  const end=history.at(-1).value;return {return:end/initial-1,cagr:Math.pow(end/initial,1/years)-1,maxDD};
}
export function backtest(data,{initial=1000000,risk='high',market='ALL',feeBps=10,slipBps=5,fxBps=10}={}){
  const subset={...data,assets:data.assets.filter(s=>market==='ALL'||s.market===market)};
  if(!subset.assets.length)throw new Error('対象市場のデータがありません。');
  const dates=calendar(subset);const start=dates.findIndex(d=>subset.assets.some(s=>(barAt(s,d)?.index??-1)>=59));
  if(start<0||start===dates.length-1)throw new Error('検証に必要な日数がありません。');
  const account=newAccount(initial,risk,dates[start]);account.history.push({date:dates[start],value:initial,cash:initial});
  const benchmarkPositions={};let benchmarkCash=initial;const allocation=initial/subset.assets.length;const benchmark=[];
  for(let i=start+1;i<dates.length;i++){
    strategyOrders(account,subset,dates[i-1]);advance(account,subset,dates[i],{feeBps,slipBps,fxBps});
    // Equal-weight fractional benchmark; no transaction costs, explicit in UI.
    subset.assets.forEach(s=>{const b=s.bars.find(b=>b.date===dates[i]);if(b&&benchmarkPositions[s.symbol]===undefined){benchmarkPositions[s.symbol]=allocation/(b.open*b.fx);benchmarkCash-=allocation;}});
    benchmark.push({date:dates[i],value:benchmarkCash+subset.assets.reduce((v,s)=>{const b=barAt(s,dates[i]);return v+(b?(benchmarkPositions[s.symbol]||0)*b.close*b.fx:0);},0)});
  }
  return {account,benchmark,stats:metrics(account.history,initial)};
}
export function monteCarlo({initial=1000000,monthly=0,mu=0.12,vol=0.35,seed=42,paths=2000}={}){
  validNumber(initial,10000,1e10,'初期資金');validNumber(monthly,0,1e8,'積立');validNumber(mu,-0.5,1,'期待収益率');validNumber(vol,0.01,1.5,'変動率');
  validNumber(paths,100,10000,'経路数');if(!Number.isInteger(paths))throw new Error('経路数は整数にしてください。');
  const r=rng(seed),values=Array(paths).fill(initial);const bands=[{month:0,low:initial,median:initial,high:initial}];
  const quantile=(a,p)=>a[Math.floor((a.length-1)*p)];const worst=Array(paths).fill(0),peaks=Array(paths).fill(initial);
  for(let month=1;month<=120;month++){
    for(let j=0;j<paths;j++){values[j]=values[j]*Math.exp((mu-vol*vol/2)/12+vol/Math.sqrt(12)*normal(r))+monthly;peaks[j]=Math.max(peaks[j],values[j]);worst[j]=Math.min(worst[j],values[j]/peaks[j]-1);}
    const sorted=[...values].sort((a,b)=>a-b);bands.push({month,low:quantile(sorted,.1),median:quantile(sorted,.5),high:quantile(sorted,.9)});
  }
  const contributed=initial+120*monthly,goal=initial*100;
  return {bands,goal,contributed,probability:values.filter(v=>v>=goal).length/paths,lossProbability:values.filter(v=>v<contributed).length/paths,crashProbability:worst.filter(v=>v<=-.5).length/paths,paths,requiredCagr:100**.1-1};
}
export function tradesCSV(trades){return ['date,symbol,side,quantity,price,fx,fee_jpy,status,pnl_jpy,reason',...trades.map(t=>[t.date,t.symbol,t.side,t.qty,t.price,t.fx,t.fee,t.status,t.pnl??'',t.message||t.reason].join(','))].join('\n');}
