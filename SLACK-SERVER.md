<a id="slack-server-candidate-contract"></a>
# Slack Server候補の契約

状態は、mainに対するレビュー準備が整ったServer側契約です。上限付きローカル起動経路は公開入力で検証し、承認済み導入環境ではChat承認入口と人間に結び付く適用の有効化も確認しました。Slack全体とモバイル承認の検証は別です。Socket Mode、OCR、永続ACK、Session／job保持、利用者管理、HTTPS／モバイル承認、Slackへの送信は独立Clientリポジトリが所有し、このServer変更で実装する機能ではありません。

knowledgeとRule双方の実際の公開fixture要求／応答ペアは`tests/fixtures/slack-contract.json`にあります。証明書・承認者・ownerはfixture用の値で、配備identityではありません。

<a id="configuration-and-identity"></a>
## 設定とidentity

本番設定や認証情報を開発worktreeへコピーしません。設定例2件は無効のプレースホルダーです。実際の`config/slack-server.json`、`config/slack-web.json`、`config/private-site.json`、key、runtime DBはGit対象外を維持します。

全Slack要求は既存Gateway mTLS listenerと許可IP検査に加え、正確な設定`client_address`、`host_owner`と一致する`X-LocalBrain-Approval-Owner`、`slack-server.json`にpin固定したWindows Hostクライアント証明書DERの小文字SHA256を要求します。汎用の信頼済み証明書やSlack member名では不十分です。配備済みprivate-site ownerを大文字小文字までそのまま使い、公開例のownerは既定値としてだけ扱います。設定欠落／不正は安全側の503、誤Host identityは403です。`enabled`が偽でもHost認証後のcapabilities照会は可能です。

`human_apply_enabled`の既定値はfalseです。Client Host認証、管理者登録、正確な提案表示、人間の明示操作、CSRF保護、receipt永続化を別途検証してから有効化します。Serverが信頼するのは認証済みHostの証言であり、receipt SHA256は整合性bindingで、独立した電子署名や人間が画面を見た証明ではありません。Slackメッセージが直接承認になることはありません。承認済み導入環境では現在このflagを有効にし、認証済みcapabilitiesを検証済みです。公開fixtureは承認契約を検証しますが、私有提案の実採用と物理的な別端末承認は別検証です。

<a id="common-exact-json-input"></a>
## 共通の厳密JSON入力

全POST要求は未知の最上位fieldを拒否し、次を必須とします。

```json
{"restriction":{"origin":"slack","dataClass":"slack_restricted","externalAi":"deny","cloudFallback":false}}
```

以下の全IDは`[A-Za-z0-9_-]{8,80}`に一致させます。IDは不透明な値で、channel名、Slack本文、利用者名、秘密情報ではありません。本文上限は65,536 bytesで、重複JSON key、非有限数、重複Content-Length、Transfer-Encodingを拒否します。応答はすべて非streaming JSONでno-storeです。利用者指定のmodel、URL、tools、functions、実行ファイル、cloud fallbackを受理しません。

`GET /v1/slack/capabilities`は`localbrain.slack-capabilities.v1`と実態に合うflagを返します。`automatic_adoption`、`ordinary_secondbrain_adoption`、`external_ai`、`cloud_fallback`はfalseです。`model_cold_start`はローカル設定の`enabled`と任意boolean `model_cold_start`が両方trueの場合だけtrueになります。省略時はfalseで、型不正や想定外設定fieldは安全側に拒否します。公開例では起動を無効にします。`rule_proposal`は`enabled`に従い、`rule_activation`は`enabled`と`human_apply_enabled`双方が必要です。既存の通常DecisionProtocolには安全なSlack Rule契約がないため、この候補で以下の限定契約を追加します。

<a id="chat-and-review"></a>
## Chatとreview

`POST /v1/slack/chat`の厳密なfield：

```json
{"request_id":"request_fixture_0001","session_id":"session_fixture_01","context":"","prompt":"Public fixture question","max_tokens":1024,"timeout_seconds":30,"restriction":{"origin":"slack","dataClass":"slack_restricted","externalAi":"deny","cloudFallback":false}}
```

