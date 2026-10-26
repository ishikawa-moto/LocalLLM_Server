# 実装判断記録

<a id="semantic-backend"></a>
## セマンティック検索の基盤

- 確認した事実： ServerPCのWindows Application Controlが`sentence-transformers`の`_regex`ネイティブDLLを遮断した。
- 問題： Qdrant Python Local ModeとSentence Transformersを同じPython processで使用できない。
- 影響： 当初のQdrant Local Mode構成はServerPCの現在の実行ポリシーでは起動しない。
- 最小の代替案： 公式Qwen3-Embedding-0.6B Q8_0 GGUFを既存llama.cppでCPU実行し、正規化した512次元vectorをSQLiteへ差分保存する。
- 理由： Docker、WSLサービス、LAN port、追加の常駐基盤を増やさず、Exact + Semantic、差分index、source/revision検証を維持できる。

<a id="serverpc-startup"></a>
## ServerPCの起動

- 確認した事実： 既存Scheduled TaskはInteractive logon trigger、Limited権限で、未実行状態だった。
- 問題： ServerPCへログオンしない場合はGatewayが起動しない。
- 影響： ClientPCからLocalBrainを利用できない。
- 最小の代替案： `Install-Server.ps1`を管理者PowerShellで一度実行し、SYSTEM + AtStartup taskと設定されたClientPC限定Firewall ruleへ置換する。
- 理由： Windowsのtask principalとFirewall変更には管理者権限が必要であり、通常ユーザーprocessから安全に代行できない。

<a id="resource-protection"></a>
## 資源の保護

- 確認した事実： ServerPCには12 GB VRAMがあり、停止中と起動済みでは必要な空きVRAMの意味が異なる。
- 問題： RAMだけでは、他アプリがVRAMを占有している状態でのモデル起動を事前に防げない。
- 影響： llama.cppの起動失敗やWindows表示系の不安定化につながる。
- 最小の代替案： 停止中の新規モデル起動だけ、`nvidia-smi`で取得した空きVRAMを設定値`min_gpu_free_gb_for_start`と比較する。起動済みモデルの要求はその使用量を理由に拒否しない。
- 理由： 初期値8.5 GBは現在の実機で起動試験できる保守的な入口であり、監査可能かつ実測後に変更できる。

2時間試験とChatGPT raw移行後、Qwen稼働中の総RAMが一時29.07 GBとなり、29.0 GB閾値では正常な既存model requestまで拒否された。実測に基づき`max_ram_usage_gb`を29.5 GBへ変更し、約2.4 GBを残しつつ境界の揺らぎを許容した。

<a id="agent-grown-secondbrain"></a>
## エージェントが育てるSecondBrain

- 確認した事実： 従来実装はhybrid検索とproposal保存を提供したが、rawから再利用知識を継続的に生成する昇格経路を持たなかった。
- 問題： 人間によるwiki整理が前提になり、Local Agentの文脈改善とCodex利用量削減が自動的に循環しない。
- 影響： 資料を投入しても検索ノイズが残り、entity統合、provenance、decision gateが一貫しない。
- 最小の代替案： source hash付きimmutable raw、FACT/SYNTHESIS/DECISION分類、direct-source FACT promotion、review待ちdraft、profile化したidle librarian、Git履歴、append-only logを既存SecondBrainへ追加する。
- 理由： Agentによる自動成長を実現しながら、AI情報の自己増殖、秘密情報昇格、無承認decision、Codexの深夜消費を構造的に防げる。

<a id="revised-implementation-validation-2026-09-14"></a>
## 修正版の実装検証（2026-09-14）

- Python単体試験：38 PASS。
- .NET 10 Client Host：Release buildとclean publishがPASS、警告0、エラー0。
- 既存ライフサイクルの長時間試験：120.04分、117サンプル、エラー0。モデル停止・再起動を含め全件PASS。
- 修正版mTLS結合試験：クライアント証明書なしの拒否、およびGateway、hybrid検索、entity、concept、synthesis、出典情報、Git履歴、superseded読取経路がPASS。
- ChatGPT移行：変更不可raw出典48件を作成し、旧48件はsupersededとして保持。直後の再実行は作成0件、重複48件。
- ローカルlibrarian実試験：出典直結FACTを3件昇格、SYNTHESISは下書きを保持。信頼されないAI出典のFACT候補はレビュー下書き1件へ集約し、根拠のないDECISION候補を拒否。
- 当時のindex：hybrid、111文書、23,405チャンク／vector。古い出典とindexエラーなし。
- 最終バックアップ：`G:\LocalBrain-Backups\Server-snapshots\LocalBrain-20260914-065124.zip`、SHA-256 `03C96954376452AF9AFE514F0787E7C470E773A6F3AEEBCC0C367870DC41A48B`。認証情報、隔離資料、モデル、派生index、キャッシュ、サービスログは除外。

残る実機固有の工程は、ServerPCでの管理者によるScheduled Task／Firewallの初回設定と、ClientPCでの暗号化クライアントパッケージの導入です。対話中のWindowsユーザーによる操作が必要で、非管理者のサービスプロセスでは完了できません。
