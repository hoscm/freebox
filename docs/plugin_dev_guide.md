# freeBox Plugin 開発ガイド

**対象読者:** freeBox 対応モジュールを開発・配布したいサードパーティ開発者  
**前提知識:** Python の基本的な知識  

---

## 1. Plugin とは

freeBox Loader は、`plugins/` ディレクトリに配置された Python ファイルを Plugin として動的にロードします。各 Plugin は独自のエンドポイントを持ち、ブラウザからアクセスできる UI と API を提供できます。

---

## 2. Plugin の基本構造

Plugin ファイルは、`Plugin` という名前のクラスを持つ Python ファイルです。

```python
class Plugin:

    def can_handle(self, path: str) -> bool:
        """このプラグインが処理すべきパスかどうかを返す"""
        return path.startswith("/myplugin")

    def handle(self, req) -> "Response":
        """リクエストを処理して Response を返す"""
        from box_webserver import Response
        body = b"Hello from myplugin!"
        return Response(200, body, "text/plain; charset=utf-8")
```

**必須メソッド:**

| メソッド | 役割 |
|---------|------|
| `can_handle(path)` | パスを処理すべきかを判定する |
| `handle(req)` | リクエストを処理して Response を返す |

**任意メソッド:**

| メソッド | 役割 |
|---------|------|
| `register_schedule(scheduler)` | スケジューラにジョブを登録する（定期実行が必要な場合） |

---

## 3. can_handle の実装

`can_handle` は Plugin の URL コンテキストを定義します。Plugin のコンテキスト名は Plugin ID と一致させることを推奨します。

```python
def can_handle(self, path: str) -> bool:
    # /myplugin および /myplugin/ 以下のすべてのパスを処理する
    return path.startswith("/myplugin")
```

**注意事項:**

- 予約済みコンテキスト（`/loader`, `/index`, `/api`, `/status`, `/manager`）は使用できません
- 他の Plugin と同じプレフィックスを使用しないでください

---

## 4. Request オブジェクト

`handle(req)` の引数 `req` は `RequestWrapper` のインスタンスです。以下のプロパティとメソッドを使用できます。

| プロパティ / メソッド | 型 | 内容 |
|----------------------|-----|------|
| `req.method` | `str` | HTTPメソッド（`"GET"` または `"POST"`） |
| `req.path` | `str` | リクエストパス（例: `/myplugin/status`） |
| `req.query` | `dict` | クエリパラメータ（例: `{"key": ["val"]}`） |
| `req.headers` | `dict` 相当 | リクエストヘッダ |
| `req.read_body()` | `bytes` | リクエストボディを読み取る（POST 時） |

**パス解析の例:**

```python
def handle(self, req) -> "Response":
    from box_webserver import Response
    # クエリを除いたパスを取得し、末尾スラッシュを除去する
    path = req.path.split("?")[0].rstrip("/")

    if req.method == "GET" and path == "/myplugin":
        # トップ画面
        return Response(200, b"<h1>My Plugin</h1>", "text/html; charset=utf-8")

    if req.method == "GET" and path == "/myplugin/status":
        # ステータス API
        import json
        body = json.dumps({"status": "ok"}, ensure_ascii=False).encode("utf-8")
        return Response(200, body, "application/json; charset=utf-8")

    return Response(404, b"Not Found", "text/plain")
```

---

## 5. Response オブジェクト

`handle` は必ず `Response` オブジェクトを返します。

```python
from box_webserver import Response

Response(
    status=200,           # HTTP ステータスコード（整数）
    body=b"...",          # レスポンスボディ（bytes）
    content_type="text/html; charset=utf-8",  # Content-Type ヘッダ
)
```

**よく使う Content-Type:**

| 内容 | Content-Type |
|------|-------------|
| HTML | `text/html; charset=utf-8` |
| JSON | `application/json; charset=utf-8` |
| プレーンテキスト | `text/plain; charset=utf-8` |

**注意:** `Response` は `from box_webserver import Response` でインポートします。  
スタンドアロン実行（テスト）のために ImportError 時のフォールバックを用意することを推奨します。

```python
try:
    from box_webserver import Response
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class Response:
        status: int = 200
        body: bytes = b""
        content_type: str = "text/plain"
```

