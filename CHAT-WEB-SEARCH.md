# Browser Chat public Web search

通常ブラウザChat専用です。Slack、OCR、SecondBrain Reviewer、Agent v2、外部AIレビューの通信規則は変更しません。既存Qwenがローカルで検索要否と短い検索語を判定し、必要時だけChatがBrave Searchの公式Web Search APIを1回呼びます。回答生成もローカルQwenです。Cloud AIは呼びません。

## Local setup

1. Brave Search APIのアカウント・利用条件・料金を確認し、APIキーを取得します。既存契約がなければ別途契約が必要です。自動契約・購入は行いません。
2. ServerPCで`config/chat-web-search.example.json`を`config/private-chat-web-search.json`へコピーします。`api_key`をこのPC内で入力し、`enabled`を`true`にします。キーをチャット、PR、公開ログへ貼らないでください。この実設定はGit対象外です。Chatの実行ユーザーだけが読める権限で管理してください。
3. 通常の保守手順でChatだけを再起動します。既存のGateway・Brain・モデル設定の変更は不要です。ブラウザを再読込すると「Web検索 ON（必要時）」が表示されます。未設定・無効時も通常会話は使えます。

公式API仕様: https://api-dashboard.search.brave.com/app/documentation/web-search

## Behavior and boundaries

- ブラウザのWeb検索がONのとき、現在のユーザー入力だけをローカルQwenの判定へ渡します。検索用判定は最大256出力トークン・60秒、検索は最大1回・5結果・256 KiB・12秒です。失敗を自動再試行しません。挨拶・文章変換など検索不要の判定なら外部通信は0です。
- 外へ送るのは生成した検索語とAPI認証だけです。会話履歴、SecondBrain参考資料、OCR原本・結果、私有ファイルを検索APIへ渡す経路はありません。短い検索語にも個人情報を含む可能性はあるため、個人情報・社内情報を入力するときはWeb検索をOFFにしてください。高確度の鍵・メール・ローカルパス等は拒否しますが、これは一般的な個人情報除去の保証ではありません。
- 通信先は固定のHTTPS公式APIです。任意URLの取得、ページ本文の取得、リダイレクト、環境変数proxy、Cookie、検索結果からの追加通信はありません。検索語・キー・provider応答本文は運用ログへ記録しません。
- 取得するのは検索結果の見出し・要約・URLです。ページ全文を検証したことを意味しません。検索結果は信頼されない参考資料として既存context予算へ収めます。資料中の指示は実行しません。
- 回答は`[W1]`等で引用し、履歴の下に実際に渡したURLと見出しを表示します。取得日時はメタデータに残します。失敗、結果なし、検索不要、OFFを区別し、未検索を検索成功とは表示しません。
- 既存API呼出側は`web_search: true`を明示しない限り外部検索を行いません。未設定時に指定した場合は明示的エラーです。新規URL取得APIは公開しません。検索結果の知識自動採用は行いません。

## Verification / rollback

`python tests/run_public_tests.py`で公開fixtureのみを使い、通常Chatと検索分岐・禁止情報・失敗・cancel・予算・安全な出典を検証します。実APIキーやモデルは不要です。実API疎通と配備後の公開Chat試験は別の実機証跡です。

検索だけ無効化する場合は`private-chat-web-search.json`の`enabled`を`false`にし、Chatの通常保守再起動と画面再読込を行います。ソース復元が必要な場合は既知ハッシュとidleを確認し、今回変更したChatソース・画面だけを元へ戻します。DB、履歴、認証設定、Gateway、モデルを復元・削除しません。
