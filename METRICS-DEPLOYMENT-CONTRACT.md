<a id="strict-search-counter-classification-during-gateway-release"></a>
# Gatewayリリース時の検索カウンターの厳密な分類

通常のindex統計更新をすべて配備側が拒否する問題を修正します。Brain、Gateway、モデル、認証、業務schema、要求受付は変更しません。配備を実行せず、以前のApply／intent／GO承認も再利用しません。

<a id="current-source-and-exact-sqlite-contract"></a>
## 現行ソースと厳密なSQLite契約

対象表はruntime/index.sqlite3に属し、Brain.__init__は次を宣言します。

    CREATE TABLE metrics (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0)

この表に属するschema objectは表本体とsqlite_autoindex_metrics_1だけです。classifierは正規化した厳密なDDL、table_xinfo（hidden列と既定値を含む）、非partialな暗黙unique PK index1件とBINARY key／rowid metadata、FK・trigger・追加indexがないことを検査します。このschema自体はTEXT PKの非NULLや整数だけの保存を強制しないため、classifier側で既知TEXT key、一意key、0～2^63-1の非負SQLite INTEGER値を別途要求します。

追跡ソースでSQL値を書き込むのはsrc/localbrain.pyのBrain.metricだけで、int(amount)を加えるUPSERTを実行します。ただしこの汎用メソッド自体は制限APIではなく、将来の直接呼出しやSQL writerは別値を渡せます。リリース判定は表名を信頼するのではなく、未知key、不正storage type、想定外内容を拒否します。

