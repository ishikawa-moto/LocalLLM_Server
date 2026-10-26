<a id="slack-interruption-metadata-and-bounded-chat-deadlines"></a>
# Slack中断メタデータと上限付きChat期限

この候補が変更するのはローカルServerPCのSlack実行経路と公開stub試験だけです。ブラウザーInternet検索、cloud fallback、ツール、モデル再試行、通常知識採用、新サービスは有効にしません。

<a id="deadline-contract"></a>
## 期限の契約

要求のtimeout_secondsは整数5～120のままです。Serverは最大chat_timeout_seconds=120を通知し、Chat実行では次を使います。

    output_tokens = min(requested_max_tokens, 1024)
    input_allowance = 30 + 15 * ceil(max(0, UTF8_input_bytes - 2048) / 2048)
    output_allowance = 30 * ceil(output_tokens / 256)
    min(requested_seconds, 120, max(input_allowance, output_allowance))

入力bytesにはcontext、改行区切り、promptを含み、16,000-byte上限を維持します。出力はモデルtoken最大1,024です。入力が短いだけで短いOCR文や質問を安価な生成と判断しないよう、出力量の時間枠を設けます。呼出し側上限の適用前で、出力256 token以下は30秒、257～512は60秒、513～768は90秒、769～1024は120秒です。出力64 tokenなら人工の10,902-byte入力でも最大105秒、1,024 tokenなら最大120秒です。呼出し側が30秒を指定すれば最大30秒です。モデル設定やcold start方針は変えず、操作内の起動／受付時間も同じ絶対期限を消費し、別枠で延長しません。

これは上限付きの時間割当てで、実測遅延や回答成功の保証ではありません。切替前にServer／Clientのcapability、要求timeout、HTTP待ち時間を整合させる必要があります。30秒しか要求／待機しない旧Clientには効果がありません。期限は実行とレビューの全passに共通の絶対値で、reviewは合計60秒上限を維持します。接続済みstreamの取消は既存の所有socketを使い、数値ループバックへのTCP接続試行自体は1秒上限です。

<a id="safe-terminal-metadata"></a>
## 安全な終端メタデータ

新しい中断receiptはstatus=interrupted、retry_safe=falseを保持し、次の固定reasonを追加できます。

- deadline_exceeded：実行の絶対期限に到達。
- foreground_preempted：通常foreground要求がこのbackground leaseを取り消した。
- background_preempted：より高優先の制限付きbackground処理が取り消した。
- explicit_cancel：認証済みHostが同じ要求とsessionを取り消した。
- transport_timeout、upstream_failure、invalid_output、background_unavailable、cancelled、execution_failed：例外本文を含まない安定した分類。

leaseの最初の取消理由を優先します。controller condition lock内で取消を記録する際にtransport I/Oは行いません。foreground受付と取消はatomicに保ち、background並行数は1のままで、中断要求を自動再送しません。

diagnosticsに入れるのは許可済み数値（input_bytes、max_tokens、requested_timeout_seconds、deadline_seconds、elapsed_ms、started_at_unix_ms、finished_at_unix_ms）と固定phase（admission/generation/validation）だけです。経過時間は単調時計を使い、壁時計timestampは相関metadataなのでシステム時計変更で動く場合があります。prompt、OCR本文、例外文、パス、認証情報、provider出力を含めません。独立した制限付きreceipt JSONへ保存し、通常telemetryにはSlack識別子を出しません。

認証済みstatus endpointと厳密な同一要求再送は、無害化したmetadataだけを返します。理由のない旧active/interrupted receiptはinterrupted/retry_safe=falseのままにし、過去の理由を創作しません。DB schema移行や書換えは不要で、既存の失敗要求をこの候補が再実行することもありません。

<a id="validation-and-rollout-boundary"></a>
## 試験と展開の境界

公開試験は人工入力とstub／fixture transportだけを使います。byte／期限境界、実foreground socket中断、明示取消、最初の理由保持、単一background lease、旧receipt、再送／status metadata、遅れたtimer callback、秘密情報を含む例外の抑制を検証します。

本番配備、Gateway再起動、モデル起動、実推論、元要求再送、実Slack投稿は準備範囲外です。別途承認した切替前に正確なsource hashを固定し、mainだけでなく稼働Gateway／Slack sourceとも比較し、Client timeout互換性を確認します。既存設定／DBを保持し、実行中・待機要求なしの保守確認、新しいidle status、process所有権を確認します。失敗時は現状を診断用に保持し、古いruntime／設定／DBへ復元しません。このpacketでは独立レビューと実配備／結合検証は行っていません。
