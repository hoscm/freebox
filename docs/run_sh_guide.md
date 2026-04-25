# インストールスクリプト実装ガイド（run.sh）

**対象読者:** freeBox 対応モジュールを開発・配布したいサードパーティ開発者  
**前提知識:** bash の基本的な知識  

---

## 1. run.sh とは

`run.sh` は `.hbx` のインストール時に freeBox Loader が実行するシェルスクリプトです。  
Plugin ファイルの配置・設定ファイルの初期化・サービス再起動などを担当します。

---

## 2. 実行環境

| 項目 | 内容 |
|------|------|
| 実行権限 | root |
| 実行タイミング | `.hbx` 展開後、`/home/hsbox/ztmp/` に展開した状態で実行 |
| 作業ディレクトリ | 規定なし（スクリプト内で絶対パスを使用すること） |
| シェル | bash |

---

## 3. 展開後のディレクトリ構成

`.hbx` を展開すると `/home/hsbox/ztmp/` に以下のファイルが展開されます。

```
/home/hsbox/ztmp/
  myplugin.py                  Plugin 実装ファイル
  version.txt                  バージョン情報
  myplugin_config.ini.template 設定テンプレート（含める場合）
  run.sh                       本スクリプト（ztmp に含まれる場合のみ）
```

**注意:** `run.sh` は `.hbx` に含めることもできますが、freeBox Loader がロード済みの `run.sh` を実行する仕組みのため、ビルドツールの設定に依存します。詳細は `hbx_build_tool_guide.md` を参照してください。

---

## 4. 標準的なディレクトリパス

| 定数 | パス | 内容 |
|------|------|------|
| `ZTMP` | `/home/hsbox/ztmp` | 展開先の一時ディレクトリ |
| `FREEBOX_DIR` | `/home/hsbox/freebox` | freeBox の作業ディレクトリ |
| `PLUGINS_DIR` | `/home/hsbox/freebox/plugins` | Plugin 配置先 |

---

## 5. 最小構成の run.sh

```bash
#!/bin/bash
# myplugin インストールスクリプト

set -e

ZTMP="/home/hsbox/ztmp"
FREEBOX_DIR="/home/hsbox/freebox"
PLUGINS_DIR="${FREEBOX_DIR}/plugins"

echo "[myplugin] インストール開始"

# サービスを停止する
systemctl stop freebox 2>/dev/null || true

# plugins/ ディレクトリを確認する
mkdir -p "${PLUGINS_DIR}"

# Plugin ファイルを配置する
cp -f "${ZTMP}/myplugin.py" "${PLUGINS_DIR}/myplugin.py"
chmod 644 "${PLUGINS_DIR}/myplugin.py"

# サービスを再起動する
systemctl start freebox

echo "[myplugin] インストール完了"
```

---

## 6. 設定ファイルの処理（初回コピーと更新時のマージ）

設定ファイルが存在する場合に上書きしてしまうと、ユーザーが設定した値が失われます。  
freeBox には設定ファイルを安全に更新するための `merge_config.py` が用意されています。

```bash
CONFIG_INI="${PLUGINS_DIR}/myplugin_config.ini"

if [ ! -f "${CONFIG_INI}" ]; then
    # 初回インストール: テンプレートをコピーする
    echo "[myplugin] 設定ファイルをコピーします"
    cp -f "${ZTMP}/myplugin_config.ini.template" "${CONFIG_INI}"
else
    # 更新時: 新しいキーだけを追加し、既存の値を維持するマージを実行する
    echo "[myplugin] 設定ファイルをマージします"
    python3 "${FREEBOX_DIR}/merge_config.py" \
        "${CONFIG_INI}" \
        "${ZTMP}/myplugin_config.ini.template"
fi

# 認証情報を含む設定ファイルはパーミッション 640 に設定する
chmod 640 "${CONFIG_INI}"
chown hsbox:hsbox "${CONFIG_INI}" 2>/dev/null || true
```

