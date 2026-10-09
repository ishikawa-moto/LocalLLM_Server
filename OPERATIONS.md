# LocalBrain運用手順

## 状態確認

```powershell
Set-Location C:\Users\USERNAME\Documents\LocalBrain
.\Manage-LocalBrain.ps1 -Action Status
```

Gatewayの監査ログは`logs\audit.jsonl`、各processの標準出力・エラーは`logs\<service>.out.log`と`logs\<service>.err.log`に保存されます。要求本文、秘密情報本文、認証トークンは監査ログへ書きません。

## SecondBrain

ファイルまたはChatGPT export ZIPを`raw`へ取り込みます。同一内容はsource hashで重複排除され、ChatGPT conversationの更新版は原本を残したまま旧版metadataをsupersededにします。対応するMarkdown、text、RSTファイルは`SecondBrain\raw`へ直接コピーしても、アイドル司書が検出して自動queue登録します。

```powershell
.\.venv\Scripts\python.exe .\src\localbrain.py ingest C:\path\to\export.zip --project chatgpt
.\Manage-LocalBrain.ps1 -Action IndexBrain
.\Manage-LocalBrain.ps1 -Action RunLibrarian
```

旧版の`chatlogs`を新しいimmutable raw構造へ一度だけ移行する場合は次を使います。再実行しても重複しません。

```powershell
.\.venv\Scripts\python.exe .\src\localbrain.py migrate-legacy-chatlogs
```

高確度の秘密情報を検出した資料は`SecondBrain\quarantine`へ隔離され、検索・Git対象になりません。原本は自動削除しません。アイドル司書は有効時に未処理sourceをLocal Qwenで整理し、Codexを自動使用しません。検証可能なFACTだけをwikiへ自動昇格し、SYNTHESISとDECISIONはdraftへ保存します。任意wiki内容を直接変更するMCP toolはありません。

## Codex Review Runner

ClientPCの許可済みGit workspace内で使用します。

```powershell
localbrain.exe review classify .localbrain\requirement.md
localbrain.exe review plan C:\path\to\repo .localbrain\plan-review.md
localbrain.exe review local-validation C:\path\to\repo .localbrain\local-validation.json
localbrain.exe review implementation C:\path\to\repo .localbrain\implementation-review.md
localbrain.exe review status C:\path\to\repo
```

`.localbrain\confidential`が存在するprojectではCodex呼び出しを拒否します。各レビュー種別は失敗した起動も含めて最大2回です。実装レビュー前に、必須test、Acceptance Criteria、local reviewがすべて成功した`local-validation.json`を登録します。完了可能になる条件はBLOCKER 0、MAJOR 0、missing tests 0、local validation成功、Acceptance Criteria成功、必要なレビュー成功です。

Review Runnerはpacketだけを一時ディレクトリへ複製し、元repositoryやContinue会話をCodexへ渡しません。秘密鍵や高確度credential形式をpacketから検出した場合も呼び出しを拒否します。packetには必要なGit diff、test結果、source fragmentを明示的に含めてください。

## バックアップと復旧

ServerPCのGatewayを起動した状態で、mTLS必須化とSecondBrain経路を実機確認できます。

```powershell
.\.venv\Scripts\python.exe .\tests\integration_gateway.py
```

2時間継続試験はGateway経由で毎分QwenとSecondBrainを確認し、中間点でモデル停止・自動再起動も実行します。

```powershell
.\Manage-LocalBrain.ps1 -Action StartLongRun
Get-Content .\logs\stability-current.json -Raw
```

```powershell
.\.venv\Scripts\python.exe .\src\backup.py F:\LocalBrainBackups
```

バックアップは秘密情報、モデル、派生index、cache、logを含みません。復旧時はGatewayを停止し、ZIPのmanifestを検証してからコードとSecondBrain原本を戻し、`IndexBrain`で派生indexを再作成します。

## 手動で必要な作業

1. ServerPCで`Install-Server.ps1`を管理者PowerShellから一度実行する。
2. `Prepare-Client-Package.ps1`で指定したPFX passwordをZIPとは別経路でClientPCへ伝える。
3. ClientPCで`Setup-Client.ps1`を一度実行し、最初のGit workspaceを登録する。
4. ClientPCを再起動し、ContinueのAgent modeからE2E試験を行う。

この4点以外は通常操作に含めません。
