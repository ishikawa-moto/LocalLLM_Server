# ClientPC Continue / LocalBrain setup guide

## API key and sign-in

LocalBrain Qwen does not use the OpenAI API. Do not create or paste an OpenAI API key into Continue.

The model entry uses `provider: openai` only because the loopback LocalBrain Bridge implements an OpenAI-compatible HTTP API. Its `apiBase` must remain `http://LOOPBACK_HOST:BRIDGE_PORT/v1`. The value `apiKey: local-loopback-only` is a non-secret placeholder sent only to the local bridge.

Continue Mission Control sign-in is separate from OpenAI. This installation uses Continue's Local Config. Prefer the Local Config without signing in. If Continue 2.0 requires onboarding before it exposes Local Config, sign in to Continue itself; do not enter an OpenAI API key and do not select a cloud model.

The official Codex extension has its own ChatGPT/OpenAI account session. Do not copy its credentials into Continue.

## Files installed on ClientPC

- Continue config: `C:\Users\USERNAME\.continue\config.yaml`
- Client settings: `C:\Users\USERNAME\AppData\Local\LocalBrain\app\clientsettings.json`
- Client executable: `C:\Users\USERNAME\AppData\Local\LocalBrain\app\localbrain.exe`
- Client audit log: `C:\Users\USERNAME\AppData\Local\LocalBrain\logs\audit.jsonl`
- Startup task: `LocalBrain Client Host`
- Local model endpoint: `http://LOOPBACK_HOST:BRIDGE_PORT/v1`

The bridge must listen only on `LOOPBACK_HOST`. Do not expose port BRIDGE_PORT through Windows Firewall.

## Normal Continue setup

1. Restart VS Code.
2. Open the Continue sidebar (`Ctrl+L` unless that shortcut has been reassigned).
3. Open the config/agent selector above the prompt and choose **Local Config**.
4. Choose **LocalBrain Qwen 27B** as the model.
5. Choose **Agent** mode.
6. Open the tool policy list. Keep `brain_search`, `brain_context`, `brain_get`, `brain_find_*`, `brain_get_*`, and `brain_status` available. Read-only tools may be Automatic. Keep every `brain_propose_*` tool on Ask First.
7. Send this smoke test:

   `brain_statusでSecondBrainの状態を確認し、その結果を日本語で短く説明してください。ファイルは変更しないでください。`

## Project allowlist

`D:\GitHub` is a repository container, not a Git repository, so it must not be registered as one broad project. Register each real Git repository separately:

```powershell
& "$env:LOCALAPPDATA\LocalBrain\app\localbrain.exe" register-project "D:\GitHub\REPOSITORY_NAME"
```

## Cleanup after a failed installer run

An interrupted or failed run must not leave an encrypted client PFX in the installer cache. Remove only residual `client.pfx` files after the successful installation:

```powershell
Get-ChildItem -LiteralPath "$env:LOCALAPPDATA\LocalBrain\installer" -Recurse -File -Filter client.pfx -ErrorAction SilentlyContinue |
  Remove-Item -Force
```

Current launchers remove the copied PFX in a `finally` block, including on failure.

## Prompt to give Codex on ClientPC

Copy the following block into the Codex VS Code extension on ClientPC:

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

## Expected result

- Continue shows `LocalBrain Qwen 27B` under Local Config.
- A first request automatically starts Qwen on ServerPC without asking the user to resend it.
- `brain_status` succeeds through the local MCP adapter.
- Only actual Git roots are present in `%LOCALAPPDATA%\LocalBrain\projects.json`.
- No OpenAI API key is stored in Continue.