**merge_config.py の動作:**
- 既存の `myplugin_config.ini` に存在しないキーをテンプレートから追加します
- 既存のキーの値は変更しません
- バージョンアップ時に新しい設定項目を安全に追加できます

---

## 7. パーミッションの設定

| ファイル種別 | 推奨パーミッション | 理由 |
|------------|----------------|------|
| Plugin ファイル（.py） | `644` | Loader が読み取れれば十分 |
| 設定ファイル（.ini） | `640` | パスワード等の認証情報を含む場合 |
| 認証情報を含まない設定 | `644` でも可 | 内容による |

---

## 8. 冪等性の確保

`run.sh` は複数回実行しても同じ状態になるように実装してください。

```bash
# ディレクトリの作成: mkdir -p を使う（存在していてもエラーにならない）
mkdir -p "${PLUGINS_DIR}"

# ファイルのコピー: cp -f を使う（上書きを許可する）
cp -f "${ZTMP}/myplugin.py" "${PLUGINS_DIR}/myplugin.py"

# サービス停止: エラーを無視する
systemctl stop freebox 2>/dev/null || true
```

---

## 9. エラーハンドリング

スクリプトの冒頭に `set -e` を記述することで、コマンドが失敗した場合に即座にスクリプトを停止できます。

```bash
set -e
```

`set -e` の例外としてエラーを無視したい場合は `|| true` を付けます。

```bash
systemctl stop freebox 2>/dev/null || true
```

---

## 10. ユーザーへのメッセージ出力

インストール完了後に必要な設定手順をユーザーへ案内してください。

```bash
echo "[myplugin] インストール完了"
echo "[myplugin] 設定ファイルにカメラ情報を入力してください。"
echo "[myplugin]   設定ファイルのパス: ${CONFIG_INI}"
echo "[myplugin]   設定後に 'sudo systemctl restart freebox' で有効になります。"
```

---

## 11. 実装例: atomcam2 の run.sh

```bash
#!/bin/bash
# atomcam2 インストールスクリプト

set -e

ZTMP="/home/hsbox/ztmp"
FREEBOX_DIR="/home/hsbox/freebox"
PLUGINS_DIR="${FREEBOX_DIR}/plugins"
PLUGIN_SUBDIR="${PLUGINS_DIR}/atomcam2"
CONFIG_INI="${PLUGIN_SUBDIR}/atomcam2_config.ini"

echo "[atomcam2] インストール開始"

systemctl stop freebox 2>/dev/null || true

mkdir -p "${PLUGINS_DIR}"

# エントリポイントを配置
cp -f "${ZTMP}/atomcam2.py" "${PLUGINS_DIR}/atomcam2.py"
chmod 644 "${PLUGINS_DIR}/atomcam2.py"

# サブディレクトリを作成し、設定ファイルを配置
mkdir -p "${PLUGIN_SUBDIR}"
if [ ! -f "${CONFIG_INI}" ]; then
    echo "[atomcam2] 設定ファイルをコピーします"
    cp -f "${ZTMP}/atomcam2_config.ini.template" "${CONFIG_INI}"
else
    echo "[atomcam2] 設定ファイルをマージします"
    python3 "${FREEBOX_DIR}/merge_config.py" \
        "${CONFIG_INI}" \
        "${ZTMP}/atomcam2_config.ini.template"
fi

chmod 640 "${CONFIG_INI}"
chown hsbox:hsbox "${CONFIG_INI}" 2>/dev/null || true

systemctl start freebox

echo "[atomcam2] インストール完了"
echo "[atomcam2] カメラ MAC とパスワード、NAS パスを設定してください。"
echo "[atomcam2]   設定ファイルのパス: ${CONFIG_INI}"
```

ファイル構成実装については `plugin_dev_guide.md` §13 を参照してください。

---

*本ドキュメントは freeBox Loader v1 の実装に基づきます。*
