<a id="browser-chat-ocr-isolated-candidate"></a>
# ブラウザーChat OCR（隔離候補）

本番は変更していません。この候補の対象はServer main `dbe7343`とClient PR15のコミット`c587a92a1649b1a610723ebcecca93b033bfd4a2`です。

<a id="audited-authentication-boundary"></a>
## 監査した認証境界

Server Chatのクライアント証明書と、既存のpin固定されたClient Host証明書は同じDERフィンガープリントです。そのため証明書だけでは役割を分離できません。実TLSピア証明書とsocketの接続元IPを組み合わせる境界を採用します。受付・状態照会・取消・ダウンロードはループバックと既存ローカル証明書pinを要求し、workerメソッドは設定済みClient IP、既存Host pin、ownerを要求します。転送ヘッダーや本文のorigin主張は役割を与えません。Serverのローカル秘密鍵を利用できるプログラムは、このローカルOS信頼境界の内側です。ローカルプログラム間を隔離するものではなく、新しい証明書・CA・ポート・秘密情報も導入しません。

監査時のGateway pointerはimmutable release `dcc046a`を指し、本番Git checkoutは`dbe7343`でcleanでした。設定されたChatポートにはPython listenerがありますが、この監査の権限では実行ファイル／コマンド情報を取得できず、ChatのScheduled Taskも見えません。これらは稼働Chatのソースや起動identityを証明しないため、listenerだけからソースを推定せず、配備時に別途検証します。

<a id="contract-and-invariants"></a>
## 契約と不変条件

worker契約の正本はClient PR15の`CHAT_IMAGE_OCR.md`と`dotnet/LocalBrain.ClientHost/ChatOcrRelay.cs`です。workerの`next`はpoll schemaだけを受理し、`job`だけを返します。jobの項目はschema/job_id/conversation_id/input_sha256/extension/formats/bytes/cancel_requestedに厳密に限定します。入力は最大4MiB、next応答は最大6MiBです。作業用の原本bytesを削除する前に、進捗登録のACKが必要です。HTTPで配達しただけでは登録になりません。

worker reportは最大16MiB、artifactは各最大1MiBです。Markdownは必須、table JSONは自動、HTML/CSVは明示選択が必要です。canonical OCR Job IDはSHA256（`chat_`＋mailbox Job ID）の先頭32桁の16進数であり、mailbox Job IDそのものではありません。結果のsource hash、origin、ローカル限定方針、会話へのbindingを再検証します。busy状態のpaused reportはページ数を省略できますが、登録ACKにはなりません。queued/running/pausedの循環は有効ですが、完了ページ数の後退は禁止です。

Serverのページ契約は、既存Clientの上限と同じ結果1～200ページ、進捗0～200ページです。単一の`chat_ocr.MAX_PAGES`で両方の検査と公開`/api/ocr/config` capabilityを管理し、UIは独自上限ではなくその値を表示します。以前のServerだけの25ページ制限では有効な26ページ登録を拒否して同じ入力を繰り返し配達していたため、これを修正します。200を超える進捗は登録前に拒否し、200を超える不正な終端結果は公開せず、既存の`OCR_INVALID_RESULT`失敗にします。source/response/report/artifactのbyte上限、登録ACK、取消・期限、worker資源・設定は変更しません。200ページでもbyte・時間・artifact上限を超え得るため、OCR完了やモデル文脈への適合を保証しません。

要求は安定したIDと変更不可bindingを保持します。終端reportの再送は同一内容でなければならず、競合reportで受理済み結果を置き換えません。取消・期限切れ・削除があれば、古いcomplete reportが後着しても公開しません。取消ACKはworker停止の証明ではありません。割当て済みで未完の取消はworker終端ACKまで配達可能に保ちます。tombstoneを再利用したり、期限切れIDを受け入れるため追い出したりしません。

割当て済みでServer所有のcancel flagが立つjobの`cancelling`更新は、実Clientの取消待ち形式に合わせてページ数を省略できます。これは待機の確認だけで、登録・カウンター・`worker_done`は変えず、割当てを未完のまま保持し、終端ACKだけが解放します。Serverのcancel flagがなければ、ページ数なしの`cancelling`は拒否します。ページ数なしの`queued`／`running`／`expiring`、片方だけのページ数、誤binding、呼出し側のcancel主張は引き続き不正です。既存のbusy状態を示すページ数なし`paused`形式は変わりません。