---

## 6. POST リクエストの処理

JSON ボディを受け取る場合:

```python
def handle(self, req) -> "Response":
    from box_webserver import Response
    import json

    path = req.path.split("?")[0].rstrip("/")

    if req.method == "POST" and path == "/myplugin/config":
        try:
            body_bytes = req.read_body()
            params = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return Response(400, b'{"error":"invalid_json"}', "application/json")

        # params を処理する
        value = params.get("key", "")
        result = json.dumps({"message": "保存しました"}, ensure_ascii=False).encode("utf-8")
        return Response(200, result, "application/json; charset=utf-8")
```

---

## 7. 設定ファイルの読み書き

Plugin 固有の設定は `configparser` を使って INI ファイルで管理することを推奨します。

### 設定ファイルのパス

```python
import os
import configparser

_PLUGIN_DIR  = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_PLUGIN_DIR, "myplugin_config.ini")
```

`__file__` は plugins/ ディレクトリ内の Plugin ファイルを指すため、設定ファイルも同じディレクトリに作成されます。

### 設定の読み込み

```python
def _load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    # デフォルト値を設定する
    cfg["section"] = {
        "key": "default_value",
    }
    if os.path.exists(_CONFIG_PATH):
        cfg.read(_CONFIG_PATH, encoding="utf-8")
    return cfg
```

### 設定の保存

```python
def _save_config(cfg: configparser.ConfigParser) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        cfg.write(f)
```

### 設定ファイルのテンプレート

配布する `.hbx` に `<plugin_id>_config.ini.template` を含め、`run.sh` で初回インストール時にコピーします（詳細は「インストールスクリプト実装ガイド」を参照）。

---

## 8. NAS の利用

NAS が接続されているかを確認するには `is_nas_available` を使用します。

```python
try:
    from box_webserver import is_nas_available
except ImportError:
    def is_nas_available(mount_point: str) -> bool:
        try:
            import os
            return os.path.ismount(mount_point) and os.access(mount_point, os.W_OK)
        except Exception:
            return False

# 使用例
if is_nas_available("/mnt/nas"):
    # NAS への書き込み処理
    pass
else:
    # NAS 未接続時の縮退処理
    pass
```

**設計原則:** NAS 未接続時はエラーを発生させず、処理をスキップしてステータスを記録する縮退動作を実装してください。

---

## 9. 定期実行ジョブの登録

定期的に処理を実行したい場合は `register_schedule` を実装します。

```python
def register_schedule(self, scheduler) -> None:
    scheduler.schedule(
        name="myplugin_job",       # ジョブ名（英数字とハイフン）
        interval_minutes=10,       # 実行間隔（分）
        func=self.my_job,          # 実行する関数（引数なし）
        timeout_minutes=5,         # タイムアウト（分）
    )

def my_job(self) -> None:
    """スケジューラから呼び出されるジョブ関数"""
    try:
        # 定期処理の実装
        pass
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("ジョブ例外: %s", e)
```

**注意事項:**
- `func` は引数なしで呼び出されます。インスタンスメソッドを使う場合は `self.method_name` の形式で渡します
- ジョブは別スレッドで実行されます
- 例外は必ずキャッチし、ジョブの失敗がサーバー全体に影響しないようにしてください
- スケジューラは Loader 起動時に `register_schedule` を呼び出します

---

## 10. HTML UI の実装

Plugin の UI は Python の文字列として HTML を生成します。ユーザー入力や設定値を HTML に埋め込む場合は必ずエスケープしてください。

```python
def _escape_html(self, s: str) -> str:
    """HTML 特殊文字をエスケープする"""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))

def _render_html(self) -> str:
    safe_value = self._escape_html(self._some_value)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>My Plugin</title></head>
<body>
<h1>My Plugin</h1>
<p>値: {safe_value}</p>
</body>
</html>
"""
```

### JavaScript からの API 呼び出し

Plugin の URL コンテキストは `/freebox/<plugin_id>/` 以下に配置されます。

```javascript
// 設定の取得
const res = await fetch('/freebox/myplugin/config');
const data = await res.json();

// 設定の保存
await fetch('/freebox/myplugin/config', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ key: 'value' }),
});
```