追跡済みproducer一覧（すべてのsrc/*.pyに対してASTで検証）：

| key | 追跡済みproducerと意味 |
| --- | --- |
| search_requests, search_hits | Brain.search：有効な検索完了後、requestを1加算し、int(bool(final_results))をhitへ加算 |
| duplicate_sources_avoided | KnowledgePipeline.enqueue：source hashが既にキューにある場合 |
| fact_promotions_blocked | promote_fact：秘密情報を含む出典を拒否 |
| contradictions_detected | promote_fact：有効claimとの矛盾を検出 |
| fact_promotions | promote_fact：昇格経路を完了 |
| drafts_blocked_for_secret | create_draft：秘密情報を含む候補を拒否 |
| fact_drafts, conflict_drafts, fact-review_drafts, synthesis_drafts, decision_drafts | create_draft：対応下書きの保存／commit後にkind + "_drafts"を加算。有限のkindは追跡済み呼出し箇所に由来 |
| synthesis_rejections | process_candidates：適格な現行出典2件が不足 |
| decision_rejections | process_candidates：原文どおりの意思決定証拠が不足 |
| librarian_failures | _run_queue_locked：項目の処理失敗を捕捉 |

既知keyは15個ですが、未作成のkeyも許されます。比較では両側のkey集合を完全一致させ、新たな既知keyの追加もHOLDです。検索以外の13カウンターはすべて同一でなければなりません。

Brain.contextとfind_entity/find_concept/find_synthesisはsearchを呼び、検索の2カウンター更新を引き継ぎます。Brain HTTPの/search、/context、find経路はrole認証後にこれらへ振り分けます。公開fixtureでは/health（Brain.status）、Gateway /health、/v1/control/status、control heartbeatはmetricsを更新しません。Gateway heartbeatはメモリー内時刻を更新し、Brain statusはmetrics／vector件数とファイル一覧を読みます。検索には中断apply回復を含む既存knowledge transaction guardがあるため、任意の検索が他の業務回復を起こさないとまでは主張しません。観測されたmetrics以外のDB内容差は引き続き拒否します。

<a id="full-evidence-and-narrow-predicate"></a>
## 全証拠と限定した判定条件

各snapshotは全5DB、全SQLite表（sqlite_sequenceとshadow tableを含む）、型付き全行digest、schema digest、user_version、整合性を保持します。DBごとに読み取り専用transactionを1つ使いますが、DB横断のatomic checkpointではありません。

indexだけは、同一transactionで取得した同一metrics行bufferから、検証済みkey／integer profileも作成します。profileを完全な型付きtable digestへ暗号学的に照合します。不正profileに含む未知key名／値やSQLは出力しませんが、全型付きtable digestは保存します。

比較で認めるのは次の条件すべてです。

- index以外の全DB記録が厳密に一致すること。ただし明示検証した制限付き移行結果だけを除く。
- indexのschema／version／table集合と、metrics以外の全表の件数・全型付きdigestが厳密に一致すること。
- metricsの既知key集合と有効INTEGER値が一致条件を満たし、検索以外の13値が不変であること。
- 両状態でhits <= requestsであること。
- 0 <= hits_delta <= requests_delta <= 32であること。

32は操作ごとに固定した分類上限で、rate limit、モデル設定、変更可能な緩和値ではありません。移行後を含む全検査を元baselineへ固定し、検査成功でbaselineを更新しません。累積上限を超えた追加の有効要求もHOLDになります。

これは内容が上限付き検索ペアと整合することを示しますが、writerの認証、保守／受付フェンスの確立、更新原因となる要求の証明はしません。hashが異なる場合も完全なbefore／current snapshotと明示delta分類を保持します。未知schema／key／type、非検索更新、業務差分、減算、上限超過、不整合なペアは必ずHOLDです。

既存writerはrequestとhitを2 transactionで更新します。その間にbaselineを採ると、正当な活動でも後でhits_delta > requests_deltaとなりHOLDし得ます。自動baseline更新でこの曖昧さを隠しません。

<a id="legacy-evidence-without-unconditional-capture"></a>
## 無条件の採取に頼らない旧証拠

以前のv1 snapshotは完全な型付きmetrics table digestを含みますが、個別カウンターprofileはありません。不透明なhashをそのまま無害と読み替えることはできません。

UpgradeLegacyは独立した明示的な読み取り専用の証明です。元v1 bytesを変えず、v2観測を採取し、まず旧metrics以外の全行／schema／version／整合性／件数一致を要求します。現行の完全な既知profileを使い、0 <= hits_delta <= requests_delta <= 32を満たす逆向きペア561通りだけを列挙します。旧記録digestを再現する以前の完全な型付きmetrics digestが厳密に1つ必要です。不一致、複数解、未知profile、非検索差分、業務差分は探索範囲を広げず安全側に拒否します。

結果には元の集約binding、algorithm version、観測した全v2 snapshot、別に導出したv2 baseline、正確な分類／deltaを保持します。旧型付きdigestと整合する内容を再構成するだけで、元の更新者が検索要求だったとは主張しません。準備は別途固定したv2 baselineだけを受理し、新しい全観測／比較を保存します。UpgradeLegacyの自動実行やbaseline自動置換はしません。

<a id="explicit-adoption-of-audited-operational-statistics"></a>
## 監査済み運用統計の明示採用

上限付き旧内容証明が失敗したらUpgradeLegacyは失敗のままとし、検索範囲を広げません。別の運用者承認済みAdoptLegacyMetricsモードだけが新しい運用baselineを確立できます。旧snapshot自身のdigest／整合性、旧metrics以外の全DB／表／schema／version／件数証拠、現行metrics表の厳密schema・既知key集合・INTEGER値を検証し、空でない判断参照を要求します。これは意図した新操作で、UpgradeLegacy／Check／Prepare／Applyの再試行や暗黙fallbackではありません。

独立した採用receiptは、旧完全snapshotと新完全観測を保持し、過去のカウンター値とproducer identityを未証明と明記し、全業務記録一致と採用したv2 baseline名を記録します。旧値や原因が無害／不変だったとは主張しません。旧DBやmetrics行を書換え・削除せず、現行key／type／schema不明や旧業務差分があれば拒否します。

新intentの前に、旧v1／新v2 bytes、型付きdigest、明示判断参照、receiptをそれぞれSHA固定します。将来の検査も同じ厳密検索ペア条件でその新固定baselineへ結び付けます。成功による別baselineの採用はなく、以前のApply／intent／GO承認を新intentへ流用できません。


<a id="apply-integration-and-alternatives"></a>
## Applyとの統合と代替案

新保守releaseでは、Prepareとフェンス前Applyは全証拠付きCheckBusiness previewだけを行います。既存の上限付き検索classifierは不変です。別途明示したAdoptFencedMetricsだけがnative UAC後、物理的に所有した両producer protocolの下で1回実行できます。限定した3カウンター条件、業務内容の厳密一致、順序、限界はRELEASE-MAINTENANCE-FENCE.mdに定めます。

有効baselineは検証済みの同一buffer bytesから、新しいCreateNew artifactへ別途SHA確認して保存します。採用後Checkは移行後を含め更新しません。制限付きストアの期待記録を置き換えられるのはSHA固定・検証済みmigration.afterだけで、indexカウンターは常にその単一の有効baselineへ固定します。拒否前にも全比較結果を保持し、HOLDはphaseと固定公開失敗コードを保存して、任意／私有例外本文を抑制します。

完全な普遍的受付フェンスには、全writerの協調が必要です。この操作は、短いクリティカルセクションで既存の追跡済みproducer protocol2つだけを使います。導入、native UAC待ち、Scheduler操作、ネットワークhealth検査中にknowledge byte lockを保持しません。協調予算と既存検索timeoutへの影響を明示します。

過去metricsを削除せず、表を除外せず、同件数を内容一致とみなしません。将来schema／policyを拡張する際は、新source、fixture、hash、承認が必要です。

<a id="qualification-boundary"></a>
## 検証の境界

公開fixtureは、実Brain search/context/find関数と隔離した認証済みBrain HTTP server、認証／network／resourceをfixtureにした実Gateway status／heartbeat handler、全表比較、敵対的schema／key／type／業務差分、上限付き旧証明、Apply状態guardを実行します。本番設定、モデル、Slack投稿、元要求は使いません。

native ACL／task操作と本番applyは別工程です。旧b90 script、intent3452、実行receipt、期限切れGOはimmutableのまま保持します。新しいnative UACの前に新commit、release manifest、intent、reviewが必要で、GitHub mainマージは親が所有します。

<a id="separate-short-maintenance-operation"></a>
## 独立した短時間の保守操作

RELEASE-MAINTENANCE-FENCE.mdは、既存producer protocol2つの下で別途明示承認された運用統計の一度限りの採用を定めます。新release候補に限り、前述のフェンスなしApply／Prepare統合に優先します。通常の検索2key／32条件、旧失敗証拠、immutableな旧baselineは不変です。フェンス前previewは採用せず、フェンス後は単一のSHA固定有効baselineを使います。
