# Gateway 単独の前進更新と明示的復旧

今回だけの既存Forward派生です。汎用配備システムではありません。
runtimeは既存 `f5df6bd12e9a87333f06948966296f80b1167d35` の146ファイル、
手順は別コミットの固定bytesに束縛します。旧runtimeは `55a206943b43cf2041742049b7265333dff91ae8` です。
本資料やGO templateの存在は、配備済み・保守承認済みを意味しません。

## 変更範囲

- SYSTEMの `LocalBrain Control Gateway` だけを無効化・停止・action更新・有効化・起動します。
- `LocalBrain Chat Web` は停止・更新しません。旧定義、security、PID、開始世代、task instance、
  command、固定spec・全コード、HTTP healthを照合します。
- 新release/spec/GOは新しい保護領域へ配置します。旧release/spec/GO/receipt/XMLは保持します。
- 設定・認証・モデル・DB bindingは維持し、DB migration・履歴巻戻し・モデル起動は配備処理に含めません。
- DBはfile identityとschema metadataのみ比較します。Slack本文、全テーブル行、DB/WALの全内容は読みません。
  この確認を内容checksumや論理行の全比較・backupとは呼びません。
- 新Slack OCRは有効化しません。既存mailboxの活動件数とGatewayの実状態も停止前に確認します。

## 読み取りとpacket準備

`Capture-GatewayForward.ps1` は配備しません。旧正式packetと正式結果の保存済みspec/GO/receiptを照合し、
現在の両taskの定義、security、プロセスと設定hashを取得します。最新SYSTEM情報が通常権限では取得できない場合は、
人間が承認した正規の管理者PowerShellでこの読み取りだけを行います。
拒否された情報を推測で補ったり、権限・OS保護を変更したりしません。
モデルは保存済み期待hashを記録し、実際のbytesは後の昇格Applyでread lock下にhash照合してから停止に進みます。

```powershell
.\deployment\Capture-GatewayForward.ps1 -DataRoot <private-data-root> `
  -ReferencePacket <preserved-old-packet> -ReferenceResult <preserved-result> `
  -ModelBaseline <preserved-model-bindings> -EmbeddingBinding <preserved-embedding-binding> `
  -Python <approved-python.exe> -Output <new-baseline.json>
```

`prepare_gateway_forward.py` は指定Git objectと読み取りbaselineから新規packetだけを作成します。
本番コード・設定・DBを作業領域へコピーしません。task定義等の非公開metadataを含むpacketはGitへ登録しません。

```text
python -B deployment/prepare_gateway_forward.py --repository <server-checkout>
  --baseline <new-baseline.json> --old-packet <preserved-old-packet>
  --procedure-commit <reviewed-40-character-procedure-commit> --packet <new-empty-packet-path>
```

作成後、`Apply-GatewayForward.ps1 -Mode Validate -Packet <packet> -ApprovedIntentSha256 <intent-hash>` で
source、manifest、spec、entry、手順、runtime、baseline、全146ファイルを照合します。
新specは旧specのsource/manifest/release/entry/GOの配置以外を変更できません。

## 保守承認と正規UAC

準備・限定検証・コミット後に、実行するpacketと停止範囲を人間へ提示します。
Continue/Slack/Chatの新規要求を止める保守時間を確認してから、templateの実値を埋めた
`apply-go.json` を作成します。過去のGO・PID・UAC承認を再使用しません。

GOは今回intent/source/manifest/script/clientへ束縛し、明示承認参照、Continue GUI成功の根拠、
実際のactive/queued=0、新規要求を出さない確認、timezone付き作成日時を要求します。有効期間は900秒です。
状態不明を0と解釈しません。snapshotは全クライアントに対する技術的な受付封鎖ではないため、
保守中の人間による新規要求抑止も必要です。

`Invoke-GatewayForwardUac.ps1` は自己昇格しません。人間が正規UACを承認したPowerShellから、
固定bytesを検証して実行します。既存のruntime ACL/lockと、保護されたApplyコピーの実行方式を再利用しています。
呼出しは `-Mode Run -Operation Apply -Packet <packet> -ApprovedIntentSha256 <intent-hash>
-ParentGoSha256 <fresh-go-hash> -WorkerSourceSha256 <worker-hash> -LaunchId <new-guid>` です。
実作業は各1回までで、同じattemptは再使用しません。結果不明なら自動再試行しません。

## 旧Gatewayへの復旧

復旧も自動実行しません。別の保守確認と新規 `restore-go.json` を必要とし、
exact Apply receipt hashをGO・CLI双方へ束縛します。新規UACの `-Operation Restore` と
`-ApplyReceiptSha256 <exact-protected-apply-execution-hash>` で同じ固定手順を使います。

|特定できた状態|許可する復旧|
|---|---|
|OldStopped：旧定義のまま停止・無効化済み|旧定義・停止世代・全待受不在を確認し、旧entryを有効化・起動|
|NewStopped：新定義へ更新済み、未起動|新定義・security・全待受不在と旧終了を確認し、旧actionへ戻して起動|
|HeldNew：receiptが特定した新プロセスがheld、新GO/pending GOなし|新task/PID/開始世代/instanceを照合して停止し、旧actionへ戻して起動|
|ActiveNew：正常に有効化済みの新Gateway|新GO/hash・現行process・idleを確認して停止し、旧actionへ戻して起動|

復旧でDBを戻したり、新旧GOを削除・編集したりしません。Chatのtask/processも維持します。
未知の登録途中・起動結果・再起動世代、未終了プロセス、DB identity/schema不一致、
不健康でidleを証明できないActiveNewは、今回の限定復旧範囲ではHOLDです。
推測したkill・強制停止・履歴復元で進めません。

## 最小確認

変更した手順は `Test-GatewayForwardGuards.ps1` と `test_gateway_forward_checks.py` に限定して確認します。
本番切替後はmTLSのstatus/readiness、正規PC73からContinue/Slackの受付、Work単発推論、
優先順位/FIFO/非割込み、残ジョブ0、旧Chatの定義/spec/process/code不変とHTTP healthを確認します。
Browser Chat実会話は、HTTP healthやRunner推論と区別し、可能なら公開固定文字列1回だけ使います。
PC73をヘッダー/IPで偽装せず、実認証の協力が得られない項目は未確認として記録します。
