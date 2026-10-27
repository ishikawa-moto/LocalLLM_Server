<a id="slack-server-validation"></a>
# Slack Serverの検証

<a id="implemented-scope-and-bounded-startup-qualification"></a>
## 実装範囲と上限付き起動検証

Serverは、検証済みHost認証chatに対するローカルモデル起動を任意に有効化できます。公開設定例はenabled、human_apply_enabled、model_cold_startをfalseに保ちます。内部mTLS、Host接続元／owner検査、証明書pin、制限データの送信先制御、既存model／context／KV設定は維持します。

- 隔離した全公開回帰は**142件、失敗・エラー・skip0**でした。起動新規9件と既存SSE、取消、restriction、lifecycle、採用、退役を含みます。fixtureは本番設定やモデルではなく、試験作成のloopback listenerを使います。
- 起動試験はcold／warm、同時起動の集約、STARTINGへのforeground合流、foreground queued／active優先、基盤readiness、所有起動の停止排除、自動再試行／StopLLMなしの失敗、不正入力、起動しないread／reviewを扱います。
- 起動差分と正確な3対象配備／rollback手順の限定独立読み取りreviewは指摘なしでした。このPRの全機能に対する独立検証ではありません。
- 別途承認した公開入力確認で、既存認証Client経路を通じSTOPPED → STARTING → READY → 応答完了を確認しました。初回chatは推論前deferred／retry_safe:trueで、ready後に同一要求だけを送信しました。推論は1回で公開markerが一致し、Session退役と最終idleも確認しました。強制停止や追加モデル試験はしていません。
- 既存SSEはrole-null、finish、usage-only metadataを受理しつつ、内容不正、完了marker、frame size、取消検査を維持します。公開試験はこれらの境界を扱います。

この限定確認は、すべての実Slack、OCR／圧縮、再接続／回復、人間によるknowledge／Rule適用、両側の保持／outbox、優先度負荷、別PC／モバイル手順を証明しません。Client認証と添付実装は別管理です。私有Slack本文、OCR文、生成回答、認証情報、DB内容はこのrepositoryやreview証拠へ含めません。

<a id="approved-chat-entry-and-human-approval"></a>
## 承認済みChat入口と人間承認

2026-10-21に別途承認した導入で、Chat入口を配備し、人間に結び付く制限knowledgeと列挙済みSlack応答Rule適用を有効化しました。6対象展開は正確なsource／config preimageと所有するChat／Gatewayの通常再起動を使い、他のモデル・認証・サービス設定は保持しました。この展開の推論や本番適用試験はしていません。

- 実Chat入口は有効で、静的HTML／script hashがreview済み公開sourceと一致しました。新規chatボタンの下からClient HTTPSパスワードページへ直接リンクします。HTTP chatで承認を認証・代理しません。既に開いているページは再読込みが必要です。
- ClientPCは管理者ブラウザー証明書なしでHTTPSログイン200と未認証提案アクセス401を独立確認しました。既存Host mTLS／IP／owner認証で人間による制限採用とRule有効化が有効、automatic／ordinary SecondBrain採用・external AI・cloud fallbackが無効と報告しました。
- 不変のapproval／menu sourceで承認試験42件、Chat12件、公開承認境界の重点2ケースが成功し、ClientPCも隔離公開試験101件を実行しました。以前の全回帰142件の代わりや合算による水増しではなく、別範囲の実行です。候補だけではaccepted knowledge／Ruleは変わらず、偽承認を拒否し、正確なHost receiptが各公開knowledge／Rule fixtureを1回だけ適用しました。固定展開の限定独立reviewは指摘なしでした。
- 実展開は旧receiptとbackupを保持し、対象hashとサービスPID／birth／listener bindingが所有配備と一致しました。Chat task-instanceの直接再読取りは権限に制約があり、正確な配備receiptを保持しました。再確認用の追加UACや再起動はしていません。

