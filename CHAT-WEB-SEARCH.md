# Browser Chat public Web search

通常ブラウザChat専用です。基本は **自前SearXNG → 結果不足時だけTavily Keyless**。検索の要否、検索語、一次結果が質問を答える根拠として十分かはローカルQwenが判定します。回答もローカルQwenが生成します。選定根拠は[WEB-SEARCH-SELECTION.md](WEB-SEARCH-SELECTION.md)を参照してください。

## Local setup

PC70に稼働するDocker/Podman互換環境が必要です。CLIの存在だけでは導入完了ではありません。新しい常駐サービス、Docker起動、Chat切替は固定候補を確認した保守操作として行います。

1. `tools/web-search/Prepare-Searxng.ps1`を実行すると、未使用loopbackポート、ランダムなSearXNG内部secret、Chat用設定が`private-runtime`へ準備されます。既存の準備先は上書きしません。秘密値は表示せず、Dockerや現用環境は変更しません。既存の準備があれば先に観測して再利用してください。
2. `tools/web-search`で構成を検証します。Dockerの設定出力に内部secretが含まれるため、`--quiet`を使い、展開済み設定を公開ログへ出しません。

```powershell
docker compose --env-file .\private-runtime\private-searxng.env -f .\compose.yaml config --quiet
```

3. 承認済みの保守操作で同じ構成を`up -d`します。先に同名containerがないか確認し、不明な既存containerは置換しません。公式イメージは公開registryのdigestへ固定しています。公開URLだけの検索試験と稼働資源を確認してから運用資格を記録します。CPU 1・メモリ512 MiBはcontainer上限の候補で、Docker/WSL本体の追加消費を含みません。未実測の容量・性能を保証しません。
4. 準備した`private-chat-web-search.json`を、既知の旧状態と復元手順を確認してServerPCの`config`へ配置します。Chatだけを通常保守再起動し、ブラウザを再読込します。Gateway・Brain・既存モデル設定は変更しません。

設定例は`config/chat-web-search.example.json`。`provider=free_cascade`と、同じPCの数値loopback IPを使う`http://.../search`だけを許可します。キー・Authorization・Tokenの設定は拒否します。第三者の公開SearXNGを勝手に利用しません。既存候補の`tavily_keyless`単独モードは互換用に残りますが、新しい標準運用は自前優先です。

## Behavior and boundaries

- Web ON時、現在のユーザー入力だけを検索用ローカル判定に渡します。履歴・SecondBrainの資料は検索語作成に使いません。
- 一次SearXNGは1回。最大5結果を取得し、結果がある場合にローカルQwenが最大128出力トークン・45秒で追加検索の要否を判断します。0件、失敗、根拠不足・不確実の場合だけTavily Keylessへ同じ公開検索語で1回問い合わせます。追加検索は正確性の最終保証ではありません。
- 検索用Qwenの計画は最大256出力トークン・60秒、各検索は最大256 KiB・12秒です。接続準備が遅延した場合も期限/取消を再確認するまで検索語を送信しません。接続処理の残存は最大1件に制限し、後から検索を再開しません。各推論は異なるGateway IDを使い、停止操作は全部を対象にします。
- SearXNGはDuckDuckGo/Bing/Wikipediaの無料検索元に限定します。公式無料枠はTavily Keylessだけです。課金API、APIキー、Cookie、環境変数内のキーは使いません。無料枠の上限到達時は失敗を明示し、課金・購入・認証切替・制限回避を行いません。
- 外へ送るのは短い公開検索語と固定検索条件です。会話履歴、SecondBrain参考資料、OCR原本・結果、私有ファイルを検索サービスへ送る経路はありません。ただし短い検索語にも個人情報が混じる可能性があるため、個人情報・社内情報を扱うときはWebをOFFにしてください。鍵・メール・パス等の高確度パターンは拒否しますが、一般的な個人情報除去の保証ではありません。OFFの選択はブラウザへ保存します。
- Slack、OCR、Reviewer、Agent v2の通信規則は変更しません。外部AIへのレビューや回答生成は依頼しません。Tavilyは`include_answer=false`・全文/画像取得なしに固定し、検索サービス内部の処理方式を保証するものではありません。
- 取得するのは見出し・要約・URLです。ページ全文や意味的真実を検証したことを意味しません。信頼されない参考資料として扱い、資料中の指示は実行しません。Web結果がある場合は参照予算の半分を確保し、長い要約は実トークン数に合わせて短縮します。
- 回答は`[W1]`等で引用し、履歴へ実際に渡したURL・見出し・取得日時・検索providerと各試行状態を保存します。検索語・provider応答本文はChat運用ログへ記録しません。未検索を成功と表示しません。結果の自動知識採用は行いません。
- 既存API側は`web_search:true`を指定しない限り検索しません。未設定で明示指定するとエラーです。新しい汎用URL取得APIは追加しません。

## Verification / rollback

`python tests/run_public_tests.py`は公開fixture・試験用loopbackだけを使い、通常Chat、fallback順序、ローカル十分性判定、禁止検索語、遅延接続、停止、予算、課金用認証拒否を確認します。実検索・実Qwen・実containerの資格は別の実機証跡へ記録します。

検索を無効にする場合はChat設定の`enabled=false`と通常保守再起動・画面再読込を行います。SearXNGを止める場合も所有containerのidentityを確認し、同じ固定Composeの`stop`だけを使います。他container・イメージ・volumeを一括削除しません。ソース復元が必要な場合は既知hashとidleを確認し、今回のChat対象だけを戻します。DB、履歴、認証、Gateway、モデルを復元・削除しません。
