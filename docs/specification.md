# freeBox 仕様概要

このドキュメントでは、freeBox の構成・動作・開発者向けの概要を説明します。

---

## 1. freeBox の構成要素

freeBox は以下の 2 つの構成要素で成り立ちます。

---

### 1-1. freeBox Loader

**概要**

hsBox 上で動作する Plugin 管理サーバーです。GitHub から Plugin モジュールを取得・インストール・管理する Web UI と API を提供します。

**主な機能**

- Plugin モジュールの一覧表示（GitHub のインデックスと連携）
- Plugin モジュールのインストール（Deploy）・削除（Uninstall）
- ローカル `.hbx` ファイルのアップロードによるインストール
- 内蔵スケジューラによる Plugin の定期実行
- 設定ファイルの管理

**動作環境**

- Python 3.10 以上
- hsBox（Debian/Ubuntu 系 Linux）
- Apache2 リバースプロキシ経由でアクセス

---

### 1-2. freeBox Plugin モジュール

**概要**

freeBox Loader にインストールして機能を追加するモジュールです。`.hbx` 形式でパッケージ化して配布します。

**特徴**

- 1 つの Plugin が 1 つの Python ファイルで構成されます
- GitHub Releases に `.hbx` ファイルを配置して配布できます
- インストール・削除は Manager UI から操作できます
- 定期実行（スケジューラ連携）が可能です

---

## 2. アーキテクチャ

```
ブラウザ
  |
  | HTTP
  v
Apache2（リバースプロキシ: /freebox/ → 127.0.0.1:9009）
  |
  v
freeBox Loader（box_webserver.py / Python HTTP サーバー）
  |
  +-- Manager UI（/freebox/manager/）
  |
  +-- Plugin ルーティング（/freebox/<plugin_id>/）
  |     atomcam2  → plugins/atomcam2.py の Plugin クラス
  |     myplugin  → plugins/myplugin.py の Plugin クラス
  |
  +-- Loader API（/freebox/api/）
  |     GET  /api/modules          インストール済みとインデックスの一覧
  |     POST /api/module/install   モジュールのインストール
  |     POST /api/module/uninstall モジュールの削除
  |     POST /api/module/upload    .hbx ファイルのアップロード
  |     POST /api/index/refresh    インデックスの再取得
  |     GET  /api/status           サービスのステータス確認
  |
  +-- スケジューラ（Plugin の register_schedule で登録）
  |
  +-- IndexCache（GitHub の index.json をキャッシュ）
```

---

## 3. ファイル構成（hsBox 上）

```
/home/hsbox/freebox/
  box_webserver.py          freeBox Loader 本体
  merge_config.py           設定ファイルのマージユーティリティ
  freebox_config.ini        Loader の設定ファイル
  plugins/
    atomcam2.py             インストール済み Plugin（エントリポイント）
    atomcam2/               Plugin 専用ファイル群
      atomcam2_config.ini   Plugin の設定ファイル
  data/
    index_cache.json        インデックスのキャッシュ

/etc/systemd/system/freebox.service   systemd サービス定義
/etc/apache2/conf-enabled/freebox.conf  Apache2 プロキシ設定
/home/hsbox/www/freebox/index.php     hsBox タブ連携用
```

---

## 4. Plugin のインストール方法

Plugin のインストールは 3 つの方法があります。

| 方法 | 対象 | 手順 |
|------|------|------|
| Manager UI からインストール | `public` / `restricted` モジュール | Manager UI の Deploy ボタンを押す |
| .hbx をアップロード | `private` モジュール | Manager UI の Upload から .hbx ファイルを選択 |
| run.sh によるインストール | すべて | hsBox に SSH して run.sh を直接実行 |

---

## 5. インデックス（index.json）

freeBox Loader は GitHub の `docs/index.json` を参照して、インストール可能なモジュールの一覧を表示します。

**取得 URL:**
```
https://raw.githubusercontent.com/hoscm/freebox/main/docs/index.json
```

**index.json のフィールド:**

