# 次期LocalBrainの統合手順

## 作業基準

PC70は推論、OCR、中央のGoal・Task State・実行権・Canonical・SecondBrain、予備開発を担当する。PC73は主開発とVS Code接続を担当する。両機で同じPiとワーカー契約を使う。次期版は `codex/next-localbrain-bonsai-durable` と独立配置で検証する。PC70の既存デプロイ、タスク、DB、設定、ポートは本番のまま扱う。切替とロールバックの実行には最終承認が必要。

作成時の基点はServer `b2ffe206336ebea02d0646a7edcfd61056c711bf`、Client `68a078bf6cc8f83dc6ff65700bf394a326494485`。既存チェックアウトで作業中の場合は別cloneを使い、既存v3からは責務と試験証跡を確認した機能を個別に取り込む。

## PC70の実行順

1. 次期版専用のモデル、データ、ログ、検証用ワークスペースを確保する。まずローカルの `127.0.0.1:18080` を推論、`127.0.0.1:18443` を引継ぎAPI候補とする。使用前に空きを確認する。PC73向けには承認済みLAN経路に限定して証明書検証付きTLSとホストごとの認証を設定する。実運用に接続する前に現在のGateway・Runner・OCR・Browser Chat・SecondBrainの実稼働と設定指紋を採取する。
2. Node.js 22.19以上で `npm ci --ignore-scripts --no-audit --no-fund` を実行する。`node integration.mjs` は隣接配置のClient `next` を使う。Clientの配置が異なる場合は `node integration.mjs <worker-client.mjs の絶対パス>`。この検証では同一PCで認証、中央プロセスの強制停止と復帰、排他、旧世代拒否、往復移管、および外部送信の判定を確認する。信頼境界の検証器は合成証跡を使う。
3. Bonsai 2.5 27B PTQ1_0の固定ファイルとPrismML CUDAビルドを公開SHA256で照合する。単一スロット、`--ctx-size 131072 --cache-type-k q4_0 --cache-type-v q4_0`、`--jinja`、MTPなしから始める。起動前に実バイナリの `--help` でオプションを確認し、起動ログと `/props` で有効な設定を確認する。`--alias bonsai-2.5-27b --host 127.0.0.1 --port 18080 --parallel 1` を指定する。`node bonsai-smoke.mjs 131072 <新規証跡ディレクトリ> --long` でtokenizer、テンプレート、Pi Durableからのツール呼出し、有効Contextと長文検索を確認する。GPU/RAMのピーク、初回応答時間、独立したCPU OCRとの併用を記録し、品質・安定性・リソースに問題があれば65536で繰り返す。MTPの比較は基本構成合格後に行う。
4. `openCoordinator` に検証済みの停止・受領・完了検証器を接続し、次期版のTLS用資格情報で `coordinatorServer` を起動する。スーパーバイザーの起動・停止、隔離されたワークスペース、Git/許可済みLAN転送を接続する。プロセスツリーの停止、仲介プロセスの終了、旧ワーカーの書込権失効をそれぞれ実測する。検証不能の場合は保留を維持する。
5. CanonicalのGoal・状態・実行権はPi Durableの同じトランザクションから参照する。Observation原文と決定的検証証跡はPC70のアクセス制御された領域に置き、必要な参照と指紋を紐付ける。次期版のSecondBrain、Slack、Browser Chat、OCRを独立データと接続先で確認する。生のSlack/OCRデータと認証情報をGitHub、外部AI、公開証跡へ含めない。

