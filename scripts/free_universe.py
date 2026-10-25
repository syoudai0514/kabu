"""Reproducible CURRENT-universe screening. Never declares historical PIT coverage."""
import datetime as dt
import hashlib
import json
import pathlib
import re
import sqlite3
import statistics
from collections import Counter


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def build_pool(database, policy):
    db = sqlite3.connect(f'file:{pathlib.Path(database).resolve()}?mode=ro', uri=True)
    members, snapshots, excluded = {}, [], Counter()
    try:
        for source in ('jpx_listings', 'nasdaq_listed', 'other_listed'):
            batch = db.execute("SELECT id,observed_at,raw_sha256 FROM batches WHERE source=? AND status='ok' ORDER BY id DESC LIMIT 1", (source,)).fetchone()
            if not batch:
                raise ValueError('Missing successful listing batch: ' + source)
            snapshots.append(dict(source=source, batch_id=batch[0], observed_at=batch[1], raw_sha256=batch[2]))
            query = 'SELECT o.payload FROM batch_items b JOIN observations o USING(source,item_key,content_sha256) WHERE b.batch_id=?'
            for row in db.execute(query, (batch[0],)):
                item = json.loads(row[0])
                if not item['common_stock_candidate']:
                    excluded['not_common_stock_candidate'] += 1
                    continue
                symbol, market = item['symbol'], item['market']
                if market == 'JP' and re.fullmatch('[0-9A-Z]{4}', symbol):
                    ticker = symbol + '.T'
                elif market == 'US' and re.fullmatch(r'[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z])?', symbol):
                    ticker = symbol.replace('.', '-')
                else:
                    excluded['unsupported_symbol_mapping'] += 1
                    continue
                key = market + ':' + ticker
                if key in members:
                    raise ValueError('Ambiguous duplicate security mapping: ' + key)
                members[key] = dict(key=key, symbol=symbol, ticker=ticker, market=market,
                                    sector=item.get('sector'), size_label=item.get('size_label'),
                                    source=source, name=item['name'], instrument_type_verified=False)
    finally:
        db.close()
    pools, available = {}, {}
    for market in ('JP', 'US'):
        rows = [x for x in members.values() if x['market'] == market]
        available[market] = len(rows)
        pools[market] = sorted(rows, key=lambda x: (hashlib.sha256((policy['seed'] + ':' + market + ':' + x['symbol']).encode()).hexdigest(), x['symbol']))[:policy['pool_per_market']]
    # Interleaving makes small diagnostic runs cover both countries.
    pool = [pools[m][i] for i in range(max(map(len, pools.values()))) for m in ('JP', 'US') if i < len(pools[m])]
    return dict(policy=policy, policy_sha256=digest(policy), snapshots=snapshots,
                available_candidates=available, excluded=dict(excluded), pool=pool,
                pool_sha256=digest(pool), historical_pit_complete=False)


def evaluate(item, prices, policy):
    bars = prices['bars']
    reasons = []
    if len(bars) < policy['minimum_bars']:
        reasons.append('insufficient_history')
    if not bars or bars[-1]['date'] != policy['required_last_session']:
        reasons.append('stale_or_missing_final_session')
    if prices.get('warnings'):
        reasons.append('price_discontinuity_review')
    if prices.get('exchange') in ('PNK', 'OQX', 'OQB'):
        reasons.append('now_otc_exchange')
    recent = bars[-policy['liquidity_window']:]
    proxy = statistics.median(b['close'] * b['volume'] for b in recent) if recent else 0
    if len(recent) < policy['liquidity_window']:
        reasons.append('insufficient_liquidity_history')
    if recent and sum(b['volume'] > 0 for b in recent) / len(recent) < policy['minimum_positive_volume_fraction']:
        reasons.append('too_many_zero_volume_days')
    if proxy < policy['minimum_median_turnover_proxy'][item['market']]:
        reasons.append('low_turnover_proxy')
    if any((dt.date.fromisoformat(b['date']) - dt.date.fromisoformat(a['date'])).days > policy['maximum_recent_gap_calendar_days'] for a, b in zip(recent, recent[1:])):
        reasons.append('recent_session_gap')
    if recent and policy['exclude_splits_in_liquidity_window'] and any(recent[0]['date'] <= s['date'] <= recent[-1]['date'] for s in prices['splits']):
        reasons.append('recent_split_turnover_review')
    if item['market'] == 'JP' and item.get('sector') in (None, '', '-'):
        reasons.append('missing_jp_sector')
    return dict(key=item['key'], market=item['market'], status='ok', eligible=not reasons,
                reasons=reasons, bars=len(bars), last_session=bars[-1]['date'] if bars else None,
                median_turnover_proxy=proxy, omitted_bars=len(prices.get('failures', [])),
                raw_sha256=prices['provenance']['sha256'], retrieved_at=prices['provenance']['retrieved_at'])


def summarize(manifest, results):
    policy = manifest['policy']
    output = dict(mode='current_sampled_universe_provisional', historical_pit_complete=False,
                  adoption_approved=False, broker_orders_enabled=False, policy_sha256=manifest['policy_sha256'],
                  pool_sha256=manifest['pool_sha256'], available_candidates=manifest['available_candidates'],
                  snapshots=manifest['snapshots'], markets={},
                  limitations=['current_survivor_bias', 'sample_not_whole_market', 'revised_price_turnover_proxy',
                               'US_sector_and_size_unverified', 'large_mid_quotas_not_applied',
                               'permanent_ids_lots_actions_and_Rakuten_tradability_unverified'])
    for market in ('JP', 'US'):
        members = [x for x in manifest['pool'] if x['market'] == market]
        rejected = Counter()
        eligible = []
        for item in members:
            result = results.get(item['key'])
            if result is None:
                rejected['not_fetched'] += 1
            elif result['status'] != 'ok':
                rejected['fetch_or_validation_failed'] += 1
            elif not result['eligible']:
                rejected.update(result['reasons'])
            else:
                eligible.append((item, result))
        eligible.sort(key=lambda pair: (-pair[1]['median_turnover_proxy'], pair[0]['symbol']))
        selections = {}
        for count in policy['targets']:
            selected, sectors = [], Counter()
            for item, result in eligible:
                if market == 'JP' and sectors[item['sector']] >= policy['jp_sector_cap']:
                    continue
                selected.append(dict(item, median_turnover_proxy=result['median_turnover_proxy']))
                sectors[item.get('sector') or 'unknown'] += 1
                if len(selected) == count:
                    break
            selections[str(count)] = dict(count=len(selected), shortfall=max(0, count-len(selected)),
                                           sectors=dict(sectors), members=selected)
        output['markets'][market] = dict(pool=len(members), processed=sum(x['key'] in results for x in members),
                                        eligible=len(eligible), rejection_reason_counts=dict(rejected), selections=selections)
    output['collection_complete'] = len(results) == len(manifest['pool'])
    return output
