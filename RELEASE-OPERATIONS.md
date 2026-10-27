<a id="fixed-gateway-release-and-deployment-operations"></a>
# 固定Gatewayリリースと配備操作

統合したSlack回復候補へ段階的release境界を追加します。これ自体で配備、mainマージ、モデル起動は行いません。

<a id="code-versus-stable-data"></a>
## コードと安定データの分離

最初の管理対象Gateway releaseは次を使用します。

- コード：ProgramData/LocalBrainServer/releases配下のcommit固定ディレクトリ
- 検証entry：ProgramData/LocalBrainServer/gateway_entry.py
- 有効pointer：ProgramData/LocalBrainServer/gateway-current.json
- 安定data root：既存のローカル配備フォルダー。intent.jsonへ非公開で記録

設定、TLS認証情報、DB、モデル、llama.cpp依存、知識、ログは安定data rootに残し、コードreleaseへのコピーや他PCへの送信はしません。release manifestはGit追跡済みの製品／source／手順ファイルだけです。新ProgramData treeはAdministrators/SYSTEMにFullControl、UsersにRead/Executeを与え、既存data ACLは変更しません。

Gateway taskの既存SYSTEM principal、実行ファイル、作業フォルダー、schedule／settings、security descriptorは保持します。変えるのはPython isolated mode（-I -B）、検証entry、固定ローカルpointerを使う引数だけです。登録はUPDATEで、CREATE_OR_UPDATEは使いません。task欠落／変更は保留します。

site_settingsはCODE_ROOTと、任意の絶対LOCALBRAIN_DATA_ROOTを分離します。Gatewayは設定／状態に安定data root、Manage-LocalBrain.ps1に凍結CODE_ROOTを使います。release所有の管理コードは将来のBrain／Proxy／Gateway child起動にも凍結コードを使い、Brainへ安定--rootを明示し、既存の安定モデル／binary pathを使用します。調整値や認証設定は変えません。将来のrelease所有Brainからのreviewer配信もrelease wrapperと安定data rootを使います。

既存の独立Chat／Reviewer定期taskと稼働Brain／Proxy／Embeddingは、この最初のGateway切替では起動先を変えず再起動もしません。identityを保護し、既存起動pathを保持するため、main同期が次回の独立起動で使うコードを変え得ます。この限界を明示し、後続component切替では独自の試験済みversion／identityを記録します。インストール全体が凍結済みと説明してはいけません。

<a id="preparation-only"></a>
## 準備だけの操作

Prepare-GatewayRelease.ps1の既定はDescribeです。明示Prepareにはcleanで正確な候補commit、以前の本番file／dependency manifest、分類済みDB baseline、観測済みtask／listener identityが必要です。これらを確認し、既存prepared payload／intentや不明ProgramData内容を拒否して、workspace packetだけを準備します。DDL、task変更、process停止、本番書込み、UAC要求は行いません。

非公開intentにはsource commit／tree、全staged file、Python実体、保護source／config hashとACL、DB identity／ACL metadata、完全な型付きDB baselineを結び付けます。部分失敗後に準備済みassetを黙って置き換えてはいけません。

snapshot helperと全payload fileは正確なimmutable Git commit blobから読みます。テキストはcommit済みLF bytesを保持し、checkoutのCRLF変換や後の作業tree編集でstaged releaseを変えません。binaryはtext変換なしでコピーします。準備後にもHEADとcleanを再検証します。

<a id="database-boundary"></a>
## DBの境界

release_db.pyは型付きcell符号化と整列した行hashで、sqlite_sequenceやshadow tableを含む全SQLite表をsnapshot化します。DBごとに1つの読み取りtransaction内でschema、user_version、整合性を記録します。5DBのsnapshotは独立しており、DB横断atomic checkpointではありません。

追加DDLの対象は制限付きストアだけです。

    CREATE TABLE retry_links(
        previous_id TEXT PRIMARY KEY,
        next_id TEXT UNIQUE NOT NULL,
        root_id TEXT NOT NULL,
        attempt_index INTEGER NOT NULL
    )

primary／unique制約で暗黙index2件が作られます。既存表の行とuser_versionは不変が必須です。既存retry表は既知の厳密DDL・列・非partial暗黙unique index2件と、trigger／FK／不明objectなしの場合だけ受理します。想定外schema、busy／未知状態、未分類baseline差は保留です。完全なDB v2証拠が許すのはMETRICS-DEPLOYMENT-CONTRACT.mdの固定index-search-counters-v1だけで、全業務行は厳密一致、既知の全metrics profileを全型付きdigestへ結び付け、元baselineから累積32以内の検索ペア増加だけを認めます。他の全変化はHOLDで、検査成功でもbaselineは更新しません。移行に汎用DB初期化や履歴の再分類は含みません。

SQLite commit前の失敗は、その未commit transactionだけをrollbackします。commit後の後続失敗は追加schemaと現状を保持し、backup DBや過去runtimeへ復元しません。