公式取得元はPrismMLのモデルリビジョン `28a7093cc25bc151cc2b284c5570f12ce343fdcf`、GGUFのSHA256 `929560ec9d37cf3cadd4b00cc87b51c570082a628dd830b4692dc0b2ed18dbbe`。Windows CUDA 12.4の確認対象はPrismML `prism-b12232-f702476`、バイナリZIP `b8fc8ed66a77b946792c03d388df1f935acd292d59c8568468ea38d1f457b499`、CUDA依存ZIP `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6`。 [モデル配布元](https://huggingface.co/prism-ml/Ternary-Bonsai-2.5-27B-GGUF/tree/28a7093cc25bc151cc2b284c5570f12ce343fdcf) [PrismMLリリース](https://github.com/PrismML-Eng/llama.cpp/releases/tag/prism-b12232-f702476)

## ワーカー契約 `localbrain-handoff/1`

HTTPはTCPループバックでの結合試験に使える。LAN接続にはTLSが必須。API認証トークンはPC70用とPC73用を別管理し、PiやMCPツールの環境へ継承しない。`Authorization: Bearer <token>` と `X-LocalBrain-Worker: <検証された起動インスタンスUUID>` を付ける。

| ルート | 内容 |
| --- | --- |
| `GET /handoff/v1/health` | 契約バージョンを照合 |
| `GET /handoff/v1/tasks` | 自分のグラントと自分宛ての引継ぎを取得。適格なPC70作業には `returnPending` を含む |
| `GET /handoff/v1/tasks/<taskId>` | 自分が関連するGoalの最新状態を照合 |
| `POST /handoff/v1/command` | `protocol`, `requestId`, `action` と必要に応じ `taskId`, `epoch`, `reason`, `checkpoint` |

作成時は `create` に `goal`、実行開始に `start` とepochを渡す。PC73は明示操作後に `drain(reason=shutdown)`。PC70はPC73由来のGoalで、PC73の新しい登録を確認した場合だけ `drain(reason=return)`。`drain` 後は新規の書込コマンドを受け付けず、スーパーバイザーが既存操作を安全地点で終了してチェックポイントを確定する。`release` は旧プロセス・外部ブローカー・旧書込権の失効証明、輸送先のポリシーとチェックポイントをPC70が検証した後に `offered` を確定する。`receive` は宛先インスタンスと受領内容の検証後にepochを進める。ワーカーは `check` の成功後だけ起動し、全書込実行境界で同じepochを検査する。通信断、未知の実行結果、照合不能で実行権を再発行しない。

チェックポイントの `serverSha`, `clientSha` は完全なGit OID。`environmentSha256`, `artifactSha256`, `stopReceiptSha256` は正規化した環境・成果物マニフェスト・停止証跡のSHA256。中央の検証器はハッシュ形式だけで信用せず、認証済みスーパーバイザーが発行した証跡をタスク・旧インスタンス・epoch・対象宛先に結び付けて照合する。受領側はSHA存在、ブランチ許可、差分と未追跡物、実行環境互換性、保全経路を確認し、変更のある配置を上書きしない。

`409` の場合は最新状態を照合して待機する。通信失敗時は同じ `requestId` で一度照合し、遷移が進んでいれば `GET` の状態を使う。`check` は毎回現在状態を検査する。シャットダウンの呼出元は対象の全Goalについて `taskPreserved` を確認するまで停止する。PC70で受領または完了が確認できない場合は電源断を保留する。復帰時にはPC70で元から実行していたGoalを継続する。

## 外部送信の設定

次期版の外部AIは初期状態で全拒否。利用時は次の項目をユーザーに一度提示し、モデル・正式CLI・接続先・データ種別・認証・費用を結び付けた設定を登録する。承認が未登録、範囲外、または追加課金の有無が未確認の経路は利用しない。

| 対象 | 接続先と認証の候補 | データと費用の条件 |
| --- | --- | --- |
| Terra・Sol | OpenAIの正式Codex CLI、契約アカウント認証 | 公開・合成、または送信を明示許可した最小抜粋。契約内利用枠のみ |
| Haiku・Opus | 実機で確認した正規の契約内CLI、その契約の提供元と認証 | 公開・合成、または送信を明示許可した最小抜粋。契約内利用枠のみ |
| GitHub | 指定2リポジトリの専用非mainブランチ、既存Git認証 | 許可済みコード・設定例・合成試験のみ。GitHub不可または社外搬出禁止の作業は認可済みLAN経路で保全 |
| Slack | 次期版専用の許可済みWorkspace・Channel、最小権限の認証 | 新規投稿・添付は接続先とデータ種別の許可後に有効化。Slack/OCR原文・秘密情報を外部AIへ送らない |

Bonsai不成立時に既存の条件を満たす経路を順に選ぶ。LOWはTerra、再検証後Haiku。NORMALはTerra、Haiku、Sol、Opus。HIGHはSol、Opus。呼び出し前の `authorizeDelegation` とCLI実行環境自体の制限を両方検証する。

## 結合と一括切替

実開発Goalで実装→決定的検証→PC73から明示移管・停止→PC70継続と単独OCR→PC73再起動・登録・自動復帰を一本で確認する。並行取得、通信断、中央再起動、SHA/環境不一致、停止証跡なし、外部送信拒否、Slack・Browser Chat・VS Code接続を少数のシナリオで確認する。両リポジトリのSHA、モデル・Pi固定版、実機、入力の分類、実測結果と未確認事項を中央に記録する。旧安定版の配備・設定・DBを保全し、承認後に一括切替とロールバック手順を実行する。