---

## 11. 実装例: atomcam2 Plugin

atomcam2 は ATOMCAM2 カメラから定期的にキャプチャ画像を NAS へ保存する Plugin です。  
設定画面・手動キャプチャ・定期実行・NAS チェックを網羅した参照実装として使用できます。

**エンドポイント構成:**

| パス | メソッド | 内容 |
|------|---------|------|
| `/atomcam2/` | GET | 設定フォーム付きステータス画面（HTML） |
| `/atomcam2/status` | GET | 最終キャプチャ状態（JSON） |
| `/atomcam2/config` | GET | 現在の設定値（JSON） |
| `/atomcam2/capture` | POST | 手動キャプチャの実行（JSON） |
| `/atomcam2/config` | POST | 設定値の保存（JSON） |

**実装のポイント:**

- `can_handle` でコンテキスト `atomcam2` を宣言
- `handle` 内でパスとメソッドを組み合わせてルーティング
- 設定は `atomcam2_config.ini` に保存し、`POST /atomcam2/config` で更新
- NAS 未接続時はキャプチャをスキップし、ステータスに `skip_nas` を記録
- ジョブ関数 `capture` は例外を必ずキャッチ

ソースコードは `modules/atomcam2/atomcam2.py` を参照してください。

---

## 12. Plugin の配布

Plugin を配布する場合は `.hbx` パッケージ形式を使用します。  
ビルド方法とパッケージ構造については「hbx ビルドツール利用手順書」（`hbx_build_tool_guide.md`）を参照してください。  
インストールスクリプトの実装については「インストールスクリプト実装ガイド」（`run_sh_guide.md`）を参照してください。

---

## 13. Plugin ファイル構成規約（G-21 追加）

### 13-1. 構造

Plugin のファイルは以下の構造で配置してください。

```
plugins/
  <id>.py              ← エントリポイント（必須・このファイルのみ plugins/ 直下に置く）
  <id>/                ← サブディレクトリ（Plugin 専用ファイル群）
    <id>_config.ini    ← 設定ファイル
    （その他 Plugin が管理するファイル）
```

### 13-2. サブディレクトリを使う理由

freeBox Loader の Uninstall 処理は以下の 2 ステップで実行されます。

1. `plugins/<id>.py` を削除
2. `plugins/<id>/` ディレクトリが存在する場合は再帰削除

サブディレクトリに設定ファイルやデータを置くことで、Uninstall 時に確実にクリーンアップされます。

### 13-3. コード中のパス設定

`atomcam2` の実装例と同じように、パスは `__file__` を基点に構築してください。

```python
import os

_PLUGIN_DIR  = os.path.dirname(os.path.abspath(__file__))
_SUBDIR      = os.path.join(_PLUGIN_DIR, "myplugin")        # plugins/myplugin/
_CONFIG_PATH = os.path.join(_SUBDIR, "myplugin_config.ini") # plugins/myplugin/myplugin_config.ini

# 起動時・設定保存時にサブディレクトリを作成する
os.makedirs(_SUBDIR, exist_ok=True)
```

### 13-4. run.sh でのサブディレクトリ作成

```bash
PLUGIN_SUBDIR="${PLUGINS_DIR}/myplugin"
CONFIG_INI="${PLUGIN_SUBDIR}/myplugin_config.ini"

# エントリポイントを配置
cp -f "${ZTMP}/myplugin.py" "${PLUGINS_DIR}/myplugin.py"
chmod 644 "${PLUGINS_DIR}/myplugin.py"

# サブディレクトリを作成し、設定ファイルを配置
mkdir -p "${PLUGIN_SUBDIR}"
if [ ! -f "${CONFIG_INI}" ]; then
    cp -f "${ZTMP}/myplugin_config.ini.template" "${CONFIG_INI}"
else
    python3 "${FREEBOX_DIR}/merge_config.py" "${CONFIG_INI}" "${ZTMP}/myplugin_config.ini.template"
fi
chmod 640 "${CONFIG_INI}"
chown hsbox:hsbox "${CONFIG_INI}" 2>/dev/null || true
```

---

*本ドキュメントは freeBox Loader v1 の実装に基づきます。*