`POST /v1/slack/review`は`context`と`prompt`を単一文字列`source`に置き換え、他のfieldは同一です。`max_tokens`は整数64..2048で、1024へ制限します。`timeout_seconds`は整数5..120で、chatは30秒、reviewは合計60秒へ制限します。chat結合入力またはreview sourceは最大16,000 UTF8 bytesです。最初のreview出力を8,000 bytes以内にして、新しい2回目promptと出力予約を不変の32K context内へ収めます。context／KV／model設定は変えません。

Chat成功：`{"status":"complete","text":"...","classification":{...}}`。

Reviewはローカル呼出しを新規に2回、各user prompt1件で行います。1回目は`{kind:"knowledge",body,evidence}`または`{kind:"rule",body,evidence,delta}`を提案し、2回目はsourceと候補を独立に検査して`{pass:boolean,evidence,issues:string[]}`を返します。evidenceは出典の逐語引用が必須です。出力不正、根拠不一致、issues、安全でないRule、判定失敗は`candidate:null`にします。モデル判定は人間承認や事実の正しさの証明ではありません。

Review成功時のkeyは厳密に`status`、`candidate`、`review`、`review_hash`です。`candidate`はnull、`{kind:"knowledge",body,evidence,review:{second,source_sha256}}`、または`kind:"rule"`と`delta`を持つ同形式です。Rule deltaは`locale`（`ja`または`en`）と`style`（`brief`または`normal`）の空でない部分集合だけで、他key／値は拒否します。`review`は`{schema:"localbrain.slack-review.v1",request_id,session_id,source_sha256,passes:[string,string],candidate,classification}`です。実ローカル評価のraw出力2件を制限付きストアへ保持し、`review_hash`はこの全review objectのcanonical SHA256です。元sourceはClientが所有・保持し、ServerはUTF8 SHA256を結び付けます。

実foreground推論semaphoreと受付conditionを共有し、優先度はforeground > Slack chat > Slack reviewです。検証済みHost認証chatは、ローカルopt-inが有効で基盤がreadyのとき、非同期の通常モデル起動を1件要求できます。同時要求はSTARTINGを共有し、foregroundが優先されます。review／status／capabilitiesは起動要求をせず、起動無効なら停止モデルを停止のまま保ちます。busy／未readyはRequest ID予約や推論なしで`{"status":"deferred","retry_safe":true}`を返します。この明示的な推論前応答だけがready後の同一要求再試行を許し、不確実要求は無条件に再試行しません。background起動失敗はFAILEDとなり、chatに`local_model_start_failed`を返し、自動再試行やモデル停止はしません。Slackはforegroundを越えてqueueを作らず推論を重ねません。review中のchatはreviewを取り消し、解放まで見送ります。foreground受付はbackground leaseを取り消して元loopback socketをshutdownし、HTTPResponseが接続socketをdetachした場合も対象です。受付にbusy確認／POST間の競合は使いません。

予約後のtimeout、優先処理による中断、取消、transport失敗、不確実な再起動では`{"status":"interrupted","retry_safe":false}`を返し、部分本文を破棄します。そのIDを自動再実行してはいけません。意図した再試行は新IDの別記録attemptとし、オフラインSlack backlogを再送しません。完了済み同一要求は永続結果を返し、同IDで内容変更は拒否します。HTTP errorには安定したerror codeだけを含め、source／model例外本文やpathは出しません。

`POST /v1/slack/cancel`の入力は厳密に`{request_id,session_id,restriction}`、応答は`{cancelled:boolean}`です。認証済みHostがSessionを所有し、Serverもactive Session一致を検査します。2回のreview passの間も取消可能です。通常の`/v1/control/job-status`にSlack識別子を公開しません。

<a id="durable-status-no-execution"></a>
## 永続status（実行なし）

`POST /v1/slack/status`の厳密入力は`{operation,request_id,session_id,request_hash,restriction}`で、operationは`chat`、`review`、`apply`です。ID文法は共通、request_hashは小文字64桁hexです。chat/reviewはcanonical `{action:operation,value:the_original_request_object}`をhash化し、applyはreceipt_hashとrestrictionを含む元apply要求全体をhash化します。Hostの変更不可の永続予約にある値と一致させます。

