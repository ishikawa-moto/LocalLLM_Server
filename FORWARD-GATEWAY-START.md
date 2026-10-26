<a id="forward-start-after-the-verified-registration-hold"></a>
# 登録確認で保留した後の前進起動

これは別途レビューした起動専用操作です。元Applyの再実行、そのUAC承認／GOの再利用、旧コード／DB復元、移行／採用の再実行、モデル起動はしないでください。

<a id="proven-current-state"></a>
## 確認済みの当時の状態

最初のnative Applyは所有Gatewayを1回停止し、運用baselineを1回採用し、制限付きretry_linksをコミット・検証して、両lockを解放しました。登録は成功しましたが、登録後の文字列hash検査が起動前に保留しました。そのreceiptのTaskUpdated=falseは、登録がなかったという意味ではありません。

正確な原因は保存済みの計画hashで実証しています。現在のDefinition.XmlTextから追加されたRegistrationInfo/SecurityDescriptor nodeだけを除くと、登録前定義の全digestを再現できます。現在のtask引数、SYSTEM principal、Python image、外部security descriptorは予定値と一致し、taskはReady、instanceは0です。action不変だけから無害な整形差と推定するより強い証拠です。

Schedulerの登録XMLとメモリー内定義の直列化は、既定項目の省略・展開・順序などが異なります。後続Applyは計画定義へ既存SDDLを明示し、採取した登録XMLをSchedulerのメモリー内schemaで正規化して、canonical全定義と実security descriptorの一致を必須とした後、登録raw XMLを固定します。不明な設定／security差は引き続き拒否します。

<a id="fixed-start-only-scope"></a>
## 固定した起動専用範囲

Resume-GatewayRelease.ps1は、旧b661b353の関数libraryと4f996de1のproducer-barrier libraryだけに制限します。正確なbyte bufferを検証して関数定義だけを取り込み、旧プログラム本体は実行しません。停止、task登録／更新、コード導入、metrics採用、DB移行、knowledge byte lock取得の呼出しはありません。

新しいrecovery intentは、以前の全HOLD receipt、元intent、導入済み元source commit／manifest／bootstrap／pointer、全導入object ACL、現在のraw Task XML、全Definition.XmlText、外部SDDL、引数、保存済み登録前hash、既存コミット済みartifact3件を結び付けます。現在定義のdescriptor node1件の追加を除くことで保存済みhashを再現し、そのdescriptorと不変の外部SDDLを一致させます。汎用的にXML差を許す回復ではありません。

元receiptと移行／採用ファイルはimmutableのままです。新receiptはCreateNewで作り、不明な既存証拠は上書きしません。初回作成に成功して初めてworkerが後続更新を所有します。既存artifactは固定hashを保持し、元の全DB classifierは既に採用した単一の有効anchorと制限付き移行receiptを使用します。別baselineを採取しません。

操作には新しい正確な人間承認のUACと、15分TTL・明示的な整数active0/queued0・新規要求停止の約束を含む新ClientMaintenanceConfirmedResumeGOが必要です。Inspectは読み取り専用でGOや管理者操作を必要としません。native事前検査の失敗も新しい所有receiptへ記録します。

Ready・instance0・listenerの厳密な確認と、保護source／設定／基盤process／導入code／ACL／全DB検査の後、workerは既存Librarian presence barrierを所有し、検証済みStartExactを1回呼びます。PID／birth観測前にScheduler GUIDを保存し、image／command／Scheduler親／release／status／DB結果を検証します。barrierは最終検査まで保持してfinallyで解放し、解放後だけ成功を保存します。busy、差分、不明identity、Run失敗、health失敗は再試行や自動復元なしで保留します。

導入済みGateway sourceは既存の元候補のままです。保守helperを含む新repository commitは、2回目のアプリ配備ではありません。Client側の認証済みcapabilities、Client Slack起動、公開実モデル試験、mainマージは親タスクが所有します。本番ローカルmain同期は、回復成功と正確な既知差分監査が終わるまで延期します。

<a id="qualification-boundary"></a>
## 検証の境界

公開fixtureは、登録済みtask操作をせずメモリー内Scheduler NewTaskだけでdescriptor挿入を再現し、全定義検証を試験します。無関係な設定／security差、Int32とInt64双方の件数を使う偽造GO、既存artifact改変、不明receipt置換を拒否します。起動手順stubはproducer busy、受付前DB差分、Run1回の失敗後処置、成功時最終検査を扱います。本番XML、SID、データ、TLS認証情報、モデル、ネットワーク要求は使いません。

実SYSTEM起動とruntime結合は別のnative検証です。後続失敗では証拠を保持し、新たな前進計画を作り、いずれの操作も再実行しません。
