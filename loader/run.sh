#!/bin/bash
# freeBox Loader インストールスクリプト
# .hbx展開後に root 権限で実行される
# 冪等性あり：複数回実行しても同一状態になる

set -e

ZTMP="/home/hsbox/ztmp"
FREEBOX_DIR="/home/hsbox/freebox"
WWW_DIR="/home/hsbox/www/freebox"
APACHE_CONF_DIR="/etc/apache2/conf-enabled"
SYSTEMD_DIR="/etc/systemd/system"
CONFIG_INI="${FREEBOX_DIR}/freebox_config.ini"

echo "[freeBox] インストール開始"

# --------------------------------------------------
# 1. サービス停止（未起動でもエラーにしない）
# --------------------------------------------------
echo "[freeBox] サービス停止..."
systemctl stop freebox 2>/dev/null || true

# --------------------------------------------------
# 2. ディレクトリ作成
# --------------------------------------------------
echo "[freeBox] ディレクトリ作成..."
mkdir -p "${FREEBOX_DIR}/plugins"
mkdir -p "${FREEBOX_DIR}/data"
mkdir -p "${WWW_DIR}"

# --------------------------------------------------
# 3. サーバーファイル配置（平坦化コピー）
# --------------------------------------------------
echo "[freeBox] サーバーファイルを配置..."
cp -f "${ZTMP}/server/box_webserver.py" "${FREEBOX_DIR}/box_webserver.py"

# パーミッション設定
chmod 755 "${FREEBOX_DIR}"
chmod 755 "${FREEBOX_DIR}/plugins"
chmod 755 "${FREEBOX_DIR}/data"
chmod 644 "${FREEBOX_DIR}/box_webserver.py"

# --------------------------------------------------
# 4. 設定ファイル処理
# --------------------------------------------------
echo "[freeBox] 設定ファイルを処理..."
if [ ! -f "${CONFIG_INI}" ]; then
    echo "[freeBox] freebox_config.ini が存在しないためテンプレートをコピー"
    cp -f "${ZTMP}/freebox_config.ini.template" "${CONFIG_INI}"
else
    echo "[freeBox] freebox_config.ini が存在するため差分マージを実行"
    python3 "${ZTMP}/merge_config.py" \
        "${CONFIG_INI}" \
        "${ZTMP}/freebox_config.ini.template"
fi
# [SEC] 機密情報（notify_url等）を含む設定ファイルは640（other読み取り不可）
chmod 640 "${CONFIG_INI}"
chown hsbox:hsbox "${CONFIG_INI}" 2>/dev/null || true  # 既にhsboxオーナーなら無害

# --------------------------------------------------
# 5. Apache設定
# --------------------------------------------------
echo "[freeBox] Apache設定を配置..."
cp -f "${ZTMP}/freebox.conf" "${APACHE_CONF_DIR}/freebox.conf"

echo "[freeBox] 必要な Apache モジュールを有効化..."
a2enmod proxy proxy_http headers || true

echo "[freeBox] Apache設定をテスト..."
if apache2ctl configtest 2>&1; then
    echo "[freeBox] Apache設定テスト成功。reload を実行..."
    systemctl reload apache2
else
    echo "[freeBox] ERROR: Apache設定テスト失敗。reload をスキップします（既存設定を保持）"
fi

# --------------------------------------------------
# 6. hsBox UI連携ファイル配置
# --------------------------------------------------
echo "[freeBox] hsBox UI連携ファイルを配置..."
cp -f "${ZTMP}/www/index.php" "${WWW_DIR}/index.php"
chmod 644 "${WWW_DIR}/index.php"

# --------------------------------------------------
# 7. systemdサービス登録
# --------------------------------------------------
echo "[freeBox] systemdサービスを登録..."
cp -f "${ZTMP}/freebox.service" "${SYSTEMD_DIR}/freebox.service"
systemctl daemon-reload
systemctl enable freebox
systemctl start freebox

# [SEC] Plugin設定ファイルのパーミッションを640に設定
find "${FREEBOX_DIR}/plugins" -maxdepth 2 -name '*_config.ini' | while read ini_file; do
    chmod 640 "${ini_file}"
    chown hsbox:hsbox "${ini_file}" 2>/dev/null || true
done

echo "[freeBox] インストール完了"