不明なら`{state:"unknown",retry_safe:false}`を返しますが、並行／不確実要求が一度も受理されなかった証明ではありません。完了runは`{state:"complete",request_hash,result,retry_safe:false}`、atomic適用receiptは`{state:"applied",request_hash,result,retry_safe:false}`です。active／中断／再起動不確実runは`{state:"running"|"interrupted"|"unresolved",request_hash,retry_safe:false}`を返します。Session／hash不一致は`request_binding_mismatch`（409）です。statusはモデル呼出しや適用をしません。不確実なapplyを自動再送せず、厳密statusを照合するかHOLDを維持します。

<a id="immutable-restricted-proposal"></a>
## 変更不可の制限付き提案

`POST /v1/slack/proposal`の入力は厳密に`{proposal_id,review_request_id,session_id,restriction}`です。同Sessionの完了済み非null Server reviewを必要とし、Clientが候補内容を差し替えることはできません。`{status:"proposed_restricted",proposal_hash,payload}`を返します。payloadの厳密fieldは次のとおりです。

```text
schema = localbrain.slack-proposal.v1
proposal_id, session_id, review_hash, candidate, classification, target, base_hash, new_hash
```

knowledgeの`target`は`restricted-knowledge`、`base_hash`は空文字列、`new_hash`は候補hashです。Ruleの`target`は厳密に`slack-response-policy`で、`rule_diff:{before,delta,after}`を含みます。before/afterは厳密な`{locale,style}`で、既定は`{locale:"ja",style:"normal"}`です。base/new hashは全before/after Rule object `{schema:"localbrain.slack-rule.v1",target:"slack-response-policy",values:{locale,style},classification}`へ結び付けます。空の変更は拒否し、後で別採用があっても同一提案の再要求は元の変更不可baseを返します。

proposal hashはcanonical payload JSONのSHA256です。同一提案の反復は冪等で、別内容へのID再利用は拒否します。Source SHA256、実評価2件、厳密Rule target／diff／base／new hash、classificationをcandidate／review／proposal hash経由で保持します。

<a id="human-only-restricted-adoption"></a>
## 人間だけが承認する制限付き採用

`POST /v1/slack/apply`の入力は厳密に`{request_id,proposal_id,proposal_hash,session_id,approval,restriction}`です。`human_apply_enabled`がtrueでない限り無効です。`approval`のfieldは次だけです。

```json
{
  "schema":"localbrain.slack-host-approval.v1",
  "approval_id":"approval_fixture_01",
  "nonce":"nonce_fixture_0001",
  "approver_id":"admin_fixture_001",
  "action":"accept_restricted",
  "proposal_id":"proposal_fixture_01",
  "proposal_hash":"__CANONICAL_PROPOSAL_SHA256__",
  "review_hash":"__CANONICAL_REVIEW_SHA256__",
  "session_id":"session_fixture_01",
  "target":"__EXACT_PROPOSAL_TARGET__",
  "base_hash":"__EXACT_PROPOSAL_BASE_HASH__",
  "new_hash":"__EXACT_PROPOSAL_NEW_HASH__",
  "issued_at":0,
  "expires_at":0,
  "host_owner":"ClientPC-windows-host",
  "host_certificate_sha256":"__PINNED_HOST_CERTIFICATE_SHA256__",
  "receipt_hash":"__CANONICAL_RECEIPT_SHA256__"
}
```

時刻は整数Unix秒で、`issued_at <= now < expires_at`、有効期間1..600秒です。`receipt_hash`は`receipt_hash`自身を除くapproval objectのhashです。全hashはUTF8 JSON、key整列、compact区切り、ASCII escapeなし、NaNなしで計算します。HostはUnicode escapeを含めcanonical bytesが一致する実装を使い、receipt発行前に正確な提案を表示・永続化します。ID、proposal／review hash、target、base/new hash、Session、action、Host owner／証明書、管理者、期限、nonceを結び付けます。Clientは承認前に厳密targetとRuleのbefore/after差分を表示します。公開vector `tests/fixtures/slack-canonical.json`は日本語、補助Unicode、改行、記号を含み、Host正規化を検査します。外側Request IDも永続application hashで結び付けます。

