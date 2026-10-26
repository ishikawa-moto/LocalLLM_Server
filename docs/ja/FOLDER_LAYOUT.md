# ServerPCのフォルダー構成と保持方針

この案内は2026-10-27の読み取り監査に基づきます。Gitのcheckout、配備済みコード、稼働プロセス、データ、作業資料を別々に管理します。Gitの文書変更やpullだけでは、本番の起動先は切り替わりません。

## 製品とデータ

| 場所 | 用途 | 分ける理由・取扱い |
|---|---|---|
| `C:\Users\USERNAME\Documents\LocalBrain` | 正規ServerソースのGit checkoutと安定data root | 開発ブランチの変更と、運用データの保持を区別する。データをGitへ追加しない |
| `config/` | 私有の環境設定・認証・TLS、公開設定例と説明 | 実設定はGit対象外。説明文書を訳してもJSON・秘密鍵・ACLは変更しない |
| `runtime/` | Chat履歴、Gateway jobs、制限付きSlack状態、検索index、OCR mailbox、所有process記録 | 私有データと稼働状態。キャッシュに見える名前だけで削除しない |
| `SecondBrain/` | 原資料・出典・知識と専用Git履歴 | 製品の説明文書や一時試験と区別し、私有原本・実エージェント命令を保持する |
| `models/` | Qwen GGUFと既存のfallback候補 | 実モデルは`config/llama-settings.json`の`model_path`を優先。参照不明の複製は削除しない |
| `bin/llama-b10809-cuda12.4/` | 実行中サービスが使うllama.cppとCUDA依存 | 再取得用ZIPと違い、現役の実行ファイル／DLLは保持する |
| `.venv/`、設定で指定したPython runtime | Python依存 | 実起動設定が指す依存を保持。第三者ライセンスや依存の原文資料は翻訳で改変しない |
| `logs/`、`output/audit/` | 運用監査・配備証跡・必要な復元資料 | 一時試験結果と区別する。過去の成功記録だけで現在の稼働版を断定しない |
| `D:\LocalBrain\embedding-models/` | CPU Embedding用GGUF | Qwen本体とは用途が異なる。存在と起動参照を照合して保持する |
| `D:\LocalBrain\models/` | 過去から保持するモデルコピー | 現用設定・fallback・読取不能な起動参照を解消できない場合は保留する |

## 保護された稼働コード

| 場所 | 役割 | 監査時点の扱い |
|---|---|---|
| `C:\ProgramData\LocalBrainServer\releases\55a206943b43cf2041742049b7265333dff91ae8` | 現役の固定コード・Web asset | 認証済みhealth/readinessのsourceと起動設定を照合。直接編集しない |
| `C:\ProgramData\LocalBrainServer\chat-ocr-activations\slack-admission-20261026-55a206943b43-v1` | Gateway／Chatの固定entry・spec・activation GO | 2つのSYSTEMタスクが同じreleaseへ結び付く。文書翻訳や整理で変更しない |
| `C:\ProgramData\LocalBrainServer\releases\519810c13dfe049802d5b2cd16fd0f6144ca982e` | 以前の固定release | 保護領域の読取制約と過去のbindingが残るため保持。未参照と断定しない |
| `C:\ProgramData\LocalBrainServer\releases\dcc046a157937de9816842daf18a810bc6e1ab0c` | 以前のGateway専用release | 旧pointerや履歴証拠との関係が残るため保持。現在の起動元と混同しない |
| `gateway_entry.py`、`gateway-current.json` | 初期のGateway専用entry／pointer | 現役の2-role specとは別。旧pointerのsourceだけから稼働版を判断しない |
| `chat-ocr-attempt-*`、`chat-ocr-uac-*`、`slack-admission-uac-*` | 本番操作の実receipt | 使用済みでも正式な証拠。承認やworkerの再実行には使わない |

通常権限で見えないSYSTEMのコマンド行、Chatタスク定義、保護releaseの全文は「未確認」と記録します。今回、ACL変更・追加UAC・サービス操作で読取制約を解消していません。現行process identity、認証済みsource、利用可能な起動設定と実配備receiptを照合し、保護領域は削除しません。

## 作業・受渡し・バックアップ

| 場所 | 用途 | 整理方針 |
|---|---|---|
| `C:\Users\USERNAME\Documents\Codex\2026-10-01\gen\work/` | 候補、隔離試験、固定配備packet、作業監査 | 統合済みclean worktreeと終了済み公開fixtureだけを条件付き整理。固定packet・最終receipt・元資料は保持 |
| 同じ親の`outputs/` | 最終報告と正式な試験・監査結果 | 一時ファイルとして扱わず保持 |
| `G:\share\LocalBrain-*` | 承認済みのPC間受渡し・review／配備証拠 | 正式証拠と重なるため保持。今回、新しい共有コピーは作らない |
| `G:\LocalBrain-Backups/` | 必要な保全資料の集約先 | モデル等GitHubにない資産、Server snapshot、保留配備のpreimage、監査indexを保持 |
| `F:\LocalBrain-Backups/` | 以前の候補場所 | 今回は存在を確認できなかった。別の無関係なF/Gデータへ削除範囲を広げない |

## 今回の整理結果

統合済み・clean・未追跡／ignoredファイルなしの旧worktree3件と、終了した公開fixture6箇所、現役binとの全entry hash一致を確認したdownload ZIP2件を整理しました。合計11対象、393ファイル、647,930,256 bytesです。削除前一覧、file hash、理由、削除開始／完了台帳は製品Git外の今回の作業監査へ保存しています。

使用中・未知・lock中・更新あり・再解析ポイントありなら削除を止めます。モデルの大きさだけを理由に削除せず、security／cancel／冪等性の回帰試験のsourceも維持しています。本番サービス停止・再起動・モデル起動、DB／認証／証明書／ACLの変更は行いません。

## 日本語文書の探し方

このsourceブランチの利用者・運用者向け説明を日本語化しています。code block、コマンド、URL、ファイル名、識別子は保持し、英語見出しの旧リンクを明示anchorで維持します。過去候補の説明にある「未配備」「未マージ」等は、その文書の当時の範囲を示すもので、翻訳を機に実績を書き換えません。

固定release／packet／receiptのMarkdown原文は変更しません。日本語の対訳・解説は製品Git外の今回の監査docs領域へ保存し、原文のpathとSHA256を対応付けます。対訳と、経緯や条件を整理した日本語解説は区別します。原文が公開sourceと完全一致するものは、その正確な版に対する日本語訳を共有して重複を避けます。機械fixture、モデルへの実命令、私有の原本、第三者ライセンスの扱いは[日本語文書と原文保持の方針](DOCUMENTATION_JA.md)を参照してください。
