<a id="chat-ocr-forward-activation"></a>
# Chat OCRの前進有効化

この候補は本番準備完了のreceiptではありません。本番作業には、別途固定した厳密なApply intentと、新しい人間の保守承認が必要です。Applyの既定モードは操作説明だけです。Validateは作業領域の公開artifactとruntime hashを検証し、本番DBを開かず、要求も送りません。Apply自身は昇格しません。

<a id="stable-history-and-configuration"></a>
## 安定した履歴と設定

コードとWeb assetは、manifest検証済みの単一immutable releaseから読み込みます。両SYSTEMタスクは保護された`chat_ocr_entry.py`、固定spec hash、明示roleを使用します。Chatには環境変数と`--root`で同じ明示data rootを渡します。環境の既定値、TLS、既存Chat／Gateway／Slack設定はそのdata rootから読み、私有設定をreleaseへコピーしません。

既存Chat DBのパスとfile identityを保持します。Gateway jobsとSlack制限付きストアも既存ファイルだけを使用します。各接続でidentityを確認し、保留中はSQLite `mode=ro`、有効化後は`mode=rw`、読み取り専用telemetryとOCR会話照会は常に`ro`を使います。配備経路は`rwc`を使わず、既存表の初期化、journal mode変更、schema修復、新しいbaseline採用、旧DB復元、別履歴作成を行いません。

起動時にはメモリー内だけで実行した参照DDLと、列型・NULL許可・既定値・主キー・unique index特性・必須indexを照合します。schema欠落／非互換や想定外triggerは保留します。前後比較では既存DB identity、schema catalog hash、main bytes、非空WALを照合します。読み取り接続でもSHMや空WALが割り当てられ得ますが、履歴変更とは表現しません。SQLite `immutable=1`で稼働WALを隠すこともしません。

新規データはOCR機能設定、mailbox DB、その生成spoolだけです。既存認証、モデル設定、Slack方針、秘密鍵、source checkout、知識ストアは変更対象外です。基盤process／listener identityと保護ファイルのhash／ACLを不変に保ちます。

<a id="single-forward-attempt"></a>
## 1回だけの前進試行

後続のApplyは、スクリプトによる自動再試行なしで次の順序を実行します。

1. 承認された正確なintent、全source／helper／runtime bytes、保護ファイル、全task定義とSDDL、旧PID／birth／command binding、対象の未作成、新しいServer／Client双方へ結び付く保守GOを確認します。すべてのChat／Slack／Continue利用者がidleと新規要求停止を確認していることが必要です。
2. 既存Librarian barrierを取得し、新しい認証済みidle snapshotを採取します。snapshotは件数だけであり、旧サービスには全体の受付フェンスがありません。未知・busyなら進みません。
3. 固定した2タスクの自動起動を一時無効化し、Chat→Gatewayを各1回通常停止します。強制終了や古いlock削除はしません。無効化タスクにinstance／process／listenerがないことを確認して進みます。
4. 既存ストレージを検証し、新しいAdmin/SYSTEM保護のrelease／spec／entry領域を作成します。両タスク無効中にactionだけを更新し、Task XMLとSDDLを予測済みScheduler正規化後の全定義に一致させます。登録triggerは無視し、ACEを保持します。
5. 新しい有効OCR設定を作り、検証済み新entryのEnabledを戻し、Gateway→Chatを各1回起動します。boot triggerかtriggerなしだけを認めます。既存Scheduler再試行設定を保持し、スクリプトは2回目のStop/Runをしません。後のScheduler起動にも同じimmutable entry／spec／DBガードを適用します。
6. 新サービスは当初、通常要求を拒否します。既存認証が最優先で、metadata healthとpin固定mTLS readinessだけを許可します。Chat worker、OCR保守、idle制御は有効化を待ち、起動だけで基盤／モデルを自動起動しません。最初の有効化時、既に動くモデルの状態を受動的に再照合して通常Slack受付に反映します。
7. HELD process／endpoint binding、既存ストレージ、保護状態を再検査します。release／spec／manifest／root／DBへ固定した永続的・保護されたactivation GOを最後の本番確定点としてatomicに公開します。将来の正規起動は、古いPIDやDB内容へ固定せず、この承認を利用できます。

receiptは新しい保護attempt領域だけへ書き、ユーザーが書き換えられるpacketの一時ファイルを経由しません。Python helperはhash検証した正確なbytesを実行します。モデル生成、OCR job配信、私有画像、外部AI要求、DB移行、旧版復元はApplyに含みません。

有効化前に失敗したら証拠を保持し、新しい前進修正を必要とします。旧コード／DB復元や黙った再試行はしません。旧タスク移行中の失敗では旧サービスが稼働し続ける場合があるため、receiptは受付状態を未確認と明記します。両方の新bindingを観測して初めて両新サービスがHELDと主張します。atomic GO確定後のreceipt保存が失敗しても、確定済みと報告します。

<a id="readiness-and-verification-limits"></a>
## Readinessと検証の限界

`GET /v1/chat-ocr/readiness`は、既存mTLS listener、実socket peer、既存role証明書pin、およびworker用の既存Host ownerヘッダー`X-LocalBrain-Approval-Owner`を使用します。有効化前はNOT_READYを返します。保護付き有効化検査と現在socketの認証後は、`CHAT_OCR_SERVER_READY`、正確なsource、既存履歴保持フラグ、認証済みendpointフラグ、UTC検証時刻、検証範囲を返します。Applyは実ループバックmTLSのhealth／readinessを検査します。ClientPCのreceiptは実ClientPC要求から得る必要があり、ローカルfixtureから作りません。このmetadataは実画像OCRやQwen生成の試験を意味しません。

公開試験はHELD受付、共有履歴保持、誤DB／欠落DB、WAL可視性、schema／unique制約、不正／古いGO、manifest／entry改変、新しいinterpreterによるartifact検証、gate再構築を扱います。Scheduler fixtureは実メモリー内定義の正規化とmockの登録task／folderメソッドを使用し、実タスクを取得・登録しません。実SYSTEM登録、サービス再起動、PC間mTLS、私有OCRは後続の本番検証であり、準備報告で成功済みと主張してはいけません。
