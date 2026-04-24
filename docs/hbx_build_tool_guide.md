# freeBox Module .hbx ビルドツール利用手順書

**ドキュメント名:** hbx_build_tool_guide.md  
**対象ツール:** `tools/make_hbx.py`  
**対象読者:** freeBox Module を開発・配布したいサードパーティ開発者  

---

## 1. .hbx ファイルとは

`.hbx` は freeBox Loader がモジュールを配布・インストールするためのパッケージ形式です。内部構造は ZIP ファイルで、以下のファイルを含みます：

```
<module_id>.hbx（ZIPファイル）
  ├── <module_id>.py   ← Plugin 実装ファイル（必須）
  └── version.txt      ← バージョン情報ファイル（必須）
```

### version.txt の仕様

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

## 2. ビルドツールの概要

`tools/make_hbx.py` は `.hbx` ファイルを生成するコマンドラインツールです。

**動作要件:**
- Python 3.10 以上

---

## 3. 基本的な使い方

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

**出力:** `dist/mymodule.hbx`

---

## 4. Plugin ファイルの実装

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

---

## 5. index.json への登録

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

## 6. GitHub Releases への配布手順

1. `python tools/make_hbx.py mymodule ./mymodule.py 1.0.0 ./dist/` でビルド
2. GitHub の対象リポジトリで新しい Release を作成（タグ名は `index.json` の `release_tag` と一致させる）
3. `dist/mymodule.hbx` を Release のアセットとして添付
4. `docs/index.json` にエントリを追加して push（またはユーザーの `index_url` に合わせて別リポジトリで管理）

---

## 7. ウォークスルー（ST検証手順）

以下の手順を実際に実行して、手順書の正確性を検証します。

### 前提条件
- Python 3.10 以上がインストール済み

### 手順

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

---

## 8. トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `Error: Invalid module_id` | モジュール ID の形式が不正 | ID が `^[a-z0-9][a-z0-9\-]*[a-z0-9]$` に準拠しているか確認 |
| `Error: Invalid version` | バージョン番号が `X.Y.Z` 形式でない | `1.0.0` の形式で指定する |
| `Error: Plugin file not found` | 指定した .py ファイルが存在しない | ファイルパスを確認する |
| `invalid_version_txt`（Loader側エラー） | `version.txt` の1行目が不正な ID | ビルド時の `module_id` を確認する |
| `download_failed`（Loader側エラー） | GitHub Release タグ名が `release_tag` と不一致 | `index.json` の `release_tag` と GitHub Release のタグ名を一致させる |
| `already_installed`（Loader側エラー） | 同一 ID のモジュールがインストール済み | Manager UI から Remove した後に再 Deploy する |
| `invalid_hbx_format`（Loader側エラー） | `.hbx` に Plugin ファイルが含まれていない | `module_id` と `plugin_file` のファイル名が一致しているか確認 |

---

*ST にて本書に従ったウォークスルー形式のドキュメントテストを実施すること。*