<a id="parent-coordination-and-native-windows-boundary"></a>
## 親との調整と正規Windows操作の境界

Apply-GatewayRelease.ps1の既定はDescribe、Inspectは読み取り専用です。Applyには正確なintent hashと、新しいhash固定apply-go.jsonが必要で、ClientMaintenanceConfirmedApplyGO、Client active／pending0、新規要求停止、親message参照を含めます。15分の操作窓はGO記録の作成から始まります。既存の人間承認に基づく工程調整のguardです。

準備中にGOは生成しません。最終hash観測後に親が実GOを送ります。その後、正規consent promptでnative Windows UACを1回要求し、手順はクリックも迂回もしません。通常の実行権限拡張はWindowsの承認の代わりにはなりません。

昇格後はまず実SYSTEM image／command lineとScheduler親を読み、固定task／GUID／PID／birthへ一致させます。不明なら停止前に失敗します。設定／依存／source hash、file所有権、厳密分類検索ペア付き全DB snapshot、現在idle、保護サービスidentityを再照合します。同じ上限付きApply事前検査でimageを確認できるなら、別の読み取りUACは不要です。

<a id="apply-sequence-and-failure-handling"></a>
## Applyの順序と失敗処理

1. 単一操作mutexを取得し、既存／pending execution receiptを拒否します。
2. GO、固定asset、旧task／image identity、source／config／DB baseline、idle／基盤サービスを検証し、receiptを永続保存します。
3. 旧Gateway稼働中に、新しいコード専用ProgramData releaseと権限を導入・検証します。
4. 停止intentを保存し、厳密に所有するGateway instanceだけを停止します。Ready、instance0、listener0を待ち、不明processをkillしません。
5. 全5DBを再検査し、制限付き専用の追加移行を1回実行します。以前の全業務行／表を検証し、元baselineから上限付き検索カウンター差を全証拠に分類します。
6. security／settings／principalを保持して、既知Gateway task actionだけを更新し、新定義bindingを保存します。
7. Run1回の前にBrain health、Proxy listener、保護identity、移行後DB baselineを再確認します。
8. Run試行を先に保存し、返却instance GUIDをPID／birth／health観測前に永続化します。新identityの採用は1回だけで、置換processを採用したりRunを再試行したりしません。
9. GET statusで正確なrelease commitとidle／基盤healthを要求します。DB／config／source／他サービスidentityを再確認し、SERVER_DEPLOYED_AWAITING_CLIENT_HOST_QUALIFICATIONを保存します。

busy、不明所有権、hash／schema差分、起動／health失敗、不完全receiptはHOLDとなり現状を保持します。Gatewayは停止したままの場合があります。旧runtime／設定／DBの自動復元や2回目の起動はありません。このscriptは推論、Slack投稿、元要求、Host認証迂回を送信しません。

<a id="gitmain-synchronization-and-cleanup"></a>
## Git／main同期と整理

Git checkout、staged payload、installed release、running processは別の状態です。pullが変えるのはcheckoutであり、pointerの昇格や稼働releaseの証明ではありません。配備receiptとstatusのrelease_commitが稼働版を示します。

GitHub mainマージは親が所有し、Server側所有者は正確なmerge SHAを受け取ってからローカルmainを同期します。既知の本番4ファイル差分が保存済み公開patchと一致し、統合候補に含まれることを確認し、未知差分があれば保留します。その後だけ、検査済みの逆patch適用でその正確な既知差分を解消し、親指定merge SHAへ通常のfast-forwardを行います。reset、force-push、未知差分の破棄はせず、installed source commit／treeと並べてmerge SHAを記録します。

新設定と必要機能の検証後、未使用と特定したfixture／旧release／worktree／backupだけを削除します。有効release／pointer、安定data／models／auth／dependencies、必要task、最終receipt、再現可能なGit source／手順を保持します。PREPARED中は整理せず、稼働コードやまだ必要なcomponent起動pathを削除しません。

<a id="qualification-limits"></a>
## 検証の限界

公開回帰／stub guardは使い捨てfixtureで論理、metadata、状態遷移、追加移行を検証します。native SYSTEM imageアクセス、ACL適用／task action更新、実Windows切替、Client Host認証capabilities、限定実モデル検証は別の実行工程です。Qwenのcontext32768／KVq4と既存Host限定TLS／restriction／foreground優先契約を維持します。

新しい固定release保守操作ではRELEASE-MAINTENANCE-FENCE.mdに従います。UAC待ちと全Scheduler操作はknowledge byte lockの外です。既存producer protocol双方と、全証拠付きの明示採用1回が必要で、暗黙baseline更新は禁止です。

移行commit後のtask登録確認で保留した場合はFORWARD-GATEWAY-START.mdに従います。Applyを再実行したり、TaskUpdated=falseから未登録と推定したりしません。別途レビューした操作で更新済みtaskを1回起動し、採用・移行は繰り返しません。