approval ID、nonce、外側Request ID、proposalごと1行のaccepted記録をSQLiteでatomicに消費します。Sessionがopenなら、同一apply再試行は期限後も元結果を返し、変更再送は拒否します。退役後はapply再送ではなくstatusで保持済みapplicationを照合します。成功は`{status:"applied_restricted",proposal_id,proposal_hash,target,new_hash,classification}`であり、**通常SecondBrainへの採用ではありません**。制限付きストアだけへ書き、Ruleは同じSQLite transaction内で列挙済みSlack chat policyだけを変更します。write transaction内でcurrent base hashを検査し、古い並行提案は承認を消費せず拒否します。以後のSlack chatはlocale/styleをsystem promptに使用します。通常Actor prompt、Continue規則、任意file、Git／shell／管理者操作、security、権限、認証は不変です。採用中にLLMを呼びません。

<a id="restricted-data-boundary"></a>
## 制限付きデータの境界

Server状態はGit対象外の`runtime/slack-restricted/state.sqlite3`だけに置きます（runs、proposals、approval receipt、accepted制限項目、列挙Slack rule state）。symlink／reparse storage pathを拒否します。通常SecondBrain、Chat履歴、embedding、TaskMemory、通常source control／exportへ挿入しません。原本と派生データには`origin=slack`、`data_class=slack_restricted`、`external_ai=deny`、`cloud_fallback=false`を付けます。制限routeのaccess logは抑制し、通常lifecycle telemetryには一般的background roleだけを出し、Slack Request／Session／source IDは出しません。

通常Gateway proxy経路は、ネストしたclassificationや`X-LocalBrain-Data-Class: slack_restricted`を含む明示Slack restrictionを拒否します。信頼されたHostが分類を除いた後はServerに推定できません。Client RestrictedExecutionの送信先制御は必須で、共同検証が残っています。これはOS sandboxや管理者による制限DBのコピーを防ぐ保証ではありません。PC全体／ローカルbackupは私有として扱い、runtimeやコピーしたsource／receipt本文をreview packetへuploadしません。Host通知のlogical Session退役は以下に実装します。Server自身が30日／7日保持方針やschedulerを実行することはありません。corpus読取／検索と両側の保持／outbox検証は残件で、本番利用はHOLDです。

<a id="host-notified-session-retirement"></a>
## Host通知によるSession退役

`POST /v1/slack/retire`は同じ有効化guard、厳密Host IP／owner／pin固定mTLS証明書、framing制限、restriction方針を使います。入力は厳密に`{request_id,session_id,restriction}`で、IDは共通の不透明8..80文字文法です。reason、source、path、age、model optionsは受け取りません。capabilitiesの`session_retirement`は`enabled`に従います。

Windows Hostは30日／7日規則の適用後、自身のSession削除と非PII退役outboxを1 transactionでcommitします。その前にsending／uncertain／unresolved applicationを持つSessionを保護します。この3-field要求はHostの私有の不確実状態を伝えられないため、Serverは独立推定やHostの保持規則適用の正しさを保証しません。応答紛失時は同じoutbox IDを永続化して再試行し、削除したSlack履歴を再構成してはいけません。

厳密な成功応答は`{status:"retired",session_id,request_id,adopted_knowledge_preserved:true}`、busyは`{status:"deferred",retry_safe:true}`です。Sessionのactive実行（受付から予約前までを含む）、ローカルapplication実行中、未完の永続run、SQLite writer競合は、job取消・モデル起動・他者lock解放なしで見送ります。再起動不確実なactive runは別途照合まで保護し、timeoutによる強制整理はしません。

単一SQLite transactionで、そのSessionの未採用提案、chat結果、未採用review結果（保存済み評価文を含む）を削除します。accepted制限knowledge／Rule、現行Slack Rule state、approval／nonce／application receipt、採用済みproposal、各accepted記録へ結び付く全2-pass review auditはすべて保持し、他Sessionは変更しません。必須採用auditが不整合なら`retirement_integrity`（409）で拒否し、部分失敗ではpayload削除とtombstoneを一緒にrollbackします。退役後も元Session／request hashでapplied status receiptを読めます。

