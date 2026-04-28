# freeBox Module 開発ガイド

**対象読者:** freeBox 対応モジュールをゼロから開発・配布したいサードパーティ開発者  
**前提知識:** Python の基本的な知識、GitHub の基本操作  

---

## 1. 開発の全体像

freeBox Module の開発から配布までは以下の流れで進めます。

```
1. Plugin 実装（.py）
      ↓
2. インストールスクリプト実装（run.sh）
      ↓
3. .hbx パッケージのビルド（make_hbx.py）
      ↓
4. GitHub Release へのアップロード
      ↓
5. index.json への登録（freeBox 公式または自リポジトリ）
      ↓
6. Manager UI からインストール（Deploy）
```

各ステップの詳細は以下のドキュメントを参照してください。

| ドキュメント | 内容 |
|------------|------|
| `docs/plugin_dev_guide.md` | Plugin クラスの実装方法・API リファレンス |
| `docs/run_sh_guide.md` | インストールスクリプトの実装方法 |
| `docs/hbx_build_tool_guide.md` | .hbx パッケージのビルド方法 |

---

## 2. 開発環境の準備

### 2-1. リポジトリのクローン

```bash
git clone https://github.com/hoscm/freebox.git
cd freebox
```

### 2-2. ローカルテスト環境（任意）

Plugin は hsBox 上でテストしますが、Python の単体テストをローカルで行う場合は以下のダミーを使います。

```python
# テスト用スタブ（Python 3.10 以上）
from dataclasses import dataclass

@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/plain"

class DummyRequest:
    def __init__(self, method="GET", path="/", body=b""):
        self.method = method
        self.path = path
        self.query = {}
        self.headers = {}
        self._body = body
    def read_body(self):
        return self._body
```

---

## 3. Plugin を最速で作る（ミニマル実装）

### 3-1. プラグインファイル（hello.py）

```python
"""
hello.py  -  freeBox Plugin ミニマル実装例
"""

class Plugin:
    def can_handle(self, path: str) -> bool:
        return path.startswith("/hello")

    def handle(self, req) -> "Response":
        try:
            from box_webserver import Response
        except ImportError:
            from dataclasses import dataclass

            @dataclass
            class Response:
                status: int = 200
                body: bytes = b""
                content_type: str = "text/plain"

        return Response(
            200,
            b"<h1>Hello from freeBox!</h1>",
            "text/html; charset=utf-8",
        )
```

### 3-2. run.sh（最小構成）

```bash
#!/bin/bash
set -e
ZTMP="/home/hsbox/ztmp"
PLUGINS_DIR="/home/hsbox/freebox/plugins"

systemctl stop freebox 2>/dev/null || true
mkdir -p "${PLUGINS_DIR}"
cp -f "${ZTMP}/hello.py" "${PLUGINS_DIR}/hello.py"
chmod 644 "${PLUGINS_DIR}/hello.py"
systemctl start freebox

echo "[hello] インストール完了"
echo "[hello] アクセス URL: http://<hsBox の IP>/freebox/hello/"
```

### 3-3. ビルド

```bash
python tools/make_hbx.py hello ./hello.py 1.0.0 ./dist/
```

### 3-4. インストール確認

1. `dist/hello.hbx` を hsBox にコピーして展開 → `bash run.sh` でインストール
2. ブラウザで `http://<hsBox の IP>/freebox/hello/` を開く
3. "Hello from freeBox!" が表示されれば成功

---

## 4. AI を活用した Plugin 開発（推奨）

Claude などの AI アシスタントを使うと、Plugin の実装を効率よく進められます。

### 4-1. AI に伝えるべき情報

AI に Plugin 実装を依頼する際は、以下の情報を提示してください。

```
以下の仕様で freeBox Plugin を実装してください。

## Plugin ID
<my_plugin>

## 処理するエンドポイント
- GET  /my_plugin/           ← HTML ステータス画面
- GET  /my_plugin/status     ← JSON でステータスを返す
- POST /my_plugin/config     ← 設定値を保存する

## 設定項目（freebox_config.ini 管理）
- [my_plugin]
- target_host = ""           ← 対象ホスト名または IP
- interval_minutes = 10      ← 定期実行間隔（分）

## 定期実行
interval_minutes ごとに <処理内容> を実行する。
NAS 未接続時はスキップして status に skip_nas を記録する。

## 制約
- Python 3.10 以上で動作すること
- クラス名は Plugin
- NAS アクセスは is_nas_available() でガードする
- ジョブ例外は必ずキャッチすること
```

### 4-2. 生成コードのチェックポイント

AI が生成したコードを採用する前に以下を確認してください。

