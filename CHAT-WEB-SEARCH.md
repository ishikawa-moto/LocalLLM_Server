# Browser Chat public Web search

通常ブラウザChat専用です。Slack、OCR、SecondBrain Reviewer、Agent v2、外部AIレビューの通信規則は変更しません。既存Qwenがローカルで検索要否と短い検索語を判定し、必要時だけChatがTavilyの公式Keyless Search APIを1回呼びます。回答生成もローカルQwenです。外部AIへのレビュー依頼や回答生成API呼出しはありません。検索サービス内部の処理方式を保証するものではありません。

## Local setup

1. この実装はTavilyの無料・回数制限付きKeyless方式です。アカウント、API契約、APIキー、カード登録、追加常駐サービスは不要です。無料枠は無制限ではなく、提供条件・可用性はサービス側で変わる可能性があります。
2. ServerPCで`config/chat-web-search.example.json`を`config/private-chat-web-search.json`へコピーします。`provider`は`tavily_keyless`のまま、`enabled`を`true`にします。この実設定はGit対象外です。APIキー・Authorization・Tokenの設定は受け付けません。
3. 通常の保守手順でChatだけを再起動します。既存のGateway・Brain・モデル設定の変更は不要です。ブラウザを再読込すると「Web検索 ON（必要時）」が表示されます。未設定・無効時も通常会話は使えます。

公式Keyless仕様: https://docs.tavily.com/documentation/keyless

検索仕様: https://docs.tavily.com/documentation/api-reference/endpoint/search

## Behavior and boundaries

- ブラウザのWeb検索がONのとき、現在のユーザー入力だけをローカルQwenの判定へ渡します。検索用判定は最大256出力トークン・60秒、検索は最大1回・5結果・256 KiB・12秒です。失敗を自動再試行しません。挨拶・文章変換など検索不要の判定なら外部通信は0です。
- 外へ送るのは生成した検索語と固定の検索条件だけです。認証ヘッダー・Cookie・環境変数内のAPIキーは送りません。会話履歴、SecondBrain参考資料、OCR原本・結果、私有ファイルを検索APIへ渡す経路はありません。短い検索語にも個人情報を含む可能性はあるため、個人情報・社内情報を入力するときはWeb検索をOFFにしてください。高確度の鍵・メール・ローカルパス等は拒否しますが、これは一般的な個人情報除去の保証ではありません。
- 通信先は固定のHTTPS公式APIです。任意URLの取得、ページ本文の取得、リダイレクト、環境変数proxy、Cookie、検索結果からの追加通信はありません。検索語・provider応答本文は運用ログへ記録しません。
- 取得するのは検索結果の見出し・要約・URLです。外部AIによる回答生成は`include_answer=false`で無効とし、ページ全文・画像の取得も依頼しません。ページ全文を検証したことを意味しません。検索結果は信頼されない参考資料として既存context予算へ収めます。Web結果がある場合は参考資料予算の半分を予約し、SecondBrainが全枠を使い切ることを防ぎます。長いWeb要約は実トークン数を測って短縮します。資料中の指示は実行しません。
- 回答は`[W1]`等で引用し、履歴の下に実際に渡したURLと見出しを表示します。取得日時はメタデータに残します。無料枠の制限は`rate_limited`として扱い、課金APIへの切替・自動契約・代理認証・制限回避をしません。失敗、結果なし、検索不要、OFFを区別し、未検索を検索成功とは表示しません。
- 既存API呼出側は`web_search: true`を明示しない限り外部検索を行いません。未設定時に指定した場合は明示的エラーです。新規URL取得APIは公開しません。検索結果の知識自動採用は行いません。

## Verification / rollback

`python tests/run_public_tests.py`で公開fixtureのみを使い、通常Chatと検索分岐・禁止情報・失敗・cancel・予算・安全な出典を検証します。実モデルや秘密情報は不要です。実API疎通と配備後の公開Chat試験は別の実機証跡です。

検索だけ無効化する場合は`private-chat-web-search.json`の`enabled`を`false`にし、Chatの通常保守再起動と画面再読込を行います。ソース復元が必要な場合は既知ハッシュとidleを確認し、今回変更したChatソース・画面だけを元へ戻します。DB、履歴、認証設定、Gateway、モデルを復元・削除しません。
