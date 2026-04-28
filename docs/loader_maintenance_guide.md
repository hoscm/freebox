# freeBox Loader — メンテナンス・リリース運用ガイド

**対象読者:** freeBox Loader を管理・運用する開発者  
**ドキュメントバージョン:** v1.0.0  

---

## 1. このドキュメントについて

このドキュメントでは freeBox Loader の以下の運用手順を説明します。

- バージョン管理とリリース手順
- 互換性設計の方針
- 本番環境（hsBox）へのデプロイ手順
- 設定ファイルの管理
- ログの確認とトラブルシューティング

---

## 2. ファイル構成

### 2-1. リポジトリ構成（主要部分）

```
freebox/
  loader/
    version.txt                  ← Loader のバージョン情報
    run.sh                       ← hsBox へのインストールスクリプト
    conf/
      freebox.conf               ← Apache2 プロキシ設定
      freebox.service            ← systemd サービス定義
      freebox_config.ini.template ← 設定ファイルのテンプレート
    server/
      box_webserver.py           ← Loader 本体
      merge_config.py            ← 設定マージユーティリティ
      favicon.ico
    www/
      index.php                  ← hsBox タブ連携用
  tools/
    make_hbx.py                  ← .hbx ビルドツール
  docs/
    index.json                   ← 公式モジュールインデックス
```

### 2-2. hsBox 上の配置（インストール後）

```
/home/hsbox/freebox/             ← Loader の作業ディレクトリ
  box_webserver.py
  merge_config.py
  freebox_config.ini
  plugins/
    atomcam2.py
    atomcam2/
      atomcam2_config.ini
  data/
    index_cache.json

/etc/systemd/system/freebox.service
/etc/apache2/conf-enabled/freebox.conf
/home/hsbox/www/freebox/index.php
```

---

## 3. バージョン管理

### 3-1. バージョン番号の方針

