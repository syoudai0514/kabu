# 研究から実売買へのIssue一覧

2026-10-27更新。親Issue＋18作業Issue。全作業は計画／未実装・未検証。既存の価格試験の結果は保存済みだが、ここに挙げた仮説・実行機能の完成を意味しない。

[親Issue #1](https://github.com/syoudai0514/kabu/issues/1) / [追加設計](research-to-live-plan.md) / [AI検討資料](ai-research-review.md) / [仮説台帳](../research/hypothesis-register.json) / [再評価テンプレート](templates/research-review.md)

| ID | 優先度 | Issue | 完了判定に必要な成果物 |
| --- | --- | --- | --- |
| R1 | P0 | [#2 [P0][R1] 今回の利益と損失を購入前の情報で監査する](https://github.com/syoudai0514/kabu/issues/2) | 独立して着手可能 |
| R2 | P0 | [#3 [P0][R2] 当時の銘柄集合・企業行動・配当と無料データの不足を整える](https://github.com/syoudai0514/kabu/issues/3) | 独立して着手可能 |
| R3 | P0 | [#4 [P0][R3] 時刻付き財務・開示と根拠付き抽出を保存する](https://github.com/syoudai0514/kabu/issues/4) | 独立して着手可能 |
| R8 | P0 | [#5 [P0][R8] 時間順検証・多数試行・採否基準を事前固定する](https://github.com/syoudai0514/kabu/issues/5) | [R11 #13](https://github.com/syoudai0514/kabu/issues/13) |
| R4 | P1 | [#6 [P1][R4] AIの5シナリオを証拠・反証付きで追跡する](https://github.com/syoudai0514/kabu/issues/6) | [R3 #4](https://github.com/syoudai0514/kabu/issues/4) |
| R5 | P1 | [#7 [P1][R5] 株価が要求する成長をシナリオで評価する](https://github.com/syoudai0514/kabu/issues/7) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R3 #4](https://github.com/syoudai0514/kabu/issues/4) |
| H1 | P1 | [#8 [P1][H1] 業績改善＋押し目買いの追加効果を検証する](https://github.com/syoudai0514/kabu/issues/8) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R3 #4](https://github.com/syoudai0514/kabu/issues/4), [R8 #5](https://github.com/syoudai0514/kabu/issues/5) |
| H2 | P1 | [#9 [P1][H2] 業績修正後の順張りを時刻と約定遅延込みで検証する](https://github.com/syoudai0514/kabu/issues/9) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R3 #4](https://github.com/syoudai0514/kabu/issues/4), [R8 #5](https://github.com/syoudai0514/kabu/issues/5) |
| H3 | P1 | [#10 [P1][H3] AI導入による生産性改善と株式成績を検証する](https://github.com/syoudai0514/kabu/issues/10) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R3 #4](https://github.com/syoudai0514/kabu/issues/4), [R4 #6](https://github.com/syoudai0514/kabu/issues/6), [R8 #5](https://github.com/syoudai0514/kabu/issues/5) |
| R9 | P1 | [#11 [P1][R9] 100万円・単元・集中・実際の約定条件で候補を再評価する](https://github.com/syoudai0514/kabu/issues/11) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R8 #5](https://github.com/syoudai0514/kabu/issues/5), [R13 #14](https://github.com/syoudai0514/kabu/issues/14) |
| R10 | P2 | [#12 [P2][R10] 候補固定後のペーパー観察と再評価履歴を残す](https://github.com/syoudai0514/kabu/issues/12) | [R8 #5](https://github.com/syoudai0514/kabu/issues/5), [R9 #11](https://github.com/syoudai0514/kabu/issues/11), [R12 #17](https://github.com/syoudai0514/kabu/issues/17) |
| R11 | P0 | [#13 [P0][R11] 最適候補の目的・制約・選別規則と不採用条件を固定する](https://github.com/syoudai0514/kabu/issues/13) | 独立して着手可能 |
| R13 | P0 | [#14 [P0][R13] 楽天の国内・米国の発注経路と無料実行環境を確認する](https://github.com/syoudai0514/kabu/issues/14) | 独立して着手可能 |
| H4 | P1 | [#15 [P1][H4] 相場局面・固定戦略合成と単一戦略を比較する](https://github.com/syoudai0514/kabu/issues/15) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R8 #5](https://github.com/syoudai0514/kabu/issues/5) |
| H5 | P1 | [#16 [P1][H5] 長期成長の保有継続・売却・再参入条件を比較する](https://github.com/syoudai0514/kabu/issues/16) | [R2 #3](https://github.com/syoudai0514/kabu/issues/3), [R8 #5](https://github.com/syoudai0514/kabu/issues/5) |
| R12 | P1 | [#17 [P1][R12] 研究・ペーパー・実発注の判断と注文台帳を共通化する](https://github.com/syoudai0514/kabu/issues/17) | [R9 #11](https://github.com/syoudai0514/kabu/issues/11), [R13 #14](https://github.com/syoudai0514/kabu/issues/14) |
| R15 | P1 | [#18 [P1][R15] スマホの本人操作・停止確認と無料での常駐運用を設計する](https://github.com/syoudai0514/kabu/issues/18) | [R13 #14](https://github.com/syoudai0514/kabu/issues/14), [R12 #17](https://github.com/syoudai0514/kabu/issues/17) |
| R14 | P2 | [#19 [P2][R14] ペーパーから限定実運用・拡大への条件と停止復旧を固定する](https://github.com/syoudai0514/kabu/issues/19) | [R10 #12](https://github.com/syoudai0514/kabu/issues/12), [R12 #17](https://github.com/syoudai0514/kabu/issues/17), [R13 #14](https://github.com/syoudai0514/kabu/issues/14), [R15 #18](https://github.com/syoudai0514/kabu/issues/18) |

T1〜T5は #6、V1は #7 に紐付く。H1〜H5は別々の仮説として比較する。H5で業績による退出を使う版のみ、R3の時点付き情報も必要。

**依存関係は完了判定に必要な成果物であり、調査や契約設計・模擬検査を先行してよい。** R10は事前検証を通った少なくとも1候補が必要だが、H1〜H5すべての完成や成功を待たない。価格基準からの開始も可能で、データがない仮説は保留する。

## 次に進める順序

1. #13で選別目的・制約、#14で楽天・無料環境の成立範囲を具体化する。
   #2〜#4の原因・データ監査を進め、#5の数値条件と未見評価を固定する。
2. データが揃ったH1〜H5を実験し、#11・#17で詳細な売買条件と台帳を検査する。
3. #12で固定候補の新データ観察。#18・#19の条件が揃った範囲で実運用への移行を判断する。

有効な候補がなければ採用しない。基準を緩める・モデルを変更する場合は新しい実験として記録する。無料で履歴が揃わない5年・10年は未評価。

Issueを閉じる際は成果物・実験ID・判定・再評価記録を残す。棄却も研究として完了可能だが、完了・評価済みは実運用開始ではない。G0〜G5の移行は[草案](../research/promotion-policy.draft.json)の未決項目を埋め、各段階の証拠で判断する。

四半期や新開示・技術変化・データ訂正・事前閾値到達時の再評価を計画する。定期ジョブ・通知・実発注は未実装。旧実験・旧結論を上書きせず、戦略変更は新モデルIDで追う。
