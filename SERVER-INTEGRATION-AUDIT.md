<a id="server-integration-audit-and-isolated-correction"></a>
# Server結合監査と隔離環境での修正

日付は2026-10-23です。範囲は本番の読み取り監査、隔離候補、公開fixtureだけです。この変更に配備、mainマージ、モデル実行、実Slack投稿、元要求再送、認証変更、本番DB更新は含みません。

<a id="relationship-to-running-code"></a>
## 稼働コードとの関係

観測したmainはec26ab902c920647f8dba0d3f3489eed4af0f821でした。本番には出力量対応期限に関する未コミット変更が4件あり、src/slack_gateway.py、tests/test_slack.py、tests/test_slack_interruptions.py、SLACK-INTERRUPTION-DIAGNOSTICS.mdでした。

採取patchのSHA256は3e5c597cc6a2f56f240b157e4f7fcfbbc7992d12f56a0dc15adce000c4b81324で、既存の出力量対応配備receiptと一致します。この候補はその修正を保持し、SERVER-EXECUTION-CONTRACT.mdに説明する実行契約の修正を加えます。本番ファイルを書き換えるものではありません。

観測した本番sourceのSHA256：

| ファイル | SHA256 |
|---|---|
| src/control_gateway.py | af87ee9394a1e5f266d3e5a4ce72321b31a971b7eb9dd518f479c02839b74765 |
| src/slack_gateway.py | 21beac736bc9907641a3a23eb02d59b8fe9c9aae41f062b615235030019d3d50 |

GatewayのPIDとbirthは出力量対応配備receiptと一致し、task定義も不変でした。通常権限ではSYSTEM processのimage／command lineを取得できなかったため、新たなloaded image／source hashの証明は主張しません。設定上の依存ファイルは、loaded processと分けてhash化しました。

読み取りcontrol statusはGateway／Brain／Proxy正常、Qwen未load、active/queued0でした。Reviewer taskはReady、task instance0で、保存markerもpendingではありません。これらの観測だけではClientの全RAM queueや、別途配信したすべてのreviewer processがidleだとは証明できません。

過去のClient capability証明は保持しました。この監査ではHost identityの偽装や新しいHost認証capabilities要求を行わず、現在のClient／Server結合成功を保証しません。

<a id="reproduced-defects"></a>
## 再現した不具合

1. 予約済みの実行中要求を再送すると、モデルcallbackがまだ稼働中でもinterruptedを返しました。残存するactive予約も再送では終端扱いになり、一方binding済みstatusは正しくunresolvedを返しました。
2. lease解放後に受理した取消の後でもcomplete receiptが保存され得ました。
3. 終端の永続化より前に登録を消していたため、所有finalizerが結果保存中でもstatusがunresolvedを返す隙間がありました。
4. 取消transport I/Oを全体の登録ロック内で行い、そのロックを使う登録／status／退役を不要に遮断しました。

候補は最終処理まで登録を保持し、取消受理と終端確定を直列化し、終端上書きを防ぎ、非終端再送にrunning/unresolvedを使い、範囲を明示した読み取り活動snapshotを公開します。この実行状態修正自体は再試行、強制解決、DB reset、暗黙の保守lockを導入しません。上限付き回復は別途SLACK_RECOVERY.mdで管理します。

<a id="validation"></a>
## 検証

- 初回公開契約再現：10試験、assertion失敗4件、エラー6件。エラーには候補だけに存在するcapability／snapshot APIの意図的欠落を含み、並行性／状態の失敗も直接観測しました。
- 最終Slack fixture：107 PASS、失敗0、エラー0、skip0。新しい契約回帰14件を含みます。
- 既存Gateway fixture：14 PASS、失敗0、エラー0、skip0。
- 通信は公開runnerがbindしたlistenerに限定し、本番設定、モデルendpoint、Slackアカウント、私有データは使用していません。

ローカル証拠には追跡ファイル103件、設定23件、設定上の依存binary59件のhashを保持しています。私有設定値、運用receipt、DB本文はこのPRに含めません。設定Pythonは3.12.14、導入済みcryptographyは50.0.2でしたが、元requirements-gateway.txtは50.0.1を固定していました。隔離候補は実機側と親側の公開TLS／Gateway試験で使った同じ50.0.2を固定し、宣言／runtime回帰で一致を強制します。本番packageの導入・更新・変更はなく、新規PCでの導入は別検証です。GGUFは再hashしておらず、実モデル互換性も主張しません。

<a id="remaining-boundary"></a>
## 残る検証範囲

Clientによるv2非終端応答／取消受理の扱い、実Host capability交渉、cold start遅延、私有入力／OCR動作、実推論は未検証です。活動snapshotは全体の静止フェンスではありません。この候補は配備・mainマージしていません。

<a id="parent-integration"></a>
## 親側への統合

実機監査commit `2cdd796869cf696bd65e563c72c2ca0719b49a7f`は既存の上限付き回復候補（Server PR 4）へ統合しています。出力量対応期限の実装は複製せず共有します。`localbrain.slack-execution.v2`と`quiescent-attempt-v1`の双方を通知し、既存の回復連鎖、静止probe、所有socket保護を保持します。feature branch競合解消後の全結合公開回帰は188件、失敗・エラー・skipは0でした。Clientのメモリー内transport fixtureも、running/unresolved見送り、固定status binding、2回目の生成transport禁止、終端結果回復について26項目成功しました。この統合で本番checkoutやサービスは変更していません。