# 500暫定候補の比較・実行手順

既存の5ETF長期実験とは別の、価格だけの探索的比較。[結果](../reports/equity-screen-2026-10-26.md)と[固定条件](equity-screen-protocol.md)を先に確認する。

Python 3、NumPyが必要。試行はPythonの数値計算で、追加のAI API・有料データ契約は使わない。原価格は公開リポジトリに含めない。

## 公開成果だけの照合

```sh
python scripts/analyze-equity-screen.py --published-only
python -m unittest discover -s tests -p 'test_*.py' -q
npm test
npm run check
npm run build
```

公開照合は1,488行・探索空間・代表選択・表示結果を照合する。原価格からの再計算や18通りの買い持ち照合は含まない。画面検証は別ターミナルで `npm start` を起動し、PlaywrightとChromiumがある環境で以下を実行する。

```sh
python tests/browser_equity_research.py
python tests/browser_research.py
```

## 保存済み原データを持つ環境での再計算

必要なのは `research/history-free-universe-2026-10-25-final` の選定キャッシュと `research/history-v2-2026-10-24/raw` の指数・為替キャッシュ。具体的なパスとハッシュ契約は `scripts/equity_data.py` を参照。新規cloneだけでは原データは揃わない。[無料候補取得手順](free-universe-runbook.md)は取得を再実行できるが、現在の提供データが過去保存物と同じとは限らず、同一結果を保証しない。

```sh
python scripts/equity_data.py --output research/history-equity-screen-rerun
python scripts/run-equity-screen.py --input research/history-equity-screen-rerun/input.npz --output research/history-equity-screen-rerun/comparison-v1
```

入力と出力は上書きしない。実験途中で止まった場合、このランナーには再開機能がないため、新しい出力ディレクトリで全件を再実行する。約100秒は今回環境の実測であり、端末に依存する。各系統の全成績と全CSVを保存してから選別する。エンジン・探索空間・入力のSHA256を実行記録に保存する。

今回保存済みの実験一式がある環境では次で原価格を含めた独立照合ができる。

```sh
python scripts/analyze-equity-screen.py
```

`scripts/equity-concentration.py` は今回の固定首位候補の利益寄与と銘柄除外の事後診断専用。保存済み `comparison-v1/concentration.json` がある場合は上書きを拒否する。選別には使わない。

## 画面と今後の実験

`src/equity-screen-results.json` が計算済み成果、`src/equity-research.js` が表示。研究室で「500候補」と従来の「5ETF」を切り替える。画面から大量試行・実際の株式注文は行わない。設定ダウンロードも実注文用ではない。

今回の価格戦略は1年のみ評価可能。5年・10年は未評価。新しいデータ・戦略・選別規則は、新しい事前固定プロトコルと出力ディレクトリで実行し、今回結果を上書きしない。過去の勝ち銘柄を外しただけでも当時の候補集合を再現したことにはならない。

実装上の補足：レンジ突破は退出条件が生じた場合、定期見直し日以外でもポートフォリオ全体の再配分を行う。指標の日数と年率変動率の252日換算は、日米の休場日が異なる共通日付集合に対する近似。
