# freeBox Module .hbx ビルドツール利用手順書

**ドキュメント名:** hbx_build_tool_guide.md  
**対象ツール:** `tools/make_hbx.py`  
**対象読者:** freeBox Module を開発・配布したいサードパーティ開発者、freeBox Loader 自体のビルドを行う開発者  

---

## 1. .hbx ファイルとは

`.hbx` は freeBox Loader がモジュールを配布・インストールするためのパッケージ形式です。内部構造は ZIP ファイルです。

**Plugin モジュールの .hbx 構造:**

```
<module_id>.hbx（ZIPファイル）
  ├── <module_id>.py   ← Plugin 実装ファイル（必須）
  └── version.txt      ← バージョン情報ファイル（必須）
```

**freeBox Loader 本体の .hbx（freebox-base.hbx）構造:**

```
freebox-base.hbx（ZIPファイル）
  ├── version.txt
  ├── run.sh                          ← インストールスクリプト（実行権限付き）
  ├── conf/freebox.conf
  ├── conf/freebox.service
  ├── conf/freebox_config.ini.template
  ├── server/box_webserver.py
  ├── server/merge_config.py
  ├── server/favicon.ico
  └── www/index.php
```

---

## 2. ビルドツールの概要

`tools/make_hbx.py` は `.hbx` ファイルを生成するコマンドラインツールです。

**動作要件:**
- Python 3.10 以上

**2 つの動作モード:**

| モード | 用途 | コマンド |
|--------|------|---------|
| `module`（デフォルト） | Plugin モジュールのビルド | `python tools/make_hbx.py <args>` |
| `loader` | freeBox Loader 本体のビルド | `python tools/make_hbx.py --type loader` |

---

## 3. Plugin モジュールのビルド（module モード）

### 3-1. ツールの取得

```bash
git clone https://github.com/hoscm/freebox.git
cd freebox
```

### 3-2. ビルドの実行

```bash
python tools/make_hbx.py <module_id> <plugin_file> <version> <output_dir>
```

| 引数 | 説明 | 例 |
|------|------|-----|
| `<module_id>` | モジュール ID（英小文字・数字・ハイフン） | `mymodule` |
| `<plugin_file>` | Plugin 実装ファイルのパス | `./mymodule.py` |
| `<version>` | バージョン番号 | `1.0.0` |
| `<output_dir>` | 出力先ディレクトリ（存在しない場合は作成） | `./dist/` |

**実行例:**

```bash
python tools/make_hbx.py mymodule ./mymodule.py 1.0.0 ./dist/
```

**出力例:**

```
Built: dist/mymodule.hbx
  module_id : mymodule
  version   : 1.0.0
  contents  : mymodule.py, version.txt
```

**version.txt の仕様（module モード）:**

```
<module_id>
<version>
```

例：

```
atomcam2
1.0.0
```

- 1行目: モジュール ID（`^[a-z0-9][a-z0-9\-]*[a-z0-9]$` に準拠）
- 2行目: バージョン番号（semver 推奨）

---

## 4. freeBox Loader 本体のビルド（loader モード）

### 4-1. ビルドの実行

`freebox/` リポジトリをクローンした親ディレクトリで実行します。

```bash
# デフォルト設定で実行（推奨）
python freebox/tools/make_hbx.py --type loader

# または freebox/ ディレクトリ内から実行する場合
cd freebox
python tools/make_hbx.py --type loader
```

| 引数 | 省略時のデフォルト | 説明 |
|------|-----------------|------|
| `--type loader` | — | loader モードで実行（必須フラグ） |
| `<src_dir>` | `freebox/loader/` | ビルド元ディレクトリ |
| `<output_file>` | `./freebox-base.hbx` | 出力ファイル名（拡張子 .hbx） |

**出力例:**

```
============================================================
freeBox Loader .hbx ビルド  [make_hbx.py --type loader]
============================================================
入力ディレクトリ : /path/to/freebox/loader
出力ファイル     : /path/to/freebox-base.hbx
--- 検証開始 ---
[OK] 必須ファイルの検証: すべて存在します
[OK] version.txt の検証: pdname=freebox, obb=269, obv=1.03.01.01, nwv=1.03.02.99, thisv=1.0.0
--- 検証完了 ---
============================================================
[完了] freebox-base.hbx  (126.8 KB)
============================================================
  thisv   : 1.0.0
収録ファイル (9 件):
  rw-r--r--  conf/freebox.conf
  rw-r--r--  conf/freebox.service
  rw-r--r--  conf/freebox_config.ini.template
  rwxr-xr-x  run.sh
  rw-r--r--  server/box_webserver.py
  rw-r--r--  server/favicon.ico
  rw-r--r--  server/merge_config.py
  rw-r--r--  version.txt
  rw-r--r--  www/index.php
============================================================
```

### 4-2. loader モードの必須ファイル

以下のファイルが `freebox/loader/` 内に存在しないとビルドが失敗します。

| ファイル | パス |
|---------|------|
| version.txt | `loader/version.txt` |
| run.sh | `loader/run.sh` |
| freebox.conf | `loader/conf/freebox.conf` |
| freebox.service | `loader/conf/freebox.service` |
| freebox_config.ini.template | `loader/conf/freebox_config.ini.template` |
| box_webserver.py | `loader/server/box_webserver.py` |
| merge_config.py | `loader/server/merge_config.py` |
| index.php | `loader/www/index.php` |

---

## 5. Plugin ファイルの実装

`.hbx` に含める Plugin ファイルは以下の形式で実装します：