退役後は不透明Sessionと削除したrequest／proposal IDのtombstone、正確なmetadata限定退役応答を保持します。同retirement ID／Sessionは同じ応答を返し、別Sessionへそのretirement IDを再利用すると`retirement_id_conflict`（409）です。退役済みSessionへの新retirement IDは新IDに対する冪等metadata限定ACKになります。source内容をaccess／audit logやGitへ出しません。

退役Sessionへの遅延chat／review／proposal／applyは、既存認証／有効化検査に従ったうえで`session_retired`（409）を返します。削除済みrequest／proposal IDは新Sessionに結び直せません（`request_retired`／`proposal_retired`、409）。期限切れSlack threadの新しい世代には新Session IDと新request／proposal IDが必要です。tombstoneは経年削除もexportもせず、通常model statusにも公開しません。

削除操作ではSQLite `secure_delete`を有効にします。削除cellをゼロ化するlogical退役であり、旧WAL frame、外部backup、process memory、全file systemからの法科学的消去を主張しません。VACUUM、強制checkpoint、任意file削除、モデル呼出し、サービス操作は行いません。公開fixtureは通常rollback-journal modeで削除内容のゼロ化を試験し、WAL／backupと両側の本番退役は未検証です。tombstoneを無視するSlack有効Server旧版へ戻さず、Slackを無効化するか互換guardを維持し、古いDB復元で退役Sessionを復活させてはいけません。

厳密な公開入出力例は`tests/fixtures/slack-retire.json`を参照してください。

<a id="chat-menu-only"></a>
## Chatのメニューだけ

`GET /api/slack-approval-entry`は別設定`slack-web.json`から`{enabled:false}`または`{enabled:true,url}`を返します。URLはHTTPS、private-siteの厳密Client host、path `/slack/approvals`で、認証情報、query、fragment、escape、redirectを認めません。メニューは直接のno-referrer/noopenerリンクを使い、Serverが承認・cookie・token・管理者操作を代理送信しません。既存のLAN共有Chat履歴は不変です。

承認済み端末手順でClientのHTTPS信頼chainを導入し、証明書／名前を検証して、各携帯端末に直接HTTPS承認URLをbookmarkします。現行Clientページはブラウザークライアント証明書ではなく管理者パスワードログインを使い、内部Host mTLSは不変です。証明書errorを迂回しません。旧HTTP Chatは便利なメニューであり承認の信頼根拠ではないため、承認には検証済みHTTPS bookmarkを使います。

<a id="validation-and-deployment-boundary"></a>
## 試験と配備の境界

`python -B tests/run_public_tests.py`を実行します。Git対象外の一時的な**公開fixture**環境設定を生成し、実設定があれば拒否し、親試験の接続先を試験作成のループバックlistenerへ限定して、自分の生成設定を削除します。通常試験は一時Brain rootと一時公開HTTP child server1件を使い、本番設定／key／modelはコピーしません。TLS Host identityは証明書adapter付きHTTP handlerで試験します。承認済み導入環境では加えて実Host mTLS／capability、HTTPS password gate、Chat入口、上限付き公開Slack／起動probe、保存結果のtext添付返送1件が成功しており、`SLACK-VALIDATION.md`と対応Client検証で説明します。広範な本番throughput、物理モバイル承認、私有提案実採用、配備後保持／回復、WAL／backupを含む物理消去、一般的なend-to-end再試行／配達は未検証です。追加の退役専用公開試験は`python -B tests/run_public_tests.py test_slack_retire.py`で、以前の109件を再実行するものではありません。

SSE／起動差分と固定承認入口／適用展開の限定的な独立読み取りreviewでは指摘なしでしたが、包括的securityやモデル品質の保証ではありません。実装範囲はreview準備済みで、残る検証は`SLACK-VALIDATION.md`に列挙します。各環境には厳密な承認、既知source／config preimage、保守静止確認、範囲限定の通常再起動、review済みrollbackが引き続き必要です。公開例は無効のまま保ち、private設定の有効化はローカル運用者だけが決めます。reviewやmergeでは、配備、登録、モデル要求、知識適用を実行しません。
