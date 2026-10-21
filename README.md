# LocalBrain

LocalBrainは、通常の調査・計画・実装・テストをServerPC上のQwenへ任せ、Codexをリスクに応じた独立レビューだけに使う構成です。接続先は環境ごとのローカル設定で指定します。

SecondBrainはAgent自身が育てるpersistent knowledge systemです。人間は主に`raw`へ資料を投入し、直接検証できるFACTは自動昇格、SYNTHESISはdraft review、DECISIONはCodexまたはユーザー確認を経ます。詳細は`SECOND-BRAIN-SPEC.md`と`SecondBrain\AGENTS.md`を参照してください。

## 配置

- ServerPC: mTLS Control Gateway、llama.cpp/Qwen、CPU embedding、SecondBrain
- ClientPC: VS Code、Continue、.NET 10 LocalBrain Client Host、Codex拡張
- `C:\Users\USERNAME\Documents\LocalBrain`: コード、設定、SecondBrain原本
- `D:\LocalBrain\models`: 主力Qwenモデル
- `D:\LocalBrain\embedding-models`: Qwen3-Embedding-0.6B Q8_0
- `runtime\index.sqlite3`: Exact index
- `runtime\semantic.sqlite3`: 差分semantic vector index

ServerPCが公開するのはTCP GATEWAY_PORTのGatewayだけです。llama.cpp、embedding、SecondBrain内部APIはそれぞれLOOPBACK_HOSTのLLM_PORT、EMBEDDING_PORT、BRAIN_PORTを使用します。

## 通常利用

ClientPCでVS Codeを起動し、Continueの`LocalBrain Qwen 27B`を選んでAgent modeへ切り替え、自然言語で依頼します。Qwen停止中の最初の要求はBridgeが保持し、GatewayがQwenを起動してから自動送信します。30分間モデル要求がなく、実行中・待機中のジョブがなければ主力Qwenだけを停止します。

リスク別Codex利用回数はLOW 0回、NORMALは実装レビュー1回、HIGHは計画レビューと実装レビューの2回です。レビューは毎回ephemeralで、NORMALはmedium、HIGHはhigh reasoningを指定します。

## セットアップ

ServerPCでは、管理者PowerShellで一度だけ次を実行します。

エクスプローラーから`Install-Server-Admin.cmd`をダブルクリックし、Windowsの管理者確認を承認する方法でも実行できます。

```powershell
Set-Location C:\Users\USERNAME\Documents\LocalBrain
.\Install-Server.ps1
```

次に暗号化クライアントZIPを作り、ClientPCへ安全にコピーします。

`Prepare-Client-Package.cmd`をダブルクリックするか、次を実行します。PFX用に12文字以上の新しいパスワードを2回入力します。

```powershell
.\Prepare-Client-Package.ps1
```

ClientPCではZIPを展開し、`Setup-Client.cmd`をダブルクリックします。PowerShellで直接行う場合は次を実行します。

```powershell
.\Setup-Client.ps1 -CertificatePfx .\certs\client.pfx -InitialWorkspace C:\path\to\repository
```

詳細は`client-package\README.md`と`OPERATIONS.md`を参照してください。

公開文書と設定例の`SERVER_HOST`、`CLIENT_HOST`、`LAN_SUBNET`、`*_PORT`はプレースホルダーです。`config/site.example.json`を`config/private-site.json`へコピーし、ServerPCとClientPCの実アドレス、許可するネットワーク範囲、各サービスのポートへ置き換えてください。`*_port`には引用符を付けず、整数を指定します。実設定とprivate元データはGitHub公開対象外です。