```python
"""
mymodule.py  -  freeBox Plugin 実装サンプル
"""

class Plugin:
    def can_handle(self, path: str) -> bool:
        """このPluginが処理すべきパスかどうかを返す"""
        return path.startswith("/mymodule")

    def handle(self, req) -> "Response":
        """リクエストを処理してResponseを返す"""
        from box_webserver import Response
        body = b"Hello from mymodule!"
        return Response(200, body, "text/plain; charset=utf-8")
```

**注意事項:**
- クラス名は必ず `Plugin` にする
- `can_handle()` と `handle()` を実装する
- `handle()` は必ず `Response` オブジェクトを返す

詳細な Plugin 実装方法は `docs/plugin_dev_guide.md` を参照してください。

---

## 6. index.json への登録

`.hbx` を GitHub Releases に配布する場合、`docs/index.json` にエントリを追加します。

```json
{
  "id": "mymodule",
  "name": "My Module",
  "description": "サンプルモジュールの説明",
  "status": "restricted",
  "version": "1.0.0",
  "release_tag": "v1.0.0",
  "author": "Your Name",
  "repository": "https://github.com/YOUR_USER/YOUR_REPO",
  "plugin_file": "mymodule.py"
}
```

**フィールド説明:**

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `id` | ✅ | モジュール ID。`^[a-z0-9][a-z0-9\-]*[a-z0-9]$` に準拠 |
| `name` | ✅ | UI 表示名 |
| `description` | ✅ | UI 説明文 |
| `status` | ✅ | `public` / `restricted`（`private` は書けない） |
| `version` | ✅ | UI 表示用バージョン番号（semver 推奨） |
| `release_tag` | ✅ | GitHub Release のタグ名（`version` と異なってもよい） |
| `author` | ✅ | 作成者名 |
| `repository` | ✅ | `.hbx` を配置した GitHub リポジトリのベース URL |
| `plugin_file` | ✅ | プラグインファイル名（ファイル名のみ・相対パス禁止） |

**hbx ダウンロード URL の構築規則:**

```
{repository}/releases/download/{release_tag}/{id}.hbx
```

---

## 7. GitHub Releases への配布手順

1. `python tools/make_hbx.py mymodule ./mymodule.py 1.0.0 ./dist/` でビルド
2. GitHub の対象リポジトリで新しい Release を作成（タグ名は `index.json` の `release_tag` と一致させる）
3. `dist/mymodule.hbx` を Release のアセットとして添付
4. `docs/index.json` にエントリを追加して push（またはユーザーの `index_url` に合わせて別リポジトリで管理）

---

## 8. ウォークスルー（動作確認手順）

以下の手順を実際に実行して、ビルドツールの動作を確認します。

### 前提条件

- Python 3.10 以上がインストール済み

### Plugin モジュールのビルド確認

```bash
# 1. リポジトリをクローン
git clone https://github.com/hoscm/freebox.git
cd freebox

# 2. サンプル Plugin ファイルを作成
cat > /tmp/testplugin.py << 'EOF'
class Plugin:
    def can_handle(self, path):
        return path.startswith("/testplugin")
    def handle(self, req):
        from box_webserver import Response
        return Response(200, b"testplugin OK", "text/plain; charset=utf-8")
EOF

# 3. .hbx をビルド
python tools/make_hbx.py testplugin /tmp/testplugin.py 1.0.0 /tmp/

# 4. 確認
ls -la /tmp/testplugin.hbx
python -c "import zipfile; zf=zipfile.ZipFile('/tmp/testplugin.hbx'); print(zf.namelist())"
```

**期待される出力例:**

```
Built: /tmp/testplugin.hbx
  module_id : testplugin
  version   : 1.0.0
  contents  : testplugin.py, version.txt
['testplugin.py', 'version.txt']
```

### freeBox Loader 本体のビルド確認

```bash
# freebox/ リポジトリの親ディレクトリで実行
python freebox/tools/make_hbx.py --type loader
python -c "import zipfile; zf=zipfile.ZipFile('freebox-base.hbx'); print(sorted(zf.namelist()))"
```

**期待される出力（namelist の例）:**

```
['conf/freebox.conf', 'conf/freebox.service', 'conf/freebox_config.ini.template',
 'run.sh', 'server/box_webserver.py', 'server/favicon.ico', 'server/merge_config.py',
 'version.txt', 'www/index.php']
```

---

## 9. トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `Error: Invalid module_id` | モジュール ID の形式が不正 | ID が `^[a-z0-9][a-z0-9\-]*[a-z0-9]$` に準拠しているか確認（単一文字は不可） |
| `Error: Invalid version` | バージョン番号が `X.Y.Z` 形式でない | `1.0.0` の形式で指定する |
| `Error: Plugin file not found` | 指定した .py ファイルが存在しない | ファイルパスを確認する |
| `Error: Missing required file` (loader モード) | 必須ファイルが `loader/` 内に見つからない | §4-2 の必須ファイル一覧を確認する |
| `invalid_version_txt`（Loader側エラー） | `version.txt` の1行目が不正な ID | ビルド時の `module_id` を確認する |
| `download_failed`（Loader側エラー） | GitHub Release タグ名が `release_tag` と不一致 | `index.json` の `release_tag` と GitHub Release のタグ名を一致させる |
| `already_installed`（Loader側エラー） | 同一 ID のモジュールがインストール済み | Manager UI から Remove した後に再 Deploy する |
| `invalid_hbx_format`（Loader側エラー） | `.hbx` に Plugin ファイルが含まれていない | `module_id` と `plugin_file` のファイル名が一致しているか確認 |

---

*本ドキュメントは freeBox Loader v1.0.0 に基づきます。*
