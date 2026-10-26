<a id="slack-recovery-candidate"></a>
# Slack回復処理の候補仕様

出力量を考慮する期限実装は、回復処理とは別に統合されています。既存の呼出し側上限と固定要求は120秒以内に制限されますが、応答時間の保証ではありません。

`POST /v1/slack/retry`は既存のHost限定認証とrestriction検査を使用します。logical ID、直前要求の正確なID／ハッシュ、新しい固定chat要求、許可された優先処理への譲渡理由を受け取ります。要求本文はRequest ID以外を直前要求と一致させます。既存の推論セマフォ、直前要求の終端状態、同じSession、総試行最大3回、単一のidleモデルslotを確認した場合だけ、一意の直前→後続リンクと新しいrunを1トランザクションで予約します。slot情報が欠落・非互換ならモデルを起動せず見送ります。後続要求の重複は既存結果を利用します。期限終了・不明・別系統の連鎖は再試行しません。

`GET /slots`はllama.cppの固定ループバック接続先で、メタデータだけを扱います。単一slotの`is_processing=false`を検査して応答を破棄し、prompt、metrics、tokenは出力しません。[上流のServer API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#get-slots-returns-the-current-slots-processing-state)を参照してください。導入版の互換性と実キャンセル後の静止状態は本番配備時の検証が必要で、公開stubは実モデルの証明ではありません。

新しい`retry_links`表は隔離候補のSlack制限付きストア内だけに存在し、対応Sessionの退役runと一緒に削除します。この候補では本番移行、再起動、モデル要求、Canonical更新、秘密情報変更、mainマージは行っていません。`quiescent-attempt-v1`を通知・使用するClientより先にServerを配備し、別途承認したidle／identity／hash／DBガードと明示的な保持計画を用います。未知の状態は保留し、現行の運用判断により自動rollback／DB復元は禁止です。

公開試験は、競合試行、永続的な連鎖、入力不変、有限回の再試行、成功結果の再利用、slot欠落／稼働中の拒否、期限終端の拒否、Clientとの共通canonical要求fixtureを扱います。全通常回帰は公開ループバック設定だけを使用します。実Serverの配備identityと実Gateway／Slack動作は別の検証工程です。

slot検査には絶対2秒の期限があり、I/O前に同じ優先度／キャンセルleaseへ登録します。HTTP/1.0または接続を閉じるslot応答は、Windowsで移譲されたsocket wrapperを確実に中断できないため、本文を読む前に不明と扱います。公開の遅延応答fixtureでforegroundによる速やかなキャンセルと期限終了を検証し、非公開socket内部には触れません。


キャンセルには、正確に所有するループバックsocketに対する公開APIの`socket.detach`／`socket.close`を使い、後でbuffered応答wrapperが再利用済みdescriptorを閉じることを防ぎます。キャンセル側スレッドは応答読取ロックを待たず、最終HTTP wrapperの後始末は要求所有者が行います。重複キャンセルで新しいsocketを閉じないことも試験します。接続を閉じる応答とHTTP/1.0のslot応答は未検証として、本文を読まず不明を返します。
