<a id="short-release-maintenance-fence"></a>
# リリース用の短時間保守フェンス

native UAC待機中の定期Librarian統計に対応する、独立した明示的保守操作です。通常の検索カウンター条件を広げたり、以前のbaselineを自動置換したりしません。親Clientの保守GOと、この新固定script／intentへの承認が必要で、以前のintent／GO／UAC receiptは再利用できません。

<a id="scope-and-order"></a>
## 範囲と順序

準備とInspectは、本番lock取得、サービス停止、新baseline採用を行いません。Inspectはtask／asset／identityの読み取り検証だけです。PrepareとApply事前検査も、完全なbefore／current観測を保持するpreviewだけです。既知metrics key集合、厳密schema／integer profile、duplicate_sources_avoidedとsearch_requests/search_hitsの単調増加、hits delta <= requests deltaを確認し、他の全カウンターと業務行・表集合・schema・version・整合性は厳密に一致させます。

正規Windows UAC後、Applyは固定asset／設定、正確なSYSTEM process image／command、Scheduler identity／PID／birth、保護listener、新しいServer idleとClient保守GOを確認します。manifest付きコードreleaseは短時間knowledge lockの外で導入し、その後次を行います。

1. 既存run_queue presence protocolに従い、保守process PID／時刻と固有tokenを使ってruntime/librarian.lockを排他的に新規作成します。古い・読めないものを含め既存ファイルがあればGateway停止前に拒否し、既存markerを削除・採用しません。
2. producer barrierを保持して、厳密に承認したGatewayを1回停止し、Scheduler Readyを待ちます。この待機と全Scheduler COM操作はknowledge byte lockの外です。配信済みLibrarian childも同じmarkerへ従い、既に稼働するchildが既存markerを所有していれば拒否します。
3. 既存1-byte runtime/knowledge-transaction.lockのbyte 0を、msvcrt.LK_NBLCKと同じWindows byte-range protocolで取得します。固定path・length・creation time・ACL・bytesを要求し、作成・truncate・置換・削除・runtime guard／recovery呼出しはしません。競合は2秒で拒否し、配備を繰り返し再試行しません。
4. 両フェンス所有中に、元SHA固定v2 snapshotからAdoptFencedMetricsを厳密に1回だけ実行します。全original/current/adopted記録と比較をCreateNewのfenced-adoption.jsonへ保存し、別のSHA固定database-fenced-baseline.jsonも作ります。以前のmetrics receiptを証明済み履歴と読み替えません。この明示採用で変化を認めるのは監査済み3カウンターだけで、他12カウンターと全業務記録は厳密一致です。helperは読み取り専用で、物理的所有は任意CLI tokenではなく呼出し側が検証します。
5. 同じbyte lock中に、制限付きretry_linksだけを追加・検証し、既存行とuser_versionを保持します。全比較は固定採用baselineと制限付き専用移行receiptを使います。
6. Scheduler更新やGatewayの1回起動より先に、必ずknowledge byte lockを解放します。Librarian markerはtask更新・起動・最終status／DB検証まで保持し、finallyで所有markerを解放します。成功は両解放後だけ保存します。
7. 以後の通常Checkは単一有効baselineへ固定し、検索以外13カウンターは不変、累積で0 <= hits delta <= requests delta <= 32を要求します。検査はbaselineを更新しません。正当でも新しい重複検出は保留し、未知key／type／schema／業務差分は全段階で保留します。

旧source／DB復元、モデル起動、実Slack投稿、元要求再送、不明processのkill、反復起動、自動rebaselineは含みません。

<a id="producer-coverage-and-limitations"></a>
## producerの範囲と限界

追跡producer一覧はMETRICS-DEPLOYMENT-CONTRACT.mdのままです。Search/context/findとenqueue/discover_raw/promote_fact/create_draft/process_candidatesは既存knowledge guardを使いますが、run_queue全体は使わず、抽出とlibrarian_failures更新はguard外でも起こります。そのため排他的Librarian presence barrierも必須で、knowledge byte lockだけでは不十分です。

配備が取得するのはOS byte lockだけです。取得時に中断applyを回復し得るBrain構築やknowledge_guard.guardは呼びません。既存producerは不変です。未知の直接SQL writerやprotocol外writerのすべてをフェンスするものではなく、観測された業務差分は全内容厳密比較で拒否します。5DBの読み取りsnapshotはDBごとに1 transactionで、DB横断atomic checkpointではありません。

byte-lock区間には20秒の協調予算があり、helperの前後境界で検査します。nativeディスク／process／SQLite呼出しを必ず強制中断する保証ではありません。導入、Scheduler COM、readiness待ち、network probeは区間外です。既存検索writerは既存guard timeout最大30秒まで待ち得るため、親保守確認でこの短時間の新規処理を止めます。超過時は保留してfinallyで解放し、コミット済み追加移行は前進調査用に保持します。

producer markerは短いbyte-lock区間だけでなく、上限付き停止／起動readiness待ちも含めて保持できます。CreateNew時から同じWindows file objectを所有し、write／delete共有を拒否します。解放にはhandleでdelete dispositionを設定し、close後に別ファイルをpath指定で消すことはしません。所有権やdelete disposition確認に失敗したら証拠を保持して保留し、部分作成失敗の後始末も元handleだけで行います。

<a id="public-qualification"></a>
## 公開試験による検証

Windows fixtureは隔離公開pathで実knowledge_guard OS protocolを双方向に使い、2秒busy拒否、例外後のproducer解放、検出／抽出なしの実run_queue locked応答、marker rename／置換拒否、不明既存marker保持、path指定の後始末禁止を試験します。Python fixtureは一度限りのreceipt内容、未知／業務／schema／counterの厳密拒否、元baseline保持、採用後の累積検査を扱います。PowerShell fixtureは停止前busy拒否、byte lock外の停止、単一採用、失敗／予算超過時finally解放、byte-lock解放後かつproducer barrier保持中だけのScheduler／起動を扱います。

これらはnative SYSTEM task操作、本番runtime結合、厳密なI/O期限を証明しません。正規native UAC前に新固定manifest／intentと新しい親保守GOのレビューが必要です。mainマージは親が所有します。
