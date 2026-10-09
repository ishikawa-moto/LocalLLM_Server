# ClientPC用LocalBrainクライアント

## 初回だけ行う操作

1. ServerPCで作成した暗号化ZIPをClientPCへコピーして展開します。
2. `Setup-Client.cmd`をダブルクリックします。
3. ZIP作成時に決めたPFXパスワードと、最初に使うGit repositoryのフォルダを入力します。
4. 完了後にVS Codeを再起動し、Continueで`LocalBrain Qwen 27B`を選び、Agent modeを使います。

セットアップはContinueと公式Codex拡張の導入、.NET Client Hostの配置、非export可能なクライアント証明書のWindows証明書ストアへの登録、Continue設定、ログオン時の自動起動をまとめて行います。

Continueのtool policyでは、`brain_search`等の読み取りtoolはAutomatic、`brain_propose_writeback`、`brain_propose_decision`、`brain_propose_merge`、`brain_propose_supersede`はAsk Firstのまま使用してください。Continueの既定はtool実行前の確認です。proposal toolをAutomaticへ変更しないでください。

Bridgeは`LOOPBACK_HOST:BRIDGE_PORT`だけで待ち受けます。ServerPCとの通信だけがmTLSで、ローカル設定に指定されたGateway（`SERVER_HOST:GATEWAY_PORT`）へ出ます。`SERVER_HOST`は実環境のServerPCホスト名またはアドレスです。

## repositoryを後から追加

```powershell
%LOCALAPPDATA%\LocalBrain\app\localbrain.exe register-project C:\path\to\repo
```

## Codex Review Runner

```powershell
localbrain.exe review classify .localbrain\requirement.md
localbrain.exe review plan C:\path\to\repo .localbrain\plan-review.md
localbrain.exe review local-validation C:\path\to\repo .localbrain\local-validation.json
localbrain.exe review implementation C:\path\to\repo .localbrain\implementation-review.md
localbrain.exe review status C:\path\to\repo
```

`local-validation.json`は`required_tests_passed`、`acceptance_criteria_passed`、`local_review_passed`をbooleanで持ち、すべてtrueの場合だけ受理されます。LOWはこのlocal validationだけでCodexを呼びません。NORMALはその後に実装レビュー1回、HIGHは計画レビューとlocal validation後の実装レビューを各1回行います。失敗したCodex起動も上限2回へ数えます。Review packetにはRequirement、Acceptance Criteria、Qwenの計画、必要なdiff、test結果だけを含めます。
