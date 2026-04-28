#!/bin/bash
# atomcam2 Module インストールスクリプト
# .hbx展開後に root 権限で実行される
# 冪等性あり：複数回実行しても同一状態になる
#
# ディレクトリ構造（G-21）:
#   plugins/atomcam2.py              ← エントリポイント
#   plugins/atomcam2/
#     atomcam2_config.ini            ← 設定ファイル

set -e

ZTMP="/home/hsbox/ztmp"
FREEBOX_DIR="/home/hsbox/freebox"
PLUGINS_DIR="${FREEBOX_DIR}/plugins"
PLUGIN_SUBDIR="${PLUGINS_DIR}/atomcam2"
CONFIG_INI="${PLUGIN_SUBDIR}/atomcam2_config.ini"

echo "[atomcam2] インストール開始"

# --------------------------------------------------
# 1. freeBox Loader サービス停止（未起動でもエラーにしない）
# --------------------------------------------------
echo "[atomcam2] サービス停止..."
systemctl stop freebox 2>/dev/null || true

# --------------------------------------------------
# 2. plugins/ ディレクトリ確認
# --------------------------------------------------
mkdir -p "${PLUGINS_DIR}"

# --------------------------------------------------
# 3. Plugin ファイル配置（エントリポイント）
# --------------------------------------------------
echo "[atomcam2] Plugin ファイルを配置..."
cp -f "${ZTMP}/atomcam2.py" "${PLUGINS_DIR}/atomcam2.py"
chmod 644 "${PLUGINS_DIR}/atomcam2.py"

# --------------------------------------------------
# 4. サブディレクトリ作成（G-21: 設定ファイルはサブディレクトリに配置）
# --------------------------------------------------
mkdir -p "${PLUGIN_SUBDIR}"

# --------------------------------------------------
# 5. 設定ファイル処理（初回：コピー / 更新：マージ）
# --------------------------------------------------
echo "[atomcam2] 設定ファイルを処理..."
if [ ! -f "${CONFIG_INI}" ]; then
    echo "[atomcam2] atomcam2_config.ini が存在しないためテンプレートをコピー"
    cp -f "${ZTMP}/atomcam2_config.ini.template" "${CONFIG_INI}"
else
    echo "[atomcam2] atomcam2_config.ini が存在するため差分マージを実行"
    python3 "${ZTMP}/merge_config.py" \
        "${CONFIG_INI}" \
        "${ZTMP}/atomcam2_config.ini.template"
fi
# [SEC] RTSPパスワード等の機密情報を含む設定ファイルは 640（other読み取り不可）
chmod 640 "${CONFIG_INI}"
chown hsbox:hsbox "${CONFIG_INI}" 2>/dev/null || true

# --------------------------------------------------
# 6. バージョン情報記録（D-02: Loader v2 で参照される）
# --------------------------------------------------
echo "[atomcam2] バージョン情報を記録..."
MODULE_VERSION=""
if [ -f "${ZTMP}/version.txt" ]; then
    # version.txt 2行形式: 行1=module_id, 行2=version
    MODULE_VERSION="$(sed -n '2p' "${ZTMP}/version.txt")"
fi
cat > "${PLUGIN_SUBDIR}/version.txt" <<EOF
atomcam2
${MODULE_VERSION}
EOF
chmod 644 "${PLUGIN_SUBDIR}/version.txt"

# --------------------------------------------------
# 7. freeBox Loader サービス再起動
# --------------------------------------------------
echo "[atomcam2] サービスを再起動..."
systemctl start freebox

echo "[atomcam2] インストール完了"
echo "[atomcam2] ⚠ 設定 GUI でカメラMAC・RTSPパスワード・NASパスを設定してください。"
echo "[atomcam2]   Manager UI → atomcam2 モジュール画面から設定できます。"
echo "[atomcam2]   設定保存後に 'sudo systemctl restart freebox' で有効化されます。"
echo ""
echo "[atomcam2] ファイル配置:"
echo "[atomcam2]   エントリポイント : ${PLUGINS_DIR}/atomcam2.py"
echo "[atomcam2]   設定ファイル     : ${CONFIG_INI}"
