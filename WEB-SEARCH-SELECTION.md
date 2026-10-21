# 無課金のWeb検索選定（2026-10-21）

採用順は **自前SearXNG → ローカルQwenの不足判定時だけTavily Keyless** です。上流制限を含め、完全に無制限・停止しない外部検索は保証できません。検索提供者の安定性と、情報自体が正しいことは別です。

| 候補 | 利用枠・費用 | 特性と判断 |
|---|---|---|
| 自前SearXNG | ソフト無料・APIキー不要。ベンダーの月次API枠は消費しない。電気・PC資源・保守は必要 | 第一優先。DuckDuckGo/Bing/Wikipediaの無料検索元だけに限定。上流のCAPTCHA・遮断・仕様変更は残る |
| DuckDuckGo単独 | 公開Instant Answer APIは無料。全件検索APIではない | 一般検索の公式APIとしては範囲不足。直接HTML取得の独自実装は増やさず、一般検索元としてSearXNG内で利用する |
| Tavily Keyless | アカウント・APIキー・カード不要の公式無料方式。回数制限あり | 第二優先。自前結果なし、接続失敗、題材違い、古い資料、根拠不足・不確実のときだけ1回使う。無料枠終了時は停止 |
| Exa Keyless MCP | 認証なしの無料・回数制限付き方式あり | 代替候補。今回はTavilyの実疎通が成功し、直接HTTPの実装で済むため、多段MCP接続や検索先を増やさない |
| Brave Search API | 現行公式案内は無料クレジット＋有料の前払いクレジット、キー・カード登録が必要 | 検索APIとして候補だが、今回の契約なし・無課金運用では不採用。旧方式の実装は置換済みで、課金用キーを受け付けない |

Qwenの追加検索判定はローカルで行い、正解や意味的真実を保証する判定ではありません。一次結果が十分なら公式無料枠へ送信しません。一次結果が不足・利用不可なら同じ公開検索語だけを第二優先へ送ります。二次結果も不足なら、不足を明示して終了します。課金・購入・有料認証への自動切替や、無料枠の制限回避はしません。

PC70の調査時点ではDocker CLIは存在しますが、エンジンへ接続できず、登録WSLも停止中でした。自前backendの実配備・性能・検索品質は導入と実測後に別途記録します。CLIや公開fixtureの成功だけで実配備完了とはしません。

現時点の比較は公式仕様・実環境適合性・公開fixtureとTavilyの公開語1件の疎通に基づきます。同一質問群による全検索エンジンの精度比較を実施したという意味ではありません。

## 確認した一次資料

- [SearXNG公式API](https://docs.searxng.org/dev/search_api.html)
- [SearXNG自前運用と上流制限](https://docs.searxng.org/own-instance.html)
- [SearXNG公式コンテナ導入](https://docs.searxng.org/admin/installation-docker.html)
- [DuckDuckGo公式のAPI説明（全件検索ではない）](https://github.com/duckduckgo/duckduckgo-publisher/blob/master/share/site/duckduckgo/api.tx)
- [Tavily公式Keyless仕様](https://docs.tavily.com/documentation/keyless)
- [Exa公式Keyless説明](https://exa.ai/docs/get-started/exa-mcp)
- [Brave公式の現行料金・登録条件](https://brave.com/search/api/)