OCRだけの処理は、Qwenを起動し得る既存の生成キューを経由しません。明示的な解釈は、実テンプレートのtoken計数と信頼されないOCR本文の区切りを使い、既存ローカルQwen経路を利用します。ツール、Internet検索、cloud fallback、SecondBrainへの自動採用は追加しません。LAN共有の会話所有権は意図した仕様のままです。

<a id="configuration-and-retention"></a>
## 設定と保持

この機能は既定では未設定／無効です。`config/chat-ocr.example.json`に、Git対象外の任意設定`config/chat-ocr.json`を説明し、有効な設定をコミットしたり本番に作成したりしません。後で有効にする場合はServerと検証済みClient relayを一緒に配備します。既存TLS検証、IP許可リスト、Client pin、ownerを維持します。ブラウザー要求は既存LAN/Host/Origin/要求ヘッダー検査を使い、信頼されるChat受付がsource hashを計算するので、LAN HTTPでブラウザーWebCryptoを必要としません。既存のLAN共有Chatへブラウザー～Server間TLSやユーザー別認証を追加するものではありません。

独立してGit対象外の`runtime/chat-ocr/` spoolには、稼働／未完job最大8件、総ファイル容量256MiB、永続metadata／tombstone最大10000件の上限があります。到達時は安全側で受付を拒否します。入力・jobの期限は2時間、出力は完了から24時間です。要求時に期限検査を行い、有効なGatewayは60秒ごとに保守走査します。完了／期限切れ／削除IDはtombstoneを残して再受付しません。UIは会話ごとの最新64 OCR jobを表示し、artifactは期限まで明示指定で取得できます。停止中のGatewayは走査できないため、次の起動／要求で内容を返す前に期限を適用します。

入力bytesはファイルとして保持し、SQLite本文に入れません。worker登録のコミットまたは終端／取消／期限を受けて原本を削除し、report公開とmetadataコミットの間のクラッシュは同一report再送で回復できます。期限削除はspool所有の生成済み入力／report名と孤立したstaging名だけを対象にします。不明ファイルは保持して容量に算入します。これはアプリファイルの削除であり、ストレージのバックアップから物理的に安全消去したという主張ではありません。binding済みの不正終端結果は可視の`OCR_INVALID_RESULT`失敗にし、不正結果の公開や反復配達を防ぎます。会話削除は先にOCR出力を削除してtombstoneを保持し、未決着workerがあれば削除を保留します。

OCRだけなら生成キューを呼びません。明示分析はローカルQwenを使い、実テンプレート／token数でoverflowを拒否します。文書本文は信頼されないJSON区切りデータとして扱い、ツールや知識採用を行いません。OCR結果カードはテキスト履歴とは別に永続化し、再読込み時に共有会話IDで復元します。ブラウザーsession storageへ保存するのはそのIDだけで、入力bytesやOCR本文は保存しません。HTML/CSVは添付ダウンロードのままにし、プレビューはraw HTMLではなくtext nodeを使います。

<a id="qualification-and-limits"></a>
## 検証と限界

前進配備、既存履歴の保護、起動時受付ガード、残る本番検証範囲は[CHAT_OCR_DEPLOYMENT.md](CHAT_OCR_DEPLOYMENT.md)で説明します。

公開試験は、認証拒否、framing/CSRF、再起動／ACK紛失／冪等性、進捗の単調性、取消／期限、原本削除、report公開時クラッシュ回復、保持／容量、不正結果失敗、artifact hash／形式、会話削除、OCRだけのモデル呼出し0件、明示分析の予算を扱います。既存の公開回帰も実行します。隔離ブラウザーfixtureではPNG選択・upload、paused状態の再読込み、完了、自動Markdown/table JSON、script風文字列のテキスト限定表示、Markdown downloadを検証しました。偽のOCR／モデルtransportを使うため、OCR精度や性能は測定しません。

実Client／GatewayのmTLS相互運用、実OCR／モデル実行、最終配備Chatのsource identity、Client／Server一体の展開は未検証です。稼働中の共有Chat／Gatewayの切替・停止・再起動はしていません。この作業で私有画像、OCR本文、新規認証情報、本番DB移行、実Chat／Slack投稿、モデル要求は使用していません。