| フィールド | 必須 | 内容 |
|-----------|------|------|
| `id` | はい | モジュール ID（英小文字・数字・ハイフン） |
| `name` | はい | UI 表示名 |
| `description` | はい | UI 説明文 |
| `status` | はい | `public` または `restricted`（`private` は index.json には書かない） |
| `version` | はい | バージョン番号（semver 推奨） |
| `release_tag` | はい | GitHub Release のタグ名 |
| `author` | はい | 作成者名 |
| `repository` | はい | `.hbx` を配置したリポジトリのベース URL |
| `plugin_file` | はい | Plugin ファイル名（`myplugin.py` 形式） |

**hbx ダウンロード URL の構築規則:**
```
{repository}/releases/download/{release_tag}/{id}.hbx
```

---

## 6. モジュールのステータス

| ステータス | 意味 | インストール方法 |
|-----------|------|----------------|
| `public` | 誰でも使用できる | Manager UI から 1 クリックでインストール |
| `restricted` | 制限あり（要確認） | Manager UI から確認ダイアログ経由でインストール |
| `private` | 非公開 | `.hbx` ファイルを直接アップロード |

---

## 7. hsBox パッチモジュールの version.txt 実装ルール（絶対遵守）

freeBox Loader は hsBox のパッチモジュールとして配布されます。
hsBox のパッチシステムが認識できるよう、`version.txt` は以下の **5行形式** で記述しなければなりません。

### 7-1. 正規 5行形式（必須）

```
行1: pdname  = モジュール名（hsBox がアクセスする際のコンテキスト名）
行2: obb     = 適用可能な最低ベースビルド番号（このビルド以降に適用可能）
行3: obv     = 適用可能な最古の hsBox フルバージョン
行4: nwv     = 適用可能な最新の hsBox フルバージョン
行5: thisv   = このパッチ自体のバージョン（semver 形式）
```

### 7-2. freeBox Loader の version.txt（正規・G-26 確定）

```
freebox
269
1.03.01.01
1.03.02.99
1.0.0
```

| 行 | フィールド | 値 | 意味 |
|----|---------|-----|------|
| 1 | pdname | `freebox` | hsBox がアクセスするコンテキスト名 |
| 2 | obb | `269` | 適用可能な最低ベースビルド番号（boxweb 参考値） |
| 3 | obv | `1.03.01.01` | 適用可能な最古バージョン（ライセンスパッチ適用後の 1.3.1.1） |
| 4 | nwv | `1.03.02.99` | 適用可能な最新バージョン（暫定・STのたびに更新） |
| 5 | thisv | `1.0.0` | このパッチ自体のバージョン |

### 7-3. 動作要件

- **hsBox 1.03.01.01 以降**（hsBox 1.3.1.0 に最新ライセンスパッチを適用した状態）で利用可能
- ST では「hsBox 1.3.1.0 + 最新ライセンスパッチ適用済み（= 1.03.01.01）」環境でテストを実施すること
- ライセンスパッチ未適用の hsBox 1.03.01.00 では適用不可

### 7-4. obb・obv・nwv の更新ルール

| フィールド | 現在値 | 更新タイミング |
|---------|--------|---------------|
| `obb` | `269` | ベースビルド番号変更時（通常は変更不要） |
| `obv` | `1.03.01.01` | サポート開始バージョンが変わる場合のみ更新 |
| `nwv` | `1.03.02.99` | ST で新しい hsBox バージョンで動作確認するたびに更新 |

---

## 8. サードパーティ Plugin の開発

freeBox 対応 Plugin の開発については以下のドキュメントを参照してください。

| ドキュメント | 内容 |
|------------|------|
| `plugin_dev_guide.md` | Plugin クラスの実装方法・API リファレンス |
| `run_sh_guide.md` | インストールスクリプト（run.sh）の実装方法 |
| `hbx_build_tool_guide.md` | `.hbx` パッケージのビルド方法 |

---

## 9. v1 の制限事項

| 項目 | 制限内容 |
|------|---------|
| 複数タブ同時利用 | 1 タブのみで使用してください |
| Deploy 後の自動再起動 | Deploy 後は hsBox を再起動してください |
| Re-deploy | 一度 Remove してから再インストールしてください |
| オフライン環境 | インターネット接続が必要です（キャッシュ有効期間内は除く） |

---

*本ドキュメントは freeBox Loader v1 の実装に基づきます。*