freeBox Loader は [Semantic Versioning](https://semver.org/lang/ja/) （semver）に従います。

```
MAJOR.MINOR.PATCH
  │      │     └─ バグ修正・後方互換あり
  │      └────── 機能追加・後方互換あり
  └──────────── 後方互換性のない変更
```

**v1 リリース後の方針:**

| 変更の種類 | バージョン上げ方 | 例 |
|-----------|--------------|-----|
| セキュリティ修正 | PATCH | 1.0.0 → 1.0.1 |
| バグ修正 | PATCH | 1.0.0 → 1.0.1 |
| 新しい API エンドポイント追加（後方互換） | MINOR | 1.0.0 → 1.1.0 |
| Plugin インターフェースの変更（後方非互換） | MAJOR | 1.0.0 → 2.0.0 |
| run.sh の手順変更（再インストールが必要） | MINOR 以上 | 1.0.0 → 1.1.0 |

### 3-2. version.txt の管理

`freebox/loader/version.txt` にバージョン情報を記述します。

**正規フォーマット（5行形式）:**

```
freebox
<適用可能な最低ベースビルド番号>
<適用可能な最古の hsBox バージョン>
<適用可能な最新の hsBox バージョン>
<このパッチ自体のバージョン>
```

**現行値（v1.0.0）の例:**

```
freebox
269
1.03.01.01
1.03.02.99
1.0.0
```

| 行 | フィールド | 意味 |
|----|---------|------|
| 1 | pdname | hsBox がアクセスするコンテキスト名 |
| 2 | obb | 適用可能な最低ベースビルド番号 |
| 3 | obv | 適用可能な最古の hsBox バージョン |
| 4 | nwv | 適用可能な最新の hsBox バージョン（ST ごとに更新） |
| 5 | thisv | このパッチ自体のバージョン（semver） |

### 3-3. バージョンアップ手順

1. `freebox/loader/version.txt` の 5 行目（`thisv`）を新バージョンに更新
2. `nwv`（4行目）を動作確認済みの最新 hsBox バージョンに更新
3. 変更内容を `git commit`
4. GitHub で新しい Release を作成（タグ: `v1.x.x`）
5. `freebox-base.hbx` をビルドして Release のアセットに添付
6. `docs/index.json` の Loader エントリを更新（`version`・`release_tag`）

---

## 4. 互換性設計

### 4-1. Plugin インターフェースの互換性

Plugin は以下の 2 つのメソッドを持つクラスとして実装します。

```python
class Plugin:
    def can_handle(self, path: str) -> bool: ...
    def handle(self, req) -> Response: ...
```

**v1 の保証:**

- `can_handle(path: str) -> bool` のシグネチャは変更しない
- `handle(req) -> Response` のシグネチャは変更しない
- `RequestWrapper` のプロパティ（`method`, `path`, `query`, `headers`, `read_body()`）は削除しない
- `Response(status, body, content_type)` のコンストラクタは変更しない

**後方互換を破る変更（MAJOR バージョンアップ）:**

- `can_handle` / `handle` のシグネチャ変更
- `RequestWrapper` の既存プロパティの廃止
- `register_schedule` のインターフェース変更

### 4-2. API エンドポイントの互換性

v1 で定義した以下の API エンドポイントは v1 系の間は削除・変更しません。

| メソッド | エンドポイント | 用途 |
|---------|-------------|------|
| GET | `/api/modules` | モジュール一覧 |
| POST | `/api/module/install` | インストール |
| POST | `/api/module/uninstall` | 削除 |
| POST | `/api/module/upload` | アップロード |
| POST | `/api/index/refresh` | インデックス更新 |
| GET | `/api/status` | ステータス確認 |

### 4-3. index.json のフィールド互換性

`docs/index.json` の既存フィールドは削除・型変更しません。  
新しいフィールドを追加する場合は、旧バージョンの Loader がフィールドを無視する実装になっているため後方互換があります。

---

## 5. リリース手順

### 5-1. リリース前チェックリスト

- [ ] `freebox/loader/version.txt` の `thisv`（5行目）が正しいバージョンか
- [ ] `docs/index.json` のテストモジュール（testmodule01〜03）が削除されているか
- [ ] `docs/index.json` の atomcam2 の `release_tag` が正しいタグ名か
- [ ] `freebox/loader/server/box_webserver.py` にデバッグコード・テスト用コードが残っていないか
- [ ] SEC-01〜SEC-05 のセキュリティチェックをパスしているか
  - SEC-01: ローカル絶対パスが含まれていないか
  - SEC-02: プライベート IP アドレスが含まれていないか
  - SEC-03: シークレット・トークンが含まれていないか
  - SEC-04: テストデータ（`.hbx`, `.ini`, テスト JSON）がコミットされていないか
  - SEC-05: 個人名・社内情報が含まれていないか
- [ ] `freebox-base.hbx` のビルドが成功し、全必須ファイルが収録されているか

### 5-2. freebox-base.hbx のビルド

```bash
# freebox/ リポジトリの親ディレクトリで実行
python freebox/tools/make_hbx.py --type loader
```

出力先: `freebox-base.hbx`（親ディレクトリ直下）

### 5-3. atomcam2.hbx のビルド

```bash
python freebox/tools/make_hbx.py atomcam2 freebox/modules/atomcam2/atomcam2.py 1.0.0 freebox/dist/
```

### 5-4. GitHub コミットと Release 作成

```bash
git add -A
git commit -m "v1.0.0: Release"
git push origin main
```

GitHub Web UI で Release を作成:

1. 「Releases」→「Draft a new release」
2. タグ: `v1.0.0`
3. アセットに `freebox-base.hbx` と `freebox/dist/atomcam2.hbx` を添付

---

## 6. 本番デプロイ手順

### 6-1. 初回インストール

`docs/getting_started.md` を参照してください。

### 6-2. バージョンアップ（上書きインストール）

```bash
# 1. 新しい freebox-base.hbx を hsBox に転送
scp freebox-base.hbx root@<hsBox の IP>:/tmp/

# 2. hsBox に SSH
ssh root@<hsBox の IP>

# 3. 展開してインストール
mkdir -p /home/hsbox/ztmp
cp /tmp/freebox-base.hbx /home/hsbox/ztmp/
cd /home/hsbox/ztmp
unzip -o freebox-base.hbx
bash run.sh
```

`run.sh` は設定ファイル（`freebox_config.ini`）を `merge_config.py` でマージするため、既存の設定値は保持されます。

---

## 7. 設定ファイルの管理

### 7-1. freebox_config.ini の場所

```
/home/hsbox/freebox/freebox_config.ini
```

### 7-2. 主要な設定項目

```ini
[loader]
index_url = https://raw.githubusercontent.com/hoscm/freebox/main/docs/index.json
index_cache_ttl = 3600

[status]
nas_mount_point = /mnt/nas
```

### 7-3. 設定の変更方法

Manager UI の Settings タブから変更できます。または hsBox に SSH して直接編集することも可能です。

```bash
ssh root@<hsBox の IP>
nano /home/hsbox/freebox/freebox_config.ini
systemctl restart freebox
```

---

## 8. ログの確認

### 8-1. サービスログ

```bash
# 最新 100 行を確認
journalctl -u freebox -n 100

# リアルタイムで確認
journalctl -u freebox -f
```

### 8-2. よくあるログメッセージ

| ログメッセージ | 意味 | 対処 |
|-------------|------|------|
| `freebox started` | 正常起動 | — |
| `index refresh: ok` | インデックス取得成功 | — |
| `index refresh: error` | インデックス取得失敗 | インターネット接続とindex_urlを確認 |
| `deploy: download_failed` | hbx ダウンロード失敗 | GitHub Release のタグと index.json の release_tag を確認 |
| `scheduler: job timeout` | スケジュールジョブがタイムアウト | Plugin の処理を確認 |

### 8-3. サービスのステータス確認

```bash
# サービス状態
systemctl status freebox

# API で確認
curl -s http://127.0.0.1:9009/api/status | python3 -m json.tool
```

---

## 9. トラブルシューティング

### 9-1. Loader が起動しない

```bash
# サービスログを確認する
journalctl -u freebox -n 50 --no-pager

# 手動起動でエラーを確認する
cd /home/hsbox/freebox
python3 box_webserver.py
```

### 9-2. Manager UI にアクセスできない

1. `systemctl status freebox` でサービスが動作していることを確認する
2. `systemctl status apache2` で Apache2 が動作していることを確認する
3. `/etc/apache2/conf-enabled/freebox.conf` が正しく配置されていることを確認する

```bash
curl -s http://127.0.0.1:9009/api/status
```

ポート 9009 が応答すれば Loader は動作しています。

### 9-3. モジュールのインストールが失敗する

| エラー | 原因と対処 |
|--------|---------|
| `download_failed` | `index.json` の `release_tag` と GitHub Release のタグ名が一致しているか確認 |
| `invalid_hbx_format` | `.hbx` の中身を確認（`unzip -l <file>.hbx`） |
| `already_installed` | Manager UI で Remove してから再インストール |
| `invalid_plugin_id` | モジュール ID が正規表現 `^[a-z0-9][a-z0-9\-]*[a-z0-9]$` に準拠しているか確認 |

---

## 10. index.json の管理

### 10-1. 正式リリース時の index.json

テストモジュール（testmodule01〜03）を削除し、atomcam2 のみ残します。

```json
[
  {
    "id": "atomcam2",
    "name": "ATOMCAM2 Capture",
    "description": "ATOMCAM2 から定期的にキャプチャ画像を NAS へ保存します。",
    "status": "restricted",
    "version": "1.0.0",
    "release_tag": "v1.0.0",
    "author": "hoscm",
    "repository": "https://github.com/hoscm/freebox",
    "plugin_file": "atomcam2.py"
  }
]
```

### 10-2. サードパーティモジュールの登録

サードパーティ開発者からの登録申請を受け付けた場合は、以下を確認してから `docs/index.json` に追加します。

- [ ] `id` が正規表現に準拠しているか
- [ ] `repository` が公開リポジトリか
- [ ] `release_tag` に対応する Release とアセット（`.hbx`）が存在するか
- [ ] `status` が適切か（`private` は記載不可）

---

*本ドキュメントは freeBox Loader v1.0.0 に基づきます。*
