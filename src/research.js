// Research output is separate from the browser's simulated trading account.
const escape = value => String(value ?? '—').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const number = value => Number.isFinite(value) ? new Intl.NumberFormat('ja-JP').format(value) : '—';
const money = value => Number.isFinite(value) ? `${number(Math.round(value))}円` : '—';
const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—';
const labels = {trend:'トレンド期間',momentum:'騰落率の期間',rebalance:'配分見直し間隔',topk:'選ぶ本数',gross:'投資比率',weighting:'配分方式',voltarget:'目標変動率',defensive_cash:'SPY下落時に現金退避',entry_buffer:'参入の余裕幅'};
Object.assign(labels,{family:'アルゴリズム系統',lookback:'参照期間',entry_window:'突破を確認する期間',exit_window:'退出を確認する期間',rsi_period:'RSI計算期間',entry_rsi:'RSI買い基準',exit_rsi:'RSI売り基準',max_hold:'最大保有日数',trend_filter:'長期トレンド条件',vol_window:'変動率の計算期間',target_vol:'目標変動率',drawdown_window:'下落を測る期間',drawdown_limit:'下落の基準',core_weight:'SPYの基礎配分',fast:'短期の参照日数',slow:'長期の参照日数',fast_weight:'短期指標の重み',satellite_weight:'追加戦略の配分'});
Object.assign(labels,{rsi_window:'RSI計算期間',short_window:'短期の参照日数',long_window:'長期の参照日数',relative_window:'相対順位の計算期間',short_weight:'短期指標の重み',absolute_threshold:'上昇率の下限',weakness_threshold:'短期騰落率の上限',relative_threshold:'SPYに対する騰落率差の下限',core:'SPYの基礎配分',cap:'投資比率の上限',risk_rule:'下落時の配分調節',blend:'順張り・逆張り・低変動の配分'});
const configValue = (key,value) => {
  if (typeof value === 'boolean') return value ? 'あり' : 'なし';
  if (['gross','entry_buffer','core_weight','fast_weight','satellite_weight','drawdown_limit','core','cap','short_weight','absolute_threshold','weakness_threshold','relative_threshold'].includes(key)) return percent(value);
  if (['voltarget','target_vol'].includes(key)) return value ? percent(value) : '指定なし';
  if (['trend','momentum','rebalance'].includes(key)) return `${value}日`;
  if (key === 'topk') return `${value}本`;
  if (key === 'weighting') return value === 'inverse_vol' ? '変動率の逆数で配分' : '均等配分';
  if (key === 'risk_rule') return ({none:'なし',half_10:'10%下落で半減',half_15:'15%下落で半減',cash_10:'10%下落で現金',cash_20:'20%下落で現金'})[value] || value;
  if (key === 'blend') return value.map(percent).join(' / ');
  return value;
};

export function researchChoice(result, familyId) {
  if (!result?.families) return result?.selected;
  return (result.families.find(f=>f.id===familyId) || result.families.find(f=>f.selected.id===result.overall_selected.id) || result.families[0]).selected;
}

