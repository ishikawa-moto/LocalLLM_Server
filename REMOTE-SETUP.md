<a id="clientpc-setup"></a>
# ClientPCのセットアップ

1. ServerPCで`Install-Server-Admin.cmd`を1回実行し、Windowsの管理者確認を承認します。
2. `Prepare-Client-Package.cmd`を実行し、12文字以上の新しいPFXパスワードを指定します。暗号化ZIPと`Install-LocalBrain-On-ClientPC.cmd`起動スクリプトが一緒に生成されます。パスワードはZIPとは別に管理します。
3. ClientPC限定の一時転送共有が必要なら、管理者として`Create-Temporary-Client-Share.ps1`を実行します。ClientPCで`\\SERVER_HOST\LocalBrainTransfer`を開き、`Install-LocalBrain-On-ClientPC.cmd`を実行します。ZIPをローカルへコピーしてから展開し、PFXパスワードと最初のGit作業場所を入力して、クライアントの導入とループバック待受の確認を行います。
4. 導入確認後、ServerPCで管理者として`Remove-Temporary-Client-Share.ps1`を実行し、共有とTCP SMB_PORTのFirewallルールを削除します。
5. VS Codeを再起動し、ContinueのAgentモードで**LocalBrain Qwen 27B**を選びます。
6. 調査計画・実装はContinueで行います。LOWではCodexを使わず、NORMALでは最終レビュー1回、HIGHでは独立した計画レビューと最終レビューを行います。

BridgeはWindowsログオン時に起動します。最初のContinue要求でServerPCのモデルが起動します。30分間モデル要求がなく、実行中・待機中のジョブもないときにモデルを停止します。heartbeatが途絶えても実行中の推論は中断しません。後続要求はモデルを再起動し、要求の再送を必要としません。

SecondBrainの読み取りツールはローカルMCPアダプターを通じて利用できます。writeback、decision、merge、supersedeツールは提案を作成するだけで、ユーザー承認が必要です。ServerPCのidle librarianは`raw`から検証済み知識を育て、Codexを自動呼び出ししません。

`SERVER_HOST`は設定済みServerPCのホスト名またはアドレスを表すプレースホルダーです。説明用の例示IPを実際の接続先として使用しないでください。
