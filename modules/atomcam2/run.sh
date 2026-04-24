#!/bin/bash
# atomcam2 Module インストールスクリプト
# .hbx展開後に root 権限で実行される
# 冪等性あり：複数回実行しても同一状態になる

set -e

ZTMP="/home/hsbox/ztmp"
FREEBOX_DIR="/home/hsbox/freebox"
PLUGINS_DIR="${FREEBOX_DIR}/plugins"
CONFIG_INI="${PLUGINS_DIR}/atomcam2_config.ini"

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
# 3. Plugin ファイル配置
# --------------------------------------------------
echo "[atomcam2] Plugin ファイルを配置..."
cp -f "${ZTMP}/atomcam2.py" "${PLUGINS_DIR}/atomcam2.py"
chmod 644 "${PLUGINS_DIR}/atomcam2.py"

# --------------------------------------------------
# 4. 設定ファイル処理（初回：コピー / 更新：マージ）
# --------------------------------------------------
echo "[atomcam2] 設定ファイルを処理..."
if [ ! -f "${CONFIG_INI}" ]; then
    echo "[atomcam2] atomcam2_config.ini が存在しないためテンプレートをコピー"
    cp -f "${ZTMP}/atomcam2_config.ini.template" "${CONFIG_INI}"
else
    echo "[atomcam2] atomcam2_config.ini が存在するため差分マージを実行"
    python3 "${FREEBOX_DIR}/merge_config.py" \
        "${CONFIG_INI}" \
        "${ZTMP}/atomcam2_config.ini.template"
fi
# [SEC] RTSPパスワード等の機密情報を含む設定ファイルは 640（other読み取り不可）
chmod 640 "${CONFIG_INI}"
chown hsbox:hsbox "${CONFIG_INI}" 2>/dev/null || true

# --------------------------------------------------
# 5. freeBox Loader サービス再起動
# --------------------------------------------------
echo "[atomcam2] サービスを再起動..."
systemctl start freebox

echo "[atomcam2] インストール完了"
echo "[atomcam2] ⚠ atomcam2_config.ini にカメラMAC・RTSPパスワード・NASパスを設定してください。"
echo "[atomcam2]   設定ファイルのパス: ${CONFIG_INI}"
echo "[atomcam2]   設定後に 'sudo systemctl restart freebox' で有効化されます。"
