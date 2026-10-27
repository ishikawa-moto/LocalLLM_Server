<a id="slack-execution-contract-v2-candidate"></a>
# Slack実行契約v2（候補）

この文書は隔離候補の説明であり、稼働中ServerPCの状態を示すものではありません。配備とmainマージには別途承認された操作が必要です。

<a id="deadline-and-bounds"></a>
## 期限と上限

候補は、観測済みの出力量対応期限修正を保持します。

    output_tokens = min(requested_max_tokens, 1024)
    input_allowance = 30 + 15 * ceil(max(0, UTF8_input_bytes - 2048) / 2048)
    output_allowance = 30 * ceil(output_tokens / 256)
    deadline_seconds = min(requested_timeout_seconds, 120,
                           max(input_allowance, output_allowance))

Chatの期限は5～120秒、入力は最大16,000 UTF-8 bytesです。Reviewは最大60秒の単一絶対期限内に2 passを行います。モデル出力上限1,024 token、並行数1、制限backgroundよりforegroundが高優先という条件を保持します。これらの上限は特定時間内の完了を保証しません。

<a id="attempt-identity-and-idempotency"></a>
## Attemptのidentityと冪等性

ServerPCに独立したAttempt識別子はありません。Hostのattemptはrequest_id、actionと完全要求のcanonical SHA256、session_id、operationへ結び付けます。Clientのattempt記録もそのbindingを保持する必要があります。同request_idで入力変更は競合です。新IDは新操作であり、transport再試行ではありません。

| 観測値 | 意味 | モデル自動再試行 |
|---|---|---|
| complete | 全結果を永続化済み。同一再送でその結果を返す | しない |
| interrupted | 中断結果を永続化済み。同一再送で安全なmetadataを返す | しない |
| running | 同じ予約済み要求がこのServer processへ登録されている | しない |
| unresolved | 対応する実所有者のない非終端予約、または永続化が不確実 | しない |
| deferred, retry_safe=true | このattemptによる永続予約は行われていない | 既存受付方針の下で、厳密に同じbindingの要求だけ |
| unknown from status | 対応する永続記録が見えない。attemptがなかった証明ではない | しない |

active／孤立予約を、再送やstatus照会だけで永続interruptionへ変換しません。旧保存済みinterrupted receiptは既存の最小metadataを保持し、過去の理由を創作しません。過去行を再分類しません。統合候補の追加retry_links表は別の回復契約に属し、既存行の意味は維持します。

終端receiptが置換できるのは、この操作自身のactive予約だけです。後着finalizerは終端済み結果を上書きできません。終端保存が失敗した場合は不確実な操作としてIDを保持しstatusを照会します。成功の捏造、予約削除、モデル再実行をしてはいけません。

<a id="cancellation-boundary"></a>
## 取消の境界

既存のsession bindingされたcancel要求形式は不変です。cancelled=trueは、この操作が終端確定処理へ入る前に取消を受理したACKです。transportの排出完了やinterrupted receiptの永続化済みを証明しません。Hostは終端結果またはunresolvedが確立するまで、binding済みstatusを観測します。

確定処理前の取消受理は、モデルlease解放後から終端保存前までを含め、complete結果の永続化を防ぎます。終端確定が始まった後の取消はcancelled=falseを返します。メモリー内登録は終端保存の成功または失敗まで保持します。

transport abortはサービス登録lockとrun別取消lockの外で行います。lease取消は最初の既知理由を保持します。explicit_cancelはcancel要求がServerPCへ到達したという意味で、人間のCancelクリックかClient timeout／shutdownからの送信かを特定しません。呼出し側の例外文、私有source、OCR、回答、任意reason本文をdiagnosticsへ出しません。

<a id="activity-observation"></a>
## 活動状態の観測

認証済みcontrol statusへschema localbrain.slack-activity.v1のrestricted_activityを追加し、件数だけを返します。

- scope: chat_review_apply
- available, stable
- active_requests, active_applications, nonterminal_runs
- quiescent, snapshot_only=true, admission_fenced=false

項目名と値は契約上の識別子のままです。対象範囲、利用可否・安定性、要求・適用・未終端run件数、および静止観測だけで受付フェンスではないことを示します。

件数の対象は、終端永続化まで登録中のchat／review、進行中apply、非終端の永続runです。件数が0へ戻っても、観測間の登録世代変更があればstableを無効にします。ストレージ欠落／安全性不明／lock中／読取不能なら、DBを初期化・移行せずavailable=false、quiescent=falseにします。

このsnapshotは保守leaseではなく、新要求を止めず、Client RAM queue、proposal／retirement書込み、全外部要求者も対象にしません。単独で本番停止・再起動の許可にしてはいけません。別途要求元／受付フェンスの確立と、新しいidentity／状態検査が必要です。

<a id="capability-negotiation"></a>
## Capability交渉

既存のHost限定認証境界は不変です。capabilitiesへ次を加えます。

- execution_contract: localbrain.slack-execution.v2
- nonterminal_states: running, unresolved
- cancel_ack_is_terminal: false
- attempt_identity: request_id+request_hash+session_id+operation
- quiescence_scope: chat_review_apply_snapshot_only

契約名、非終端状態、取消ACKが終端でないこと、attemptのbinding、静止観測の限定範囲を、上記の識別子で通知します。

既存privacy flagのexternal_ai、cloud_fallback、automatic_adoption、ordinary_secondbrain_adoptionはfalseを保持します。本番配備前にClientが新しい非終端応答を理解する必要があります。実Host認証capability観測とClient結合は別検証で、source確認や公開stubだけでは証明できません。

<a id="integration-with-bounded-recovery"></a>
## 上限付き回復との統合

この実行状態契約は、統合候補で`SLACK_RECOVERY.md`と組み合わせます。厳密なchat／review再送は別の生成を作りません。独立した`/v1/slack/retry`は`quiescent-attempt-v1`の下だけで許可します。直前attemptに永続化済みの適格な中断があり、active所有者がなく、推論semaphore下でidleな単一backend slotを確認し、一意の後続をpayload／hash／Session一致と総生成最大3回の条件でatomic予約する必要があります。取消ACKや活動snapshotだけでは回復証拠になりません。安全な応答所有socket取消実装を保持します。
