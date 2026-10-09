# 実装判断記録

## Semantic backend

- Confirmed Fact: ServerPCのWindows Application Controlが`sentence-transformers`の`_regex`ネイティブDLLを遮断した。
- Problem: Qdrant Python Local ModeとSentence Transformersを同じPython processで使用できない。
- Impact: 当初のQdrant Local Mode構成はServerPCの現在の実行ポリシーでは起動しない。
- Minimal Alternative: 公式Qwen3-Embedding-0.6B Q8_0 GGUFを既存llama.cppでCPU実行し、正規化した512次元vectorをSQLiteへ差分保存する。
- Reason: Docker、WSLサービス、LAN port、追加の常駐基盤を増やさず、Exact + Semantic、差分index、source/revision検証を維持できる。

## ServerPC startup

- Confirmed Fact: 既存Scheduled TaskはInteractive logon trigger、Limited権限で、未実行状態だった。
- Problem: ServerPCへログオンしない場合はGatewayが起動しない。
- Impact: ClientPCからLocalBrainを利用できない。
- Minimal Alternative: `Install-Server.ps1`を管理者PowerShellで一度実行し、SYSTEM + AtStartup taskと設定されたClientPC限定Firewall ruleへ置換する。
- Reason: Windowsのtask principalとFirewall変更には管理者権限が必要であり、通常ユーザーprocessから安全に代行できない。

## Resource protection

- Confirmed Fact: ServerPCには12 GB VRAMがあり、停止中と起動済みでは必要な空きVRAMの意味が異なる。
- Problem: RAMだけでは、他アプリがVRAMを占有している状態でのモデル起動を事前に防げない。
- Impact: llama.cppの起動失敗やWindows表示系の不安定化につながる。
- Minimal Alternative: 停止中の新規モデル起動だけ、`nvidia-smi`で取得した空きVRAMを設定値`min_gpu_free_gb_for_start`と比較する。起動済みモデルの要求はその使用量を理由に拒否しない。
- Reason: 初期値8.5 GBは現在の実機で起動試験できる保守的な入口であり、監査可能かつ実測後に変更できる。

2時間試験とChatGPT raw移行後、Qwen稼働中の総RAMが一時29.07 GBとなり、29.0 GB閾値では正常な既存model requestまで拒否された。実測に基づき`max_ram_usage_gb`を29.5 GBへ変更し、約2.4 GBを残しつつ境界の揺らぎを許容した。

## Agent-grown SecondBrain

- Confirmed Fact: 従来実装はhybrid検索とproposal保存を提供したが、rawから再利用知識を継続的に生成する昇格経路を持たなかった。
- Problem: 人間によるwiki整理が前提になり、Local Agentの文脈改善とCodex利用量削減が自動的に循環しない。
- Impact: 資料を投入しても検索ノイズが残り、entity統合、provenance、decision gateが一貫しない。
- Minimal Alternative: source hash付きimmutable raw、FACT/SYNTHESIS/DECISION分類、direct-source FACT promotion、review待ちdraft、profile化したidle librarian、Git履歴、append-only logを既存SecondBrainへ追加する。
- Reason: Agentによる自動成長を実現しながら、AI情報の自己増殖、秘密情報昇格、無承認decision、Codexの深夜消費を構造的に防げる。

## Revised implementation validation (2026-09-14)

- Python unit tests: 38 PASS.
- .NET 10 Client Host: Release build and clean publish PASS, warnings 0, errors 0.
- Existing lifecycle long run: 120.04 minutes, 117 samples, errors 0, model stop/restart exercised, all PASS.
- Revised mTLS integration: no-client-certificate rejection plus Gateway, hybrid search, entity, concept, synthesis, source provenance, Git history, and superseded read routes PASS.
- ChatGPT migration: 48 immutable raw sources created, 48 legacy entries retained as superseded; immediate rerun created 0 and reported 48 duplicates.
- Local librarian live test: 3 direct-source FACTs promoted, SYNTHESIS retained as draft, untrusted AI-source FACT candidates aggregated into one review draft, ungrounded DECISION candidates rejected.
- Current index: hybrid, 111 documents, 23,405 chunks/vectors, no stale source and no index error.
- Final backup: `F:\LocalBrainBackups\LocalBrain-20260914-065124.zip`, SHA-256 `03C96954376452AF9AFE514F0787E7C470E773A6F3AEEBCC0C367870DC41A48B`; credentials, quarantine, models, derived indexes, caches, and service logs excluded.

Remaining machine-bound steps are the one-time elevated ServerPC Scheduled Task/firewall installation and running the encrypted client package installer on ClientPC. They require the interactive Windows user and cannot be completed by the non-administrator service process.