| チェック | 確認内容 |
|---------|---------|
| `can_handle` | 予約済みコンテキスト（`/loader`, `/api`, `/status`, `/manager`）を使っていないか |
| `handle` | ユーザー入力を HTML に直接埋め込んでいないか（XSS 対策） |
| ジョブ | 例外を try/except でキャッチしているか |
| 設定ファイルパス | `__file__` を基点にパスを構築しているか |
| `Response` | `from box_webserver import Response` を使っているか |

### 4-3. AI に run.sh も生成させる

Plugin 実装が完成したら、run.sh の生成も AI に依頼できます。

```
上記の Plugin のインストールスクリプト（run.sh）を実装してください。

## 条件
- 設定ファイルは /home/hsbox/freebox/plugins/my_plugin/ に配置
- 設定ファイルのテンプレートは my_plugin_config.ini.template として .hbx に含める
- 初回は template をコピー、更新時は merge_config.py でマージする
- run.sh は冪等に実装する（複数回実行しても同じ状態になる）

## 参照実装
freebox/modules/atomcam2/run.sh を参照してください。
```

---

## 5. Plugin の設計チェックリスト

実装前に以下を確認してください。

- [ ] Plugin ID が `^[a-z0-9][a-z0-9\-]*[a-z0-9]$` に準拠している（単一文字は不可）
- [ ] 予約済みコンテキスト（`loader` / `index` / `api` / `status` / `manager`）と衝突しない
- [ ] `can_handle` が自 Plugin のパスのみを処理する
- [ ] `handle` がすべてのパスに対して `Response` を返す（404 を含む）
- [ ] ユーザー入力を HTML に埋め込む場合、HTML エスケープを実装している
- [ ] スケジューラジョブを登録する場合、例外をキャッチして Loader のクラッシュを防いでいる
- [ ] 設定ファイルを使う場合、`__file__` 基点でパスを構築している
- [ ] NAS を使う場合、未接続時の縮退動作を実装している
- [ ] `run.sh` が冪等（複数回実行しても同じ状態）に実装されている
- [ ] `.hbx` のビルドが成功している

---

## 6. GitHub への公開手順

### 6-1. リポジトリ準備

Plugin を公開するための GitHub リポジトリを作成します。構成例：

```
my-freebox-plugin/
  my_plugin.py                  ← Plugin 実装
  run.sh                        ← インストールスクリプト
  my_plugin_config.ini.template ← 設定テンプレート
  version.txt                   ← バージョン情報（ビルドツールが自動生成）
  README.md                     ← 使い方の説明
```

### 6-2. .hbx ビルドとリリース

```bash
# tools/ ディレクトリに make_hbx.py をコピーして使用
python make_hbx.py my_plugin ./my_plugin.py 1.0.0 ./dist/

# GitHub Release を作成してアセット添付
# タグ名: v1.0.0
# アセット: dist/my_plugin.hbx
```

### 6-3. index.json の記述例

freeBox の公式 `docs/index.json` に追加するか、自リポジトリの `index.json` を別途管理します。

```json
{
  "id": "my_plugin",
  "name": "My Plugin",
  "description": "プラグインの説明をここに書く",
  "status": "restricted",
  "version": "1.0.0",
  "release_tag": "v1.0.0",
  "author": "Your Name",
  "repository": "https://github.com/YOUR_USER/my-freebox-plugin",
  "plugin_file": "my_plugin.py"
}
```

**status の選択基準:**

| status | 使う場面 |
|--------|---------|
| `public` | 誰でも安全に使えるモジュール |
| `restricted` | 設定が必要・一部の環境向け・動作条件がある |
| `private` | 配布しない・個人用・index.json には書かない |

---

## 7. バージョンアップ手順

### 7-1. Plugin ファイルを更新する

```bash
# バージョンを上げて再ビルド
python make_hbx.py my_plugin ./my_plugin.py 1.1.0 ./dist/
```

### 7-2. GitHub Release を作成する

新しいタグ（例: `v1.1.0`）で Release を作成し、`dist/my_plugin.hbx` を添付します。

### 7-3. index.json を更新する

`version` と `release_tag` を新しいバージョンに更新します。

```json
{
  "version": "1.1.0",
  "release_tag": "v1.1.0"
}
```

### 7-4. 設定ファイルに新しいキーを追加する場合

`my_plugin_config.ini.template` に新しいキーを追加し、バージョンアップ時に `merge_config.py` が既存の設定を保持しながら新しいキーを追加します。`run.sh` に `merge_config.py` の呼び出しが実装されていれば自動的に対応します。

---

## 8. 参照実装

atomcam2 Plugin は設定画面・手動実行・定期実行・NAS チェックを網羅した参照実装です。

| ファイル | 内容 |
|---------|------|
| `modules/atomcam2/atomcam2.py` | Plugin 実装（参照実装） |
| `modules/atomcam2/run.sh` | インストールスクリプト（参照実装） |
| `modules/atomcam2/atomcam2_config.ini.template` | 設定テンプレート |

---

*本ドキュメントは freeBox Loader v1.0.0 に基づきます。*
