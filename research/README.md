# 研究用v2エンジン

24,000候補の高速探索、分担・再開、独立分析、固定候補の前向き観察は [新しい研究実行手順](../docs/research-runbook.md) を参照。高速探索はETFの連続比率近似で、以下のv2の詳細約定エンジンとは別です。

スマホの既存JavaScriptエンジンとは別の、Python 3.12 / 標準ライブラリのみの研究用CLIです。ブローカーに注文を送る機能はありません。

## 参考ETFの再実行

```sh
python scripts/fetch-v2-reference.py
python -m unittest discover -s tests -p 'test_v2*.py' -v
python scripts/run-v2.py --input research/history-v2-2026-10-24/reference-input.json --mode etf_reference
```

取得済みの原文から正規化だけを再実行する場合は `python scripts/fetch-v2-reference.py --cached`。価格と全台帳はgit管理対象外。派生集計だけが `reports/v2-reference-2026-10-24-summary.json` に出力されます。価格配信元の訂正により再取得した結果が変わる可能性があります。日付・条件は `v2-config.json` に固定されています。

## 500普通株の入力契約

現時点ではこの入力データが未取得です。JSONを受け付ける基盤を実装した段階で、各ベンダーのAPIアダプター・500銘柄規模の性能試験・全企業行動の実データ照合は未完了です。

```sh
python scripts/run-v2.py --input /path/to/verified-pit.json --mode point_in_time_equities --out research/history-pit/results.json --summary research/history-pit/summary.json
```

- ルート：`schema_version: 2`、`mode: point_in_time_equities`、`instruments`、`fx`、`coverage`、`membership_snapshots`。
- `instruments`：再利用しない`id`、`market`（JP/US）、`currency`（JPY/USD）、`kind: common_stock`、開始時点の整数`lot`、`bars`、`actions`。開始前の252営業日以上を含め、候補選定前に入手可能だった価格を供給する。
- `bars`：`date`、UTCオフセット付き`open_at`/`close_at`、当時の単位の`open`/`close`/`volume`。分割調整価格をそのまま入れて分割イベントと二重調整しない。
- `actions`：`at`と`type`。`split`は`ratio`、`lot`は変更後`lot`、`dividend`は1株当たり`amount`と`pay_at`、`delist`は確定した`cash_per_share`。株式交換・合併・端株清算はこの簡易スキーマで自動再現できないため、明示的な拡張・検証が必要。未確認清算値を0や最終株価で埋めない。
- `fx`：JPY/USDの`mid`、利用可能時刻`at`。UTCオフセット必須。基準値の取得時刻と売買時刻を混同しない。
- `membership_snapshots`：国ごと・月初ごとに`market`、`effective_at`、`known_at`、`complete`、`provenance`、`members`。`members`には`id`、当時の`sector`、`size_bucket`（large/mid）、整数`lot`。大型・中型の区分規則は出典とともに固定し、時点整合した規模情報から計算する。現在の区分を遡及適用しない。
- `coverage`：`from_date`/`through`、`includes_delisted`、`complete_market_history`、`permanent_ids`、`historical_lots`、`corporate_actions_complete`、出典根拠の`evidence`。配当込みで実行する場合は設定`dividends: true`と`dividend_mode: payable_events`も必要。

完全性のフラグは入力条件を宣言するもので、データの真正性を自動的に証明しません。現存500銘柄にフラグを付けても適切なPITデータにはなりません。利用権限・全市場カバレッジ・公表時刻・上場廃止清算を独立に照合してから利用してください。

月次選定は各国250候補（大型125・中型125）を目指します。要件を満たさない月は不足数を台帳に記録し、数合わせのために流動性・履歴要件を緩めません。保持する銘柄数は候補数と異なり、シグナル・100万円の資金・単元・リスク枠で決まります。

## 既知の研究上の限界

株式の国別業種上限はありますが、日米をまたぐ同業種の集約・ETFの重複保有・相関制約は未実装です。売買単位変更後の旧単位注文や端株、USD配当未収金の毎日の為替再評価、現実の約定不能・取引停止には追加検証が必要です。借入・空売り・税金・売買板・場中損切りも扱いません。未来改変テストはコードの因果性を検査しますが、データ提供元の過去訂正や母集団の完全性を検証するものではありません。