候補は認証済み管理者が正確な内容を明示承認するまでpendingです。この検証は私有提案の実採用、本番Ruleの試験変更、携帯端末での承認操作を意味しません。公開templateは既定無効のままです。

<a id="lifecycle-and-retry-boundaries"></a>
## ライフサイクルと再試行の境界

起動を要求できるのは検証済み認証chatだけで、ローカルopt-inが許す場合に限ります。並列起動せずSTARTINGを共有し、foreground受付はchatとreviewより優先します。background起動失敗はStopLLMや自動再試行を呼びません。status、capabilities、自律reviewはモデルを起動しません。

永続予約より前の明示deferredだけがretry-safeで、Hostは後でreadyを確認して同一要求を再送できます。timeout、中断応答、不確実予約は再推論の許可ではありません。既存Request／Session identityを保持して永続statusを調べます。warmモデルを停止してcold試験を作り出さないでください。

<a id="deployment-and-rollback"></a>
## 配備とrollback

実装範囲はmainへのreview準備が整っており、残る検証は明示した制約です。mergeだけでは配備／有効化しません。各本番変更に正確な承認範囲、既知preimage、独立review済み手順が必要です。起動変更はGateway source2件を置き換え、ローカルmodel_cold_start flagだけを追加します。無効な公開例は本番設定ではありません。

登録済みGateway taskを停止する前に、新しいsafe-idle、正確なtask instance GUID／PID／birth、実行ファイル／引数binding、不変の保護依存、正確なsource／config hashを確認します。Schedulerが返すstart identityをhealth検査より先に永続化します。この展開のためにPID kill、task修復、他サービス停止をしてはいけません。

rollbackには所有execution receipt、厳密な既知old／new bytesとbackup hash、不変の依存、新しいsafe-idle観測が必要です。未知編集、busy、status到達不能、不完全receipt、置換processは照合を必要とします。現行の私有DB、accepted knowledge、Rule方針、receipt、退役tombstoneを保持し、退役Sessionを復活させる古い状態を復元してはいけません。

<a id="earlier-fixture-evidence"></a>
## 以前のfixture証拠

初期候補は公開fixture109件、その後に退役専用16件が成功しました。SSE追加修正では全133件が成功し、現行142件は起動source変更に対する新しい全実行です。複数回の件数を合算して単一の過去実行として示すものではありません。

退役fixtureは、認証、厳密restriction／schema、推論なし、Session限定整理、accepted knowledge／Rule／採用済み2-pass audit保持、厳密再送、active／unresolved／application／writer見送り、受付競合、tombstone、transaction rollbackを検証します。SQLite secure_deleteの対象は削除DB cellで、WAL、backup、storageからの法科学的消去はfixtureの主張外です。


<a id="2026-10-21-resource-admission-integration-fixes"></a>
## 2026-10-21の資源受付統合修正

全公開回帰148件が成功し、失敗・エラー・skipは0でした。本番設定やモデルは使用せず、通信はfixture所有のループバックlistenerに限定しました。

永続予約前の資源拒否は、retry_safe=trueを持つ構造化deferredを返します。資源回復後に同じ固定要求が1回完了でき、その後はcached結果を返します。負荷時もcached status／resultと取消は利用可能で、起動失敗や不確実結果をretry-safeへ変換しません。

認証済みbackground cold startは実モデルhealthを照合し、foreground condition lock外でcold-start RAM／GPU受付を採取します。続けて所有権、queued／active work、lifecycle stateを再確認して起動1件を予約します。fixtureはGPU空き不足の古いREADY、採取中のforeground到着、受付拒否、集約起動、自動再試行／強制停止なしのFAILEDを扱います。

この更新はPR sourceだけを変更します。本番配備、サービス再起動、モデル起動、実Slack推論はしていません。以前の実機検証は以前の導入buildを対象とし、mergeだけで修正が配備されるわけではありません。
