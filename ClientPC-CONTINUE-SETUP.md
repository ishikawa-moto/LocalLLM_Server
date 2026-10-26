<a id="clientpc-continue--localbrain-setup-guide"></a>
# ClientPCのContinue／LocalBrainセットアップガイド

<a id="api-key-and-sign-in"></a>
## APIキーとサインイン

LocalBrain QwenはOpenAI APIを使用しません。Continue用のOpenAI APIキーを作成したり、貼り付けたりしないでください。

モデル項目の`provider: openai`は、ループバックのLocalBrain BridgeがOpenAI互換HTTP APIを実装しているためだけに使用します。`apiBase`は`http://LOOPBACK_HOST:BRIDGE_PORT/v1`のままにします。`apiKey: local-loopback-only`は秘密情報ではないプレースホルダーで、ローカルBridgeだけへ送信されます。

Continue Mission ControlのサインインはOpenAIとは別です。この構成はContinueのLocal Configを使用するため、サインインせずLocal Configを使う方法を優先します。Continue 2.0でLocal Configを表示する前に初期設定が必要な場合はContinue自体へサインインし、OpenAI APIキーの入力やクラウドモデルの選択はしないでください。

公式Codex拡張は独自のChatGPT／OpenAIアカウントセッションを使用します。その認証情報をContinueへコピーしないでください。

<a id="files-installed-on-clientpc"></a>
## ClientPCへ配置されるファイル

- Continue設定：`C:\Users\USERNAME\.continue\config.yaml`
- クライアント設定：`C:\Users\USERNAME\AppData\Local\LocalBrain\app\clientsettings.json`
- クライアント実行ファイル：`C:\Users\USERNAME\AppData\Local\LocalBrain\app\localbrain.exe`
- クライアント監査ログ：`C:\Users\USERNAME\AppData\Local\LocalBrain\logs\audit.jsonl`
- 起動タスク：`LocalBrain Client Host`
- ローカルモデルの接続先：`http://LOOPBACK_HOST:BRIDGE_PORT/v1`

Bridgeは`LOOPBACK_HOST`だけで待ち受けます。Windows FirewallでBRIDGE_PORTを公開しないでください。

<a id="normal-continue-setup"></a>
## Continueの通常設定

1. VS Codeを再起動します。
2. Continueサイドバーを開きます（ショートカットを変更していなければ`Ctrl+L`）。
3. 入力欄の上にある設定／エージェント選択から**Local Config**を選びます。
4. モデルに**LocalBrain Qwen 27B**を選びます。
5. **Agent**モードを選びます。
6. ツールポリシー一覧を開き、`brain_search`、`brain_context`、`brain_get`、`brain_find_*`、`brain_get_*`、`brain_status`を利用可能にします。読み取り専用ツールはAutomaticでも構いません。すべての`brain_propose_*`はAsk Firstに保ちます。
7. 次の疎通確認を送信します。

   `brain_statusでSecondBrainの状態を確認し、その結果を日本語で短く説明してください。ファイルは変更しないでください。`

<a id="project-allowlist"></a>
## プロジェクト許可リスト

`D:\GitHub`はリポジトリを収める親フォルダーであり、Gitリポジトリではありません。全体を1つの広いプロジェクトとして登録せず、実際のGitリポジトリを個別に登録します。

```powershell
& "$env:LOCALAPPDATA\LocalBrain\app\localbrain.exe" register-project "D:\GitHub\REPOSITORY_NAME"
```

<a id="cleanup-after-a-failed-installer-run"></a>
## インストーラー失敗後の整理

中断・失敗した実行で、暗号化クライアントPFXをインストーラーキャッシュへ残してはいけません。インストール成功後、残存する`client.pfx`だけを削除します。

```powershell
Get-ChildItem -LiteralPath "$env:LOCALAPPDATA\LocalBrain\installer" -Recurse -File -Filter client.pfx -ErrorAction SilentlyContinue |
  Remove-Item -Force
```

現行の起動スクリプトは、失敗した場合も`finally`ブロックでコピーしたPFXを削除します。

<a id="prompt-to-give-codex-on-clientpc"></a>
## ClientPCのCodexへ渡す指示

次のブロックをClientPCのCodex VS Code拡張へコピーします。

```text
このPCはLocalBrainのClientPCです。LocalBrain Client HostとContinue 2.0.0は導入済みです。OpenAI APIキーや新しいクラウドモデルは追加しないでください。

目的は、ContinueからLOOPBACK_HOST:BRIDGE_PORTのLocalBrain Bridgeを使い、mTLSでローカル設定に指定されたServerPCのQwenとSecondBrainへ接続できる状態を確認することです。

次を順番に実行してください。
1. C:\Users\USERNAME\.continue\config.yamlを読み、LocalBrain Qwen 27B、apiBase http://LOOPBACK_HOST:BRIDGE_PORT/v1、apiKey local-loopback-only、tool_use、LocalBrain SecondBrain MCPが設定されていることを確認する。
2. C:\Users\USERNAME\AppData\Local\LocalBrain\app\clientsettings.jsonを読み、Gatewayが設定されたServerPCのURL（https://SERVER_HOST:GATEWAY_PORT）を指し、bridgePortがBRIDGE_PORTであることを確認する。SERVER_HOSTとUSERNAMEは実環境の値へ置き換える。証明書の秘密鍵を表示・exportしない。
3. Scheduled Task `LocalBrain Client Host` が実行中で、LOOPBACK_HOST:BRIDGE_PORTだけがlistenしていることを確認する。LAN向けfirewall ruleは作らない。
4. D:\GitHub直下を調べ、各サブフォルダーのうち実際にGit rootであるものだけを、localbrain.exe register-projectで個別登録する。D:\GitHub自体は登録しない。
5. ContinueのLocal Configをreloadし、LocalBrain Qwen 27Bを選べることを確認する。設定に問題があれば既存ファイルをバックアップして最小修正する。
6. localhostのOpenAI互換endpointへ小さな疎通要求を送り、停止中のQwenが自動起動して応答することを確認する。同じrequestを重複送信しない。
7. LocalBrain MCPへinitialize、tools/list、brain_statusを実行し、SecondBrainの応答を確認する。
8. 実行した確認、変更ファイル、登録したGit repository、テスト結果、残る問題を日本語で報告する。

禁止事項:
- OpenAI APIキー、Continue用クラウドAPIキー、別モデルを追加しない。
- 証明書や秘密鍵を表示・exportしない。
- ServerPCまたはClientPCに新しいLAN公開portを作らない。
- 設定されたServerPC以外のモデル接続先へ変更しない。
- D:\GitHub全体を1つのprojectとして許可しない。
```

<a id="expected-result"></a>
## 期待する結果

- ContinueのLocal Configに`LocalBrain Qwen 27B`が表示されます。
- 最初の要求でServerPCのQwenが自動起動し、ユーザーに再送を求めません。
- ローカルMCPアダプターを通じて`brain_status`が成功します。
- `%LOCALAPPDATA%\LocalBrain\projects.json`には実際のGitルートだけが登録されます。
- ContinueにOpenAI APIキーは保存されません。
