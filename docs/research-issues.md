# AI株式研究のIssue一覧

2026-10-26作成。全件計画／未検証。Issueの作成は実装・有効性の確認を意味しない。

[親Issue #1](https://github.com/syoudai0514/kabu/issues/1) / [検討資料](ai-research-review.md) / [仮説台帳](../research/hypothesis-register.json) / [再評価テンプレート](templates/research-review.md)

| ID | 優先度 | Issue | 依存する作業 |
| --- | --- | --- | --- |
| R1 | P0 | [#2 [P0][R1] 今回の利益と損失を購入前の情報で監査する](https://github.com/syoudai0514/kabu/issues/2) | 独立して着手可能 |
| R2 | P0 | [#3 [P0][R2] 当時の銘柄集合・企業行動・配当と無料データの不足を整える](https://github.com/syoudai0514/kabu/issues/3) | 独立して着手可能 |
| R3 | P0 | [#4 [P0][R3] 時刻付き財務・開示と根拠付き抽出を保存する](https://github.com/syoudai0514/kabu/issues/4) | 独立して着手可能 |
| R8 | P0 | [#5 [P0][R8] 時間順検証・多数試行・採否基準を事前固定する](https://github.com/syoudai0514/kabu/issues/5) | 独立して着手可能 |
| R4 | P1 | [#6 [P1][R4] AIの5シナリオを証拠・反証付きで追跡する](https://github.com/syoudai0514/kabu/issues/6) | [R3 #4](https://github.com/syoudai0514/kabu/issues/4) |
| R5 | P1 | [#7 [P1][R5] 株価が要求する成長をシナリオで評価する](https://github.com/syoudai0514/kabu/issues/7) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R3 #4](https://github.com/syoudai0514/kabu/issues/4) |
| H1 | P1 | [#8 [P1][H1] 業績改善＋押し目買いの追加効果を検証する](https://github.com/syoudai0514/kabu/issues/8) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R3 #4](https://github.com/syoudai0514/kabu/issues/4), [R8 #5](https://github.com/syoudai0514/kabu/issues/5) |
| H2 | P1 | [#9 [P1][H2] 業績修正後の順張りを時刻と約定遅延込みで検証する](https://github.com/syoudai0514/kabu/issues/9) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R3 #4](https://github.com/syoudai0514/kabu/issues/4), [R8 #5](https://github.com/syoudai0514/kabu/issues/5) |
| H3 | P1 | [#10 [P1][H3] AI導入による生産性改善と株式成績を検証する](https://github.com/syoudai0514/kabu/issues/10) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R3 #4](https://github.com/syoudai0514/kabu/issues/4), [R4 #6](https://github.com/syoudai0514/kabu/issues/6), [R8 #5](https://github.com/syoudai0514/kabu/issues/5) |
| R9 | P1 | [#11 [P1][R9] 100万円・単元・集中・実際の約定条件で候補を再評価する](https://github.com/syoudai0514/kabu/issues/11) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R8 #5](https://github.com/syoudai0514/kabu/issues/5) |
| R10 | P2 | [#12 [P2][R10] 候補固定後のペーパー観察と再評価履歴を残す](https://github.com/syoudai0514/kabu/issues/12) | [R8 #5](https://github.com/syoudai0514/kabu/issues/5), [R9 #11](https://github.com/syoudai0514/kabu/issues/11), [H1 #8](https://github.com/syoudai0514/kabu/issues/8), [H2 #9](https://github.com/syoudai0514/kabu/issues/9), [H3 #10](https://github.com/syoudai0514/kabu/issues/10) |

T1〜T5の技術シナリオは #6、V1の価格評価は #7 に紐付く。H1/H2/H3はそれぞれ独立した売買仮説として比較する。R10の依存条件は3仮説すべての成功ではなく、各評価結果と不足が整理済みであること。

P0を先行し、データが足りない仮説は `blocked` と不足理由を記録する。数値の採否条件・窓・期間が未決なら実験前に固定する。無料で履歴が揃わない5年・10年は未評価とする。

Issueを閉じる際は成果物・実験ID・判定・再評価記録を残す。棄却も研究として完了可能だが、完了・評価済みは実運用承認ではない。

再評価の定期案は四半期ごと、加えて新開示・技術変化・データ訂正・事前閾値到達時。定期ジョブや通知は未設定。仮説台帳の状態と最終レビューを更新し、過去の実験記録は上書きしない。
