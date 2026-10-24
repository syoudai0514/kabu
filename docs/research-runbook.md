# Sol / Sonnet等に渡す研究実行手順

主担当が仮説・採点式・期間を固定し、試行担当がCLIを実行し、主担当が全結果を分析する構成です。1試行ごとのLLM呼出しは不要。モデルが何であっても同じコード・データ・シードなら同じ結果になります。Sonnetや外部APIの接続は実装していません。

## 最新：異なる9系統の比較

初回の同系統パラメータ探索に加え、順張り・終値レンジ突破・RSI逆張り・短期下落順位・低変動率・二期間モメンタム・SPY基礎配分・SPY変動率管理・三戦略合成を別々に実装しました。各2,500設定、合計22,500通り。

```sh
OPENBLAS_NUM_THREADS=1 python scripts/run-families.py --output research/history-families-new --summary research/history-families-new/summary.json --batch-size 1024
```

`--shards`、`--shard-index`、`--resume`、`--aggregate`の分担と再開は下記と同様。設定は`research/family-space.json`、定義・採点・制約を結果を見る前に固定します。完全な一覧と出典ハッシュを保存し、入力・ソース変更後は新しい実験ディレクトリを使います。

初回結果の独立照合は `python scripts/analyze-families.py`。画面の集計ファイルは `src/research-families-results.json` です。以前の `research-results.json` は第1実験の保存版として残します。[9系統の分析結果](../reports/algorithm-families-2026-10-24.md)も参照してください。

9系統に対応する前向き観察：

```sh
python scripts/family-paper.py freeze --input research/history-v2-2026-10-24/reference-input.json
python scripts/family-paper.py observe --input /path/to/updated-reference-input.json
```

今回08-02152を `research/history-family-forward/` に固定済みで、観測0日です。研究用で採用ではありません。別モデルは`--model`と`--state`で別パスを指定。固定日より後の最初の完了終値を代理の初期配分とし、次の評価日から損益を記録します。過去の修正・ソースや設定の変更・台帳の書き換えを拒否します。終端清算費用は観察中の台帳に含めず、履歴比較の各期間末清算とは区別します。

次の節以降は、第1実験と共通する運用上の説明および初回の再現手順です。

## セットアップと一括探索

Python 3.12、NumPy 2.3.5。設定は [search-space.json](../research/search-space.json)、研究方針は [固定プロトコル](research-protocol.md)。入力ETF履歴の取得方法は [研究用エンジン](../research/README.md) を参照。

```sh
python -m pip install -r research/requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
OPENBLAS_NUM_THREADS=1 python scripts/run-research.py --output research/history-search-new --summary research/history-search-new/summary.json --batch-size 1024
```

**現在の高速探索はETF専用です。日米500普通株の入力を受け付ける実装ではありません。** PIT株式は既存v2エンジンで別途検証する設計で、500銘柄用の高速探索アダプター、企業行動・単元を反映した上位候補の詳細再生は未実装です。データも未取得。5ETFの結果を500銘柄版と表示しないでください。

## 分担・再開

4人に分担する場合、同じ入力・設定・出力ディレクトリで、それぞれ `--shards 4 --shard-index 0`（他は1/2/3）を指定。1シャードに書き込むプロセスは1つだけ。LLMの同時起動は必要なく、通常の並列プロセスでも実行できます。停止後は同じ引数に `--resume` を付けます。

```sh
OPENBLAS_NUM_THREADS=1 python scripts/run-research.py --output research/history-search-new --shards 4 --shard-index 0 --resume
# 全シャードが完了してから集約
OPENBLAS_NUM_THREADS=1 python scripts/run-research.py --output research/history-search-new --aggregate --summary research/history-search-new/summary.json
```

全候補が一度ずつ揃わない集約は拒否します。データ・設定・エンジンソースが変わった出力に上書き再開せず、新しい実験ディレクトリを作ってください。初回24,000試行の旧manifestはソースガード追加前なので、新CLIでの再開は拒否されます。初回台帳は保存済みで、新しい実験として再実行できます。

## 成果物と分析

- `manifest.json`：24,000候補の設定とハッシュ。
- `checkpoint-*.jsonl`：再開用の全候補結果。
- `all-candidates.csv`：不採用を含む全結果。
- `summary.json`：上位20、候補ID、学習・選別・監査、1/5/10年比較、コスト2倍、近傍設定、学習だけで選んだ候補の年別診断。
- 初回結果の公開台帳：[圧縮CSV](../reports/research-trials-2026-10-24.csv.gz)。独立照合は `python scripts/analyze-research.py`。

集計JSONを `src/research-results.json` に反映し、検証・ビルド・Sites公開を行うとスマホ画面に表示されます。公開先にPythonジョブサーバーや市場データ取得スケジュールはありません。

主担当への引き継ぎには、候補数・データハッシュ・設定ハッシュ・コード版・実行時間・採用判定を含めます。最高成績だけを報告せず、両期間で超過した候補の数、費用感度、近傍の成績、監査での悪化も伝えます。監査を見て修正した場合は既知データになったことを記録します。

## 候補固定後の前向き観察

これは採用ではなく、研究候補の継続観察です。モデルを自動再選択しません。実株の注文は作成・送信せず、連続比率の仮想資産を記録します。

```sh
python scripts/research-paper.py freeze --input research/history-v2-2026-10-24/reference-input.json
# 後日、同じ形式で新しい完全な日足履歴を取得してから
python scripts/research-paper.py observe --input /path/to/updated-reference-input.json
```

初回候補c01478を2026-10-24にローカルで固定済みです。`research/history-forward/model.json`と`state.json`はgit管理外で、初期資金100万円、新しい観測0日。`freeze`は既存モデルを上書きしません。別候補は別の`--model`と`--state`を指定します。

`observe`は固定日以降に完了したUTC日足のみを使い、最初の新しい終値以降の代理約定から記録します。重複実行は同じ日を二重記録せず、欠けた日を履歴から順番に進めます。モデル改変、エンジン変更、既に観測した過去価格の改訂は拒否します。データ訂正時は原因を照合して別実験として記録し、古い台帳を消して良い結果だけを残さないでください。

前向き台帳は保有継続の時価で、探索時の各期間末の仮想清算費用とは扱いが異なります。日次終値の連続比率モデルなので、実際の日本・米国市場の同時売買は再現しません。500銘柄の正式ペーパー運用・楽天実注文とは区別します。

## 次の研究に必要なもの

[ニュース取得・銘柄拡張計画](news-and-universe-plan.md)に、公式データ取得元、日米各200/250/300の比較、財務・企業開示を使う別系統の仮説、情報取得時刻・費用・欠落の扱いをまとめました。これは未実装の次期計画です。データ不足を埋めるまで既存ETF試行を500普通株・ニュース戦略の結果と読み替えず、必要情報を満たす仮説から実装・検証します。

日米の当時の投資可能集合・上場廃止・単元・企業行動・利用可能時刻付きの履歴データと、500銘柄用の探索アダプターです。テーマを変えて試行を増やす前に、データ要件とベンチマークの配当・通貨・費用を揃えます。

次の仮説候補は「SPYを基礎配分にして超過収益部分だけ別戦略にする」「日米普通株の業種内相対順位」「時点整合した財務の成長性・収益性と価格トレンドを併用する」。未実装・未検証の仮説であり、今回の結果を見て有効性が証明されたものではありません。