export function researchPage(result, error, chart, familyId) {
  const heading = `<div class="page-head"><div><div class="eyebrow">STRATEGY RESEARCH</div><h1>アルゴリズム研究室</h1><p class="subtitle">多数の仮説を比較して、採用できる根拠を確かめる。</p></div></div>`;
  if (!result) return heading + `<section class="panel" role="status"><h2>${error ? '研究結果を読み込めませんでした' : '研究結果を読み込んでいます'}</h2><p>${error ? '接続を確認し、もう一度読み込んでください。' : '学習・選別・監査の結果を確認しています。'}</p><button class="button" id="research-refresh">再読み込み</button></section>`;
  let familyPanel = '';
  if (result.families) {
    const chosen = researchChoice(result,familyId);
    const active = result.families.find(f=>f.selected.id===chosen.id);
    const windowValue = (family,years) => money(family.windows.find(w=>w.years===years)?.selected.final);
    familyPanel = `<section class="panel"><div class="panel-head"><h2>売買の考え方が異なる ${result.families.length} 系統を比較</h2><span class="tag">パラメータ違いと区別</span></div><p class="method">各系統の中で学習・選別期間から選んだ候補を比較します。10年・5年・1年の結果を使って順位を付け直してはいません。</p><div class="table-wrap"><table><thead><tr><th>アルゴリズム</th><th>試行数</th><th>両期間でSPY超過</th><th>10年・最終資産</th><th>5年・最終資産</th><th>1年・最終資産</th></tr></thead><tbody>${result.families.map(f=>`<tr><td>${escape(f.name)}</td><td>${number(f.trials)}</td><td>${number(f.positive_both_count)}</td><td>${windowValue(f,10)}</td><td>${windowValue(f,5)}</td><td>${windowValue(f,1)}</td></tr>`).join('')}</tbody></table></div><p class="small-note">初期100万円。いずれも固定5ETF・同じ費用・同じ円換算の近似モデル。系統の数と、系統内で試した設定の数を分けて表示しています。</p><label>詳しく見るアルゴリズム<select id="research-family">${result.families.map(f=>`<option value="${escape(f.id)}" ${f.id===active.id?'selected':''}>${escape(f.name)}</option>`).join('')}</select></label><p class="method">${escape(active.definition)}</p></section>`;
    result = {...result, selected:chosen, windows:active.windows, top20:active.top5,
      curves:active.curves || (chosen.id===result.overall_selected.id?result.curves:[]),
      provenance:{...result.provenance,executed_trials:result.provenance.total_trials},
      ranking:{...result.ranking,positive_both_count:result.families.reduce((sum,f)=>sum+f.positive_both_count,0)}};
  }
  const selected = result.selected, periods = [...(result.windows || [])].sort((a,b)=>b.years-a.years), top = result.top20 || [];
  const count = result.provenance?.executed_trials;
  const yearsWon = periods.filter(p => p.selected.final > p.spy.final).length;
  const card = (label, value, text) => `<div class="stat"><div class="stat-label">${label}</div><strong>${value}</strong><small>${text}</small></div>`;
  const config = Object.entries(selected.config).filter(([key])=>key!=='family').map(([key, value]) => `<div class="rule"><span>${escape(labels[key] || key)}</span><b>${escape(configValue(key,value))}</b></div>`).join('');
  const curves = result.curves || [];
  const compare = periods.map(p => `<tr><td>${escape(p.years)}年</td><td>${money(p.selected.final)}</td><td>${money(p.spy.final)}</td><td class="${p.selected.final > p.spy.final ? 'positive' : 'negative'}">${((p.selected.cagr-p.spy.cagr)*100).toFixed(2)}pt</td><td>${percent(p.selected.max_drawdown)}</td></tr>`).join('');
  const partitions = ['train','validation','audit'].map((key, i) => `<div class="research-stage"><span>${i + 1}</span><div><b>${['学習','選別','監査（既知の期間）'][i]}</b><p>${escape(result.partitions[key]?.start)} → ${escape(result.partitions[key]?.end)}</p><small>${i < 2 ? '候補の順位付けに使用' : '順位付けから隔離。完全な未使用データではありません'}</small></div></div>`).join('');
  return heading + `<div class="notice"><b>研究用・運用採用は保留</b><span>5本の指数ETFによる近似試験です。日米500銘柄版の検証ではありません。将来の期待収益を保証する数値ではありません。</span></div>
    <div class="stats">${card('比較した候補',number(count),'設定を変えて実際に計算')}${card('両期間でS&P500超過',number(result.ranking?.positive_both_count),'学習と選別の両方を満たす候補')}${card('S&P500を超えた期間',`${yearsWon} / ${periods.length}`,'選択候補の10年・5年・1年診断')}${card('実際の発注','なし','研究結果は既存口座に自動反映しません')}</div>
    ${familyPanel}<section class="panel"><div class="panel-head"><h2>100万円からの比較</h2><span class="tag">選択中の系統 / JPY</span></div><div class="table-wrap"><table><thead><tr><th>期間</th><th>研究候補</th><th>S&P500 ETF</th><th>年率の差</th><th>候補の最大下落</th></tr></thead><tbody>${compare}</tbody></table></div><p class="small-note">取引費用を含む連続比率モデル。分配金・税金は除外。整数株数や実際の寄り付きでの約定を再現した金額ではありません。3期間は重複し、順位を決めるために使用した期間も含みます。</p></section>
    <section class="panel"><div class="panel-head"><h2>資産推移の比較</h2><span class="tag">過去の参考値</span></div><div class="legend"><span><i></i>研究候補</span><span><i class="light"></i>S&P500 ETF</span></div>${curves.length ? chart([{label:'研究候補',values:curves.map(p=>p.selected),color:'#47744f'},{label:'S&P500 ETF',values:curves.map(p=>p.spy),color:'#b68a4e'}],{labels:curves.map(p=>p.date)}) : '<p>推移データはありません。</p>'}</section>
    <div class="grid-main"><section class="panel"><h2>選択されたルール / ${escape(selected.id)}</h2>${config}<p class="small-note">日数はモデル内の営業日。最高成績の設定を将来の最適解とみなすことはできません。</p><div class="actions"><button class="button" id="research-export">研究結果を保存 ↓</button><button class="button" id="research-config">候補の設定を保存 ↓</button></div></section><section class="panel"><h2>検証期間を分ける</h2>${partitions}<p class="small-note">何万回も試すと偶然の好成績が見つかります。監査結果を見て変更した設定は、再び開発中の仮説として扱います。</p></section></div>
    <section class="panel"><h2>固定した候補を、新しいデータで観察</h2><p class="method">9系統のいずれも、候補を固定して新しいデータで追跡するコマンドに対応しました。別系統の比較結果を理由に、観察中のモデルを自動で置き換えません。</p><p class="small-note">研究専用の連続比率モデルです。観測は計算環境から実行します。採用・実発注・市場データの自動取得は行っていません。</p></section>
    <section class="panel"><div class="panel-head"><h2>上位候補を比較</h2><span class="tag">順位に監査結果は使わない</span></div><div class="table-wrap"><table><thead><tr><th>候補ID</th><th>学習・年率</th><th>選別・年率</th><th>監査・年率</th><th>選別・最大下落</th></tr></thead><tbody>${top.map(c=>`<tr><td>${escape(c.id)}</td><td>${percent(c.train?.cagr)}</td><td>${percent(c.validation?.cagr)}</td><td>${percent(c.audit?.cagr)}</td><td>${percent(c.validation?.max_drawdown)}</td></tr>`).join('')}</tbody></table></div></section>
    <section class="panel method"><h2>運用に進む前に確認すること</h2><p>当時の銘柄履歴を揃えた500銘柄版の検証、整数株数と売買時刻を反映した再計算、ルール固定後の新しいデータでの検証が必要です。研究上位という理由だけでは採用しません。</p><p>大量試行は計算環境のPythonで行い、この画面には集計済みの結果を表示します。スマホを閉じても完了した結果は失われません。画面上から研究ジョブを起動する機能はありません。</p><a href="https://github.com/syoudai0514/kabu/blob/main/docs/algorithm-families-protocol.md" target="_blank" rel="noreferrer">研究方針・実行手順 ↗</a></section>`;
}
