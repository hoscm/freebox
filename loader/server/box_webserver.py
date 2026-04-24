#!/usr/bin/env python3
"""
box_webserver.py  ─  freeBox Loader メインサーバー
=======================================================
Apache2 Proxy から /freebox/ を受け取り、Plugin にルーティングする。

実装方式設計書 (FD): step2_loader_design_7.md (Rev.7)
API 仕様書 (API): freebox_loader_api_spec_v1.md (v1)
CD 機能分割リスト: freebox_cd_task_list.md

【実装完了機能 (このファイル)】
  FX-001 HTTPサーバー起動・設定読み込み
  FX-002 セキュリティチェック（パストラバーサル防御）
  FX-003 ルーティング (_dispatch)
  FX-004 IndexCache（GitHubインデックス取得・キャッシュ）
  FX-005 PluginManager（プラグインロード・ルーティング）
  FX-006 Scheduler（内蔵CRONスケジューラ）
  FX-007 ステータス通知 (send_notify)
  FX-008 NASチェック (is_nas_available)
  FX-009 Response / RequestWrapper クラス
  FX-101 GET /api/modules
  FX-102 POST /api/module/install
  FX-103 POST /api/module/uninstall
  FX-104 POST /api/module/upload
  FX-105 POST /api/index/refresh（排他制御付き）
  FX-106 GET /api/status（実チェック版）
  FX-205 Re-deployダイアログ実装済み（BK-04: v1ではUIを非表示化、v2で再設計予定）
  FX-206 Uninstall確認ダイアログ（confirm-uninstall・1段階・全status共通）
  FX-208 StatusBar（30秒ポーリング）
  FX-209 RebootBanner（Install/Uninstall/Upload成功後に再起動バナー表示）
  FX-210 Refresh Indexボタン（POST /api/index/refresh → GET /api/modules で再描画）

Python バージョン互換性要件: 3.10.11 / 3.14
  - PEP 604 (X | Y 型ヒント) は 3.10 以降で使用可
  - 3.11+ 専用機能 (except* 等) は使用禁止
  - cgi.FieldStorage は 3.13 で削除済み → email.message_from_bytes を使用（FX-104）
"""

# ---------------------------------------------------------------------------
# 標準ライブラリ インポート
# ---------------------------------------------------------------------------
import configparser
import email           # FX-104: multipart/form-data パース（cgi 代替）
import email.policy    # FX-104: compat32 ポリシー（Python 3.10〜3.14 互換）
import importlib.util
import json
import logging
import os
import os.path
import posixpath    # FX-002: パストラバーサル防御で使用 (ファイル冒頭でインポート)
import re
import signal       # FX-007: SIGTERM シグナルハンドラ登録に使用
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# 定数定義
# ---------------------------------------------------------------------------
# FX-001: ファイルシステムパス定数
#   BASE_DIR はこのスクリプトの配置場所を基点とする。
#   WorkingDirectory と一致させるため os.path.abspath で絶対パス化する。
#   FD §3-2: /home/hsbox/freebox/ を WorkingDirectory とする設計と整合。
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR      = os.path.join(BASE_DIR, "plugins")
DATA_DIR         = os.path.join(BASE_DIR, "data")
CONFIG_PATH      = os.path.join(BASE_DIR, "freebox_config.ini")
INDEX_CACHE_PATH = os.path.join(DATA_DIR, "index_cache.json")

# FX-002/FX-004: セキュリティ・入力検証用正規表現
#   FD §4-4, §9-3: Plugin ID / module ID は ^[a-z0-9]+$ に限定
VALID_PLUGIN_NAME = re.compile(r'^[a-z0-9][a-z0-9\-]*[a-z0-9]$')
#VALID_PLUGIN_NAME = re.compile(r'^[a-z0-9]+$')

# FX-005: ルーティング予約名（Loader 専用コンテキスト）
#   FD §4-6: loader / index / api / status / manager
#   "manager" は ManagerPlugin が占有するため Plugin からは除外する
RESERVED_PLUGIN_NAMES = frozenset({"loader", "index", "api", "status", "manager"})

# FX-001: サーバーデフォルト値
#   freebox_config.ini が存在しない場合、または各キーが欠落している場合に使用する。
#   FD §10-3: [server] / [loader] / [status] セクション定義と整合。
DEFAULT_HOST      = "127.0.0.1"   # バインドアドレス（ループバック = Apache経由のみ）
DEFAULT_PORT      = 9009          # 待受ポート (FD §3-2)
DEFAULT_INDEX_URL = (             # GitHub Raw URL (FD §9-1)
    "https://raw.githubusercontent.com/hoscm/freebox/main/docs/index.json"
)
DEFAULT_CACHE_TTL = 3600          # インデックスキャッシュ TTL (秒) = 1時間
DEFAULT_CONTEXT   = "manager"     # ルートアクセス転送先コンテキスト

# FX-007: ステータス通知メッセージ定数 (FD §3-5)
NOTIFY_MSG_OK   = "FreeBoxLoader-OK"
NOTIFY_MSG_FAIL = "FreeBoxLoader-Fail"

# FX-009: JSON Body 読み取りの最大サイズ定数
#   RequestWrapper.read_body() が対象。通常の JSON ボディ用（最大数十バイト）。
#   FX-104 の multipart アップロードは本定数を使わず rfile から直接読み取る。
_MAX_BODY_SIZE: int = 1 * 1024 * 1024   # 1MB

# FX-104: multipart/form-data アップロードの最大サイズ定数
#   private Module の .hbx ファイルは通常 1MB 未満。
#   10MB を上限とし、過大リクエストによる OOM リスクを低減する。
#   ⚠ この値を変更する場合は Nginx/Apache のクライアントボディサイズ設定とも整合させること。
_MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024   # 10MB

# ---------------------------------------------------------------------------
# ロギング設定
# ---------------------------------------------------------------------------
# FX-001: systemd 管理下では stdout が journald にキャプチャされる。
#   FD §3-3: Type=simple で起動するため、ログ出力先は stdout に固定する。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ===========================================================================
# FX-008: NASチェック
# ===========================================================================

def is_nas_available(mount_point: str) -> bool:
    """
    NAS マウントポイントが有効かつ書き込み可能かを確認する。

    FD §6-2 準拠:
      os.path.ismount()  : マウントポイントとして認識されているか
      os.access(W_OK)    : 書き込み権限があるか

    引数:
      mount_point : 確認対象のマウントポイントパス
    戻り値:
      True  = NAS 接続あり・書き込み可能
      False = 未接続・パス不正・例外発生のいずれか

    設計注意点:
      例外は呼び出し元に伝播させず False を返す（縮退設計の核心: FD §6-4）。
    """
    try:
        return os.path.ismount(mount_point) and os.access(mount_point, os.W_OK)
    except Exception:
        return False


# ===========================================================================
# FX-001: 設定読み込み・保存
# ===========================================================================

def load_config() -> configparser.ConfigParser:
    """
    freebox_config.ini を読み込み、ConfigParser オブジェクトを返す。

    FD §10-3 準拠: [server] / [loader] / [status] セクションのデフォルト値を
    configparser のセクション初期値として設定しておくことで、
    設定ファイルの部分的な欠落に対してもフォールバック値が自動的に適用される。

    FD §10-2-1: パーミッション 640 (hsbox:hsbox) 必須。
                このコードでは権限設定を行わない（run.sh の責務: FD §10-5 参照）。
    """
    cfg = configparser.ConfigParser()
    cfg["server"] = {
        "host":      DEFAULT_HOST,
        "port":      str(DEFAULT_PORT),
        "base_path": "",   # FX-211: Apache2 Proxy ベースパス（例: /freebox）。空=直接アクセス
    }
    cfg["loader"] = {
        "index_url":       DEFAULT_INDEX_URL,
        "index_cache_ttl": str(DEFAULT_CACHE_TTL),
        "default_context": DEFAULT_CONTEXT,
    }
    cfg["status"] = {
        "notify_url":       "",
        "notify_component": "FreeBoxLoader",
    }

    if os.path.exists(CONFIG_PATH):
        cfg.read(CONFIG_PATH, encoding="utf-8")
        logger.info("[FX-001] 設定ファイル読み込み完了: %s", CONFIG_PATH)
    else:
        logger.warning(
            "[FX-001] 設定ファイルが見つかりません。デフォルト設定で起動します: %s",
            CONFIG_PATH,
        )
    return cfg


def save_config(cfg: configparser.ConfigParser) -> None:
    """
    ConfigParser の内容を freebox_config.ini に書き込む。
    書き込み失敗時は例外をそのまま raise する（呼び出し元でキャッチすること）。
    """
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            cfg.write(f)
        logger.info("[FX-001] 設定ファイル保存完了: %s", CONFIG_PATH)
    except Exception as e:
        logger.error("[FX-001] 設定ファイル保存失敗: %s", e)
        raise


# ===========================================================================
# FX-007: ステータス通知
# ===========================================================================

def send_notify(notify_url: str, component: str, message: str) -> None:
    """
    notify_url が設定されている場合にステータスを POST で通知する。
    notify_url が空の場合はサイレントスキップ。通知失敗はログ警告のみ。

    FD §3-5 準拠。
    """
    if not notify_url:
        return
    try:
        payload = json.dumps({
            "component": component,
            "message":   message,
            "timestamp": datetime.now().isoformat(),
        }).encode("utf-8")
        req = urllib.request.Request(
            notify_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status >= 400:
                logger.warning("[FX-007] 通知: サーバーエラー応答 %d (%s)", resp.status, notify_url)
            else:
                logger.info("[FX-007] 通知完了: %s (%d)", notify_url, resp.status)
    except Exception as e:
        logger.warning("[FX-007] 通知送信失敗（継続）: %s", e)


# ===========================================================================
# FX-004: IndexCache（GitHubインデックス取得・キャッシュ）
# ===========================================================================

def _empty_index() -> dict:
    """インデックスが取得できない場合のフォールバック値。FD §9-2 の最小スキーマ。"""
    return {"schema_version": "1", "modules": []}


def _validate_index(data: dict) -> bool:
    """
    index.json の入力検証。フェッチ直後に呼び出し、不正データを排除する。

    FD §9-3 / FD §4-4-1 (セキュリティ基本方針) 準拠:
      - トップレベルが dict であること
      - schema_version が str 型であること
      - modules が list 型であること
      - modules[].id が VALID_PLUGIN_NAME に準拠すること
      - modules[].plugin_file がファイル名のみ（パス文字列・相対パス禁止）
    """
    if not isinstance(data, dict):
        logger.warning("[FX-004][SEC] index 検証失敗: トップレベルが dict ではない")
        return False
    if not isinstance(data.get("schema_version"), str):
        logger.warning("[FX-004][SEC] index 検証失敗: schema_version が str ではない")
        return False
    modules = data.get("modules", [])
    if not isinstance(modules, list):
        logger.warning("[FX-004][SEC] index 検証失敗: modules がリストではない")
        return False
    for i, mod in enumerate(modules):
        if not isinstance(mod, dict):
            logger.warning("[FX-004][SEC] index 検証失敗: modules[%d] が dict ではない", i)
            return False
        mod_id = str(mod.get("id", ""))
        if not VALID_PLUGIN_NAME.match(mod_id):
            logger.warning("[FX-004][SEC] index 検証失敗: modules[%d].id が不正 (%r)", i, mod_id)
            return False
        plugin_file = str(mod.get("plugin_file", ""))
        if "/" in plugin_file or "\\" in plugin_file or ".." in plugin_file:
            logger.warning("[FX-004][SEC] index 検証失敗: modules[%d].plugin_file にパス指定 (%r)", i, plugin_file)
            return False
        if not plugin_file.endswith(".py"):
            logger.warning("[FX-004][SEC] index 検証失敗: modules[%d].plugin_file が .py でない (%r)", i, plugin_file)
            return False
    return True


class IndexCache:
    """
    GitHub インデックス JSON のフェッチとファイルキャッシュを管理するクラス。

    FD §9-4 準拠:
      - TTL 期限内はオンメモリキャッシュを返す（GitHub アクセスなし）
      - TTL 切れ時に GitHub からフェッチ → ファイルキャッシュを更新
      - フェッチ失敗時はファイルキャッシュにフォールバック

    FX-105 排他制御 (FD §4-3 準拠):
      - _refresh_lock + _refreshing フラグで POST /api/index/refresh の並行実行を防止
    """

    def __init__(self, index_url: str, cache_ttl: int, cache_path: str) -> None:
        self._url         = index_url
        self._ttl         = cache_ttl
        self._cache_path  = cache_path
        self._lock        = threading.Lock()
        self._data: dict  = self._load_file_cache()  # ← ここを変更
        self._fetched_at: float = 0.0
        self._refresh_lock: threading.Lock = threading.Lock()
        self._refreshing: bool = False

    #def __init__(self, index_url: str, cache_ttl: int, cache_path: str) -> None:
    #    self._url         = index_url
    #    self._ttl         = cache_ttl
    #    self._cache_path  = cache_path
    #    self._lock        = threading.Lock()
    #    self._data: dict  = _empty_index()
    #    self._fetched_at: float = 0.0
    #    self._refresh_lock: threading.Lock = threading.Lock()
    #    self._refreshing: bool = False

    def get(self) -> dict:
        """
        キャッシュが有効ならオンメモリキャッシュを返す。TTL 切れなら _fetch() を呼び出す。
        TTL 判定と _fetched_at 更新を同一ロックブロックで行い TOCTOU を防止する。
        """
        with self._lock:
            if (
                time.time() - self._fetched_at < self._ttl
                and self._data.get("modules") is not None
            ):
                return self._data
            self._fetched_at = time.time()
        return self._fetch()

    def get_last_fetched(self) -> float:
        """最終取得時刻 (Unix timestamp) を返す（Manager UI 表示用）"""
        with self._lock:
            return self._fetched_at

    def _fetch(self) -> dict:
        """
        GitHub からインデックス JSON を取得し、ファイルキャッシュを更新する。
        失敗時はファイルキャッシュにフォールバック。
        """
        try:
            req = urllib.request.Request(self._url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if not _validate_index(data):
                logger.error("[FX-004] インデックス JSON がスキーマ検証失敗。キャッシュを使用します。")
                return self._load_file_cache()
            self._save_file_cache(data)
            with self._lock:
                self._data       = data
                self._fetched_at = time.time()
            logger.info("[FX-004] インデックス取得成功: %d モジュール", len(data.get("modules", [])))
            return data
        except Exception as e:
            logger.warning("[FX-004] インデックス取得失敗: %s → ファイルキャッシュを使用", e)
            cached = self._load_file_cache()
            with self._lock:
                self._data       = cached
                self._fetched_at = time.time()
            return cached

    def _save_file_cache(self, data: dict) -> None:
        """tmp → rename (os.replace) によるアトミックな書き込み。FD §9-4 / §4-3 準拠。"""
        cache_dir = os.path.dirname(self._cache_path)
        os.makedirs(cache_dir, exist_ok=True)
        tmp_path = self._cache_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._cache_path)
        except Exception as e:
            logger.warning("[FX-004] キャッシュ保存失敗: %s", e)

    def _load_file_cache(self) -> dict:
        """ファイルキャッシュからインデックス JSON を読み込む。失敗時は空インデックスを返す。"""
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception as e:
            logger.warning("[FX-004] ファイルキャッシュ読み込み失敗: %s", e)
            return _empty_index()

    #def _load_file_cache(self) -> dict:
    #    """ファイルキャッシュからインデックス JSON を読み込む。失敗時は空インデックスを返す。"""
    #    try:
    #        with open(self._cache_path, "r", encoding="utf-8") as f:
    #            return json.load(f)
    #    except Exception:
    #        return _empty_index()

    # ------------------------------------------------------------------
    # FX-105: POST /api/index/refresh 排他制御付きリフレッシュ
    # ------------------------------------------------------------------

    def refresh(self) -> tuple:
        """
        GitHub インデックス JSON を強制再取得し、キャッシュを更新する。

        FD §4-3 準拠: 排他制御付きリフレッシュ処理。

        戻り値タプル（呼び出し元 _api_post_index_refresh() が解釈）:
          (True,  data)          : 成功
          (False, "in_progress") : _refreshing=True のため 409
          (False, "fetch_failed"): GitHub 取得失敗のため 503
          (False, "cache_failed"): キャッシュ保存失敗のため 500
        """
        with self._refresh_lock:
            if self._refreshing:
                logger.info("[FX-105] refresh: 並行リクエストを拒否（_refreshing=True）")
                return (False, "in_progress")
            self._refreshing = True

        try:
            logger.info("[FX-105] インデックス再取得開始: %s", self._url)
            try:
                req = urllib.request.Request(self._url)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read().decode("utf-8")
                data = json.loads(raw)
            except Exception as e:
                logger.warning("[FX-105] GitHub 取得失敗: %s", e)
                return (False, "fetch_failed")

            if not _validate_index(data):
                logger.warning("[FX-105] 取得データがスキーマ検証失敗")
                return (False, "fetch_failed")

            try:
                self._save_file_cache(data)
            except Exception as e:
                logger.warning("[FX-105] キャッシュ保存失敗: %s", e)
                return (False, "cache_failed")

            with self._lock:
                self._data       = data
                self._fetched_at = time.time()

            logger.info("[FX-105] インデックス再取得完了: %d モジュール", len(data.get("modules", [])))
            return (True, data)
        finally:
            # FD §4-3: 正常終了・例外終了を問わず finally で _refreshing をリセット
            with self._refresh_lock:
                self._refreshing = False


# ===========================================================================
# FX-005: PluginManager（プラグインロード・ルーティング）
# ===========================================================================

class PluginManager:
    """
    plugins/ ディレクトリ内の .py ファイルをロードし、
    リクエストパスを処理できる Plugin を検索するマネージャ。

    FD §5-3: sorted(key=str.lower) による辞書順ロードを仕様とする。
    FD §4-4: Plugin 名バリデーション・予約名スキップは Core 側の責務。
    """

    def __init__(self) -> None:
        self._lock   = threading.Lock()
        self.plugins: list = []

    def load_plugins(self) -> None:
        """plugins/ ディレクトリ内の .py ファイルを辞書順にロードする。"""
        os.makedirs(PLUGINS_DIR, exist_ok=True)
        try:
            files = [
                f for f in os.listdir(PLUGINS_DIR)
                if f.endswith(".py") and not f.startswith("_")
            ]
        except Exception as e:
            logger.error("[FX-005] plugins/ 読み取りエラー: %s", e)
            return

        for filename in sorted(files, key=str.lower):
            plugin_name = filename[:-3]
            if not VALID_PLUGIN_NAME.match(plugin_name):
                logger.warning("[FX-005] Pluginスキップ（名前不正）: %s", filename)
                continue
            if plugin_name in RESERVED_PLUGIN_NAMES:
                logger.warning("[FX-005] Pluginスキップ（予約名）: %s", filename)
                continue
            filepath = os.path.join(PLUGINS_DIR, filename)
            try:
                spec   = importlib.util.spec_from_file_location(plugin_name, filepath)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                plugin_cls = getattr(module, "Plugin")
                instance   = plugin_cls()
                self.plugins.append((plugin_name, instance))
                logger.info("[FX-005] Pluginロード成功: %s", plugin_name)
            except Exception as e:
                logger.warning("[FX-005] Pluginロード失敗（スキップ）: %s - %s", filename, e)

    def find_plugin(self, path: str):
        """パスを処理できる Plugin インスタンスを返す。見つからない場合は None。"""
        with self._lock:
            plugins_snapshot = list(self.plugins)
        for name, plugin in plugins_snapshot:
            try:
                if plugin.can_handle(path):
                    return plugin
            except Exception as e:
                logger.warning("[FX-005] can_handle 例外 [%s]: %s", name, e)
        return None

    def get_plugin_names(self) -> list:
        """ロード済み Plugin 名のリストを返す（Manager UI / API 用）"""
        with self._lock:
            return [name for name, _ in self.plugins]


# ===========================================================================
# FX-006: Scheduler（内蔵CRONスケジューラ）
# ===========================================================================

class Scheduler:
    """
    ループ型スケジューラ。FD §5-2 (Rev.5 修正版) 完全準拠。

    while True + sleep(1) ループで次回実行予定時刻を絶対時刻で管理する。

    排他制御 (FD §5-3 準拠):
      - running フラグの変更は必ず self._lock の保護下で行う
      - next_run の更新は func() の例外有無を問わず finally ブロックで行う
    """

    def __init__(self) -> None:
        self._jobs:   list                      = []
        self._lock:   threading.Lock            = threading.Lock()
        self._thread: "threading.Thread | None" = None

    def schedule(self, name: str, interval_minutes: int, func, timeout_minutes: int = 5) -> None:
        """ジョブを登録する。"""
        job = {
            "name":             name,
            "interval_seconds": interval_minutes * 60,
            "func":             func,
            "timeout_seconds":  timeout_minutes * 60,
            "next_run":         datetime.now() + timedelta(seconds=interval_minutes * 60),
            "running":          False,
        }
        with self._lock:
            self._jobs.append(job)
        logger.info("[FX-006] スケジュール登録: %s（%d分間隔）", name, interval_minutes)

    def get_jobs(self) -> list:
        """ジョブ情報のスナップショットを返す（Manager UI / API 表示用）"""
        with self._lock:
            return [
                {
                    "name":             j["name"],
                    "running":          j["running"],
                    "next_run":         (
                        j["next_run"].isoformat()
                        if isinstance(j["next_run"], datetime)
                        else str(j["next_run"])
                    ),
                    "interval_seconds": j["interval_seconds"],
                }
                for j in self._jobs
            ]

    def _run_job(self, job: dict) -> None:
        """
        別スレッドでジョブを実行する。FD §5-2 Rev.5 修正版の実装規約に完全準拠。
          running=True のセット: runner() 冒頭の with self._lock: 内（FD §5-2 修正点①）
          running=False のリセット + next_run 更新: finally ブロック内（FD §5-2 修正点②）
        【_loop() との責務分離】
          _loop() が running=True をロック内で予約セットしてからこのメソッドを呼び出す。
          runner() 内では running=True のセットを行わない（二重セット防止）。
          これにより _loop() のロック解放〜_run_job() 呼び出しまでの TOCTOU 競合を排除する。（G-04修正）
        """
        def runner() -> None:
            # running=True は _loop() 側の with self._lock: 内で予約セット済み（G-04修正）
            try:
                job["func"]()
            except Exception as e:
                logger.exception("[FX-006] ジョブ例外 [%s]: %s", job["name"], e)
            finally:
                # FD §5-2 修正点①②: running=False リセット + next_run 更新を finally 内のロックで実施
                with self._lock:
                    job["running"]  = False
                    job["next_run"] = datetime.now() + timedelta(seconds=job["interval_seconds"])

        t = threading.Thread(target=runner, daemon=True, name=f"sched-{job['name']}")
        t.start()

    def _loop(self) -> None:
        """スケジューラのメインループ。1秒ごとにジョブの実行要否を確認する。
        FD §5-2 Rev.6 準拠（G-04 TOCTOU 修正）:
          running=True の予約セットをロック内で行い、ロック解放後に _run_job() を呼ぶ。
          これにより running チェック〜_run_job() 呼び出しまでの TOCTOU 競合を完全排除する。
        """
        while True:
            now = datetime.now()
            with self._lock:
                jobs_snapshot = list(self._jobs)
            for job in jobs_snapshot:
                with self._lock:
                    if job["running"]:
                        continue
                    if now < job["next_run"]:
                        continue
                    # G-04修正: running=True をロック内で予約セットしてから _run_job() を呼ぶ
                    job["running"] = True
                self._run_job(job)
            time.sleep(1)

    def start(self) -> None:
        """スケジューラループをデーモンスレッドで起動する"""
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        logger.info("[FX-006] スケジューラ起動")

    def register_plugins(self, plugin_manager: "PluginManager") -> None:
        """Plugin が register_schedule() を持つ場合にスケジューラを渡す。FD §5-4 準拠。"""
        for name, plugin in plugin_manager.plugins:
            if hasattr(plugin, "register_schedule"):
                try:
                    plugin.register_schedule(self)
                    logger.info("[FX-006] スケジュール登録呼び出し: %s", name)
                except Exception as e:
                    logger.warning("[FX-006] register_schedule 例外 [%s]: %s", name, e)


# ===========================================================================
# FX-009: Response / RequestWrapper クラス
# ===========================================================================
#
# 【設計概要】FD §4-2-1「Responseインターフェース仕様（PluginとCoreの契約）」準拠。
#
# Plugin と Core の責務境界：
#   Plugin  : リクエストを処理し Response オブジェクトを返す（ソケット書き込み禁止）
#   Core    : Response を受け取り _send_response() でソケットに書き込む
#
# 【Plugin実装者向け利用方法】
#   from box_webserver import Response, RequestWrapper
#
#   class Plugin:
#       def can_handle(self, path: str) -> bool:
#           return path.startswith("/myplugin")
#
#       def handle(self, req: RequestWrapper) -> Response:
#           body = json.dumps({"ok": True}).encode("utf-8")
#           # ⚠ content_type は必ず明示すること（デフォルト "text/plain" に注意）
#           return Response(200, body, "application/json; charset=utf-8")
#
# ---------------------------------------------------------------------------


class RequestWrapper:
    """
    BaseHTTPRequestHandler を Plugin に渡す薄いラッパー。

    FD §4-2-1 (Responseインターフェース仕様) 準拠:
      Plugin は直接ソケットに書き込まず、必ず Response オブジェクトを返す。

    ⚠ FX-104 の multipart アップロードは read_body() を使わず、
      _api_post_module_upload() が self.rfile を直接読み取る設計とする。
      read_body() は通常の JSON ボディ専用（上限 _MAX_BODY_SIZE = 1MB）。
    """

    def __init__(self, handler: BaseHTTPRequestHandler) -> None:
        self._handler        = handler
        self.method:  str    = handler.command
        self.path:    str    = handler.path
        self.headers         = handler.headers

    def read_body(self) -> bytes:
        """
        Content-Length に基づいてリクエストボディを読み取る。
        上限: _MAX_BODY_SIZE（1MB）。超過分は切り捨て。
        """
        try:
            raw_length = self.headers.get("Content-Length", "0")
            length     = min(int(raw_length), _MAX_BODY_SIZE)
            if length > 0:
                return self._handler.rfile.read(length)
        except Exception:
            pass
        return b""


class Response:
    """
    Plugin が返すレスポンスオブジェクト。Plugin と Core の唯一の契約。

    FD §4-2-1 準拠:
      status       : int   HTTPステータスコード
      body         : bytes レスポンスボディ（必ずバイト列）
      content_type : str   Content-Type ヘッダ値

    ⚠ content_type のデフォルト値は "text/plain"。
      JSON を返す場合は "application/json; charset=utf-8" を明示すること。
    """

    def __init__(
        self,
        status: int = 200,
        body: bytes = b"",
        content_type: str = "text/plain",
    ) -> None:
        self.status:       int   = status
        self.body:         bytes = body
        self.content_type: str   = content_type


def _send_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    body: bytes,
    content_type: str,
) -> None:
    """
    Response オブジェクトの内容を HTTP レスポンスとしてソケットに送信する。

    FX-009 / FD §4-2-1 準拠。Core が一元的にレスポンス送信を担う。

    セキュリティヘッダ（全レスポンスに一律付与）:
      X-Content-Type-Options: nosniff    - MIME スニッフィング抑制
      X-Frame-Options: SAMEORIGIN        - クリックジャッキング対策
      Cache-Control: no-store            - 動的コンテンツのキャッシュ禁止
    """
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "SAMEORIGIN")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _parse_plugin_name(path: str) -> "str | None":
    """
    リクエストパスから Plugin 名（第1セグメント）を取り出す。
    FD §4-4 準拠: Plugin 名の検証は Core (_dispatch) のみで行う。

    戻り値:
      ""       : ルートアクセス（パスが "/" のみ）
      plugin名 : 有効な Plugin 名（VALID_PLUGIN_NAME 通過済み）
      None     : バリデーション失敗 → 400 Bad Request を返すべき状態
    """
    first = path.strip("/").split("/")[0]
    if not first:
        return ""
    if not VALID_PLUGIN_NAME.match(first):
        return None
    return first


# ===========================================================================
# Manager UI ─ HTML 生成ヘルパー群（FX-201〜213 担当範囲）
# ===========================================================================

def _build_stats_section(modules, plugin_names, scheduler_jobs, last_fetched):
    index_ids = {m["id"] for m in modules}
    return {
        "index_count":    len(modules),
        "deployed_count": sum(1 for n in plugin_names if n in index_ids),
        "private_count":  sum(1 for n in plugin_names if n not in index_ids),
        "job_count":      len(scheduler_jobs),
        "last_fetch_str": (
            datetime.fromtimestamp(last_fetched).strftime("%Y-%m-%d %H:%M")
            if last_fetched > 0 else "未取得"
        ),
    }


def _jesc(obj) -> str:
    """mod オブジェクトを HTML 属性に安全に埋め込むためのエスケープヘルパー。
    json.dumps で直列化後、HTML属性を壊す " を &quot; に変換する。
    G-12修正: uninstall_btn で &quot; エスケープが抜けていたバグを修正。
    """
    return json.dumps(obj).replace('"', '&quot;')


def _render_module_card(mod, plugin_names):
    mod_id        = mod.get("id", "")
    mod_name      = mod.get("name", mod_id)
    mod_desc      = mod.get("description", "")
    mod_status    = mod.get("status", "public")
    mod_ver       = mod.get("version", "")
    mod_author    = mod.get("author", "")
    mod_file      = mod.get("plugin_file", "")
    mod_ffmpeg    = mod.get("requires_ffmpeg", False)
    mod_hsbox_min = mod.get("hsbox_min_version", "")
    is_installed  = mod_id in plugin_names

    badge_cls      = f"badge-{mod_status}"

    # FX-205: Deploy/Re-deploy ボタンのディスパッチロジック (FD §7-3 H-03準拠)
    #
    # is_installed=true の場合: 全status共通で confirm-redeploy モーダルを経由する。
    # ❗ Re-deploy 時に新規インストール用の多段階確認を誤適用しないこと（FD §7-3）
    #
    # is_installed=false の場合: status に応じた確認フロー。
    #   public     -> 確認なしで doInstall() 直接 (FD H-09準拠)
    #   restricted -> confirm-install-restricted (1段階)
    #   private    -> confirm-install-private    (2段階・同一モーダル内 step 制御)
    if is_installed:
        # BK-04: v1 では Re-deploy ボタンを非表示化。v2 で再設計予定。
        # 運用: Remove → 再インストール（Deploy）で代替。
        deploy_btn_cls = "btn-warn"
        deploy_label   = "↺ Re-deploy"
        dialog_onclick = f"onclick=\"showDialog('redeploy',{_jesc(mod)})\" style=\"display:none\""
    elif mod_status == "restricted":
        # FX-203: confirm-install-restricted (1段階)
        deploy_btn_cls = "btn-warn"
        deploy_label   = "⚠ Deploy"
        dialog_onclick = f"onclick=\"showDialog('restricted',{_jesc(mod)})\""
    elif mod_status == "private":
        # FX-204: confirm-install-private (2段階・同一モーダル内 step 制御)
        deploy_btn_cls = "btn-danger"
        deploy_label   = "⚠⚠ Deploy"
        dialog_onclick = f"onclick=\"showDialog('private',{_jesc(mod)})\""
    else:
        # FX-202: public -> 確認なしで doInstall() 直接（FD H-09）
        deploy_btn_cls = "btn-primary"
        deploy_label   = "▼ Deploy"
        mod_id_js = f"'{mod_id}'"
        dialog_onclick = f'onclick="doInstall({mod_id_js})"'
        #dialog_onclick = f"onclick='doInstall({json.dumps(\"{mod_id}\")})'"

    tags_html = ""
    if mod_ffmpeg:
        tags_html += '<span class="tag">ffmpeg required</span>'
    if mod_hsbox_min:
        tags_html += f'<span class="tag">hsBox ≥ {mod_hsbox_min}</span>'

    installed_chip = (
        '<div class="installed-chip" style="margin-top:5px">✓ Installed</div>'
        if is_installed else ""
    )
    icon_map = {"atomcam2": "📷"}
    icon = icon_map.get(mod_id, "⬡")

    # FX-206: Uninstall ボタン（インストール済みの場合のみ表示）
    # FD §7-3 H-01: doUninstall() の直接呼び出しは禁止。
    # 必ず showDialog('uninstall', mod) を経由して confirm-uninstall モーダルを表示すること。
    uninstall_btn = (
        f'<button class="btn btn-danger btn-sm" '
        f'onclick="showDialog(\'uninstall\',{_jesc(mod)})">\U0001f5d1 Remove</button>'
        if is_installed else ""
    )

    return f"""
      <div class="mod-card {mod_status}" data-id="{mod_id}">
        <div class="mod-header">
          <div class="mod-icon">{icon}</div>
          <div class="mod-meta">
            <div class="mod-name">{mod_name}</div>
            <div class="mod-id">{mod_id} · {mod_file}</div>
          </div>
          <div class="mod-status-badge {badge_cls}">
            <span class="badge-dot"></span> {mod_status}
          </div>
        </div>
        <div class="mod-desc">{mod_desc}</div>
        <div class="mod-tags">{tags_html}</div>
        <div class="mod-footer">
          <div>
            <div class="mod-ver">v<span>{mod_ver}</span> · {mod_author}</div>
            {installed_chip}
          </div>
          <div style="display:flex;gap:8px">
            <div class="mod-state-chip" style="font-size:11px;min-height:18px"></div>
            <button class="btn btn-ghost btn-sm" onclick="showModInfo({_jesc(mod)})">詳細</button>
            {uninstall_btn}
            <button class="btn {deploy_btn_cls} btn-sm" {dialog_onclick}>{deploy_label}</button>
          </div>
        </div>
      </div>"""


def _build_index_cards(modules, plugin_names):
    return "".join(_render_module_card(mod, plugin_names) for mod in modules)


def _build_private_cards(plugin_names, index_ids):
    cards = ""
    for pname in plugin_names:
        if pname in index_ids:
            continue
        cards += f"""
      <div class="mod-card private">
        <div class="mod-header">
          <div class="mod-icon">🧪</div>
          <div class="mod-meta">
            <div class="mod-name">{pname}</div>
            <div class="mod-id">{pname} · {pname}.py</div>
          </div>
          <div class="mod-status-badge badge-private">
            <span class="badge-dot"></span> private
          </div>
        </div>
        <div class="mod-desc">手動でインストールされた Module です。</div>
        <div class="mod-tags">
          <span class="tag">手動インストール</span>
          <span class="tag">未確認</span>
        </div>
        <div class="mod-footer">
          <div class="mod-ver" style="color:var(--text-mid)">手動インストール済み</div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-danger btn-sm"
              onclick="showDialog('uninstall',{{id:'{pname}',name:'{pname}',plugin_file:'{pname}.py',status:'private'}})">
              🗑 Remove
            </button>
            <!-- BK-04: Re-deploy ボタンは v2 で再設計。運用: Remove → 再インストールで代替。 -->
          </div>
        </div>
      </div>"""
    return cards


def _build_scheduler_rows(scheduler_jobs):
    if not scheduler_jobs:
        return (
            '<tr><td colspan="4" style="color:var(--text-lo);font-size:12px;'
            'padding:12px">登録ジョブなし</td></tr>'
        )
    rows = ""
    for job in scheduler_jobs:
        running_badge = (
            '<span style="color:var(--accent)">▶ running</span>'
            if job["running"]
            else '<span style="color:var(--text-lo)">waiting</span>'
        )
        interval_min = job["interval_seconds"] // 60
        rows += f"""
        <tr>
          <td style="font-family:var(--mono);font-size:12px;color:var(--text-hi)">{job['name']}</td>
          <td style="font-family:var(--mono);font-size:11px;color:var(--text-lo)">{interval_min}min</td>
          <td>{running_badge}</td>
          <td style="font-family:var(--mono);font-size:11px;color:var(--text-lo)">{job['next_run']}</td>
        </tr>"""
    return rows


def _render_html_template(stats, index_cards_html, private_cards_html, sched_rows, cfg, last_fetched):
    notify_url      = cfg.get("status", "notify_url",      fallback="")
    default_context = cfg.get("loader", "default_context", fallback=DEFAULT_CONTEXT)
    index_url       = cfg.get("loader", "index_url",       fallback=DEFAULT_INDEX_URL)
    # FX-212: base_path を [server] セクションから取得し JS 変数 BP として埋め込む。
    #   ブラウザの fetch('/api/...') はApache proxy prefix がないと届かないため、
    #   Python がページ生成時に base_path を BP に展開して fetch(BP+'/api/...') とする。
    #   base_path=/freebox → BP='/freebox' → fetch('/freebox/api/..') → Apache転送 → /api/.. ✅
    #   base_path=''       → BP=''        → fetch('/api/..')         → 直接接続時 ✅
    _base_path      = cfg.get("server", "base_path", fallback="").rstrip("/")
    now_str         = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    index_count    = stats["index_count"]
    deployed_count = stats["deployed_count"]
    private_count  = stats["private_count"]
    job_count      = stats["job_count"]
    last_fetch_str = stats["last_fetch_str"]

    private_section = (
        private_cards_html
        if private_cards_html
        else '<div style="color:var(--text-lo);font-size:12px;font-family:var(--mono);padding:8px">ローカル Module なし</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>freeBox Loader – Module Manager</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Noto+Sans+JP:wght@300;400;500;700&display=swap');
  :root {{
    --bg-base:#0d0f12;--bg-panel:#13161b;--bg-card:#1a1e26;--bg-hover:#21262f;
    --border:#2a2f3a;--border-hi:#3a4050;
    --accent:#00c8a0;--accent-dim:#00c8a022;
    --warn:#f5a623;--warn-dim:#f5a62322;
    --danger:#ff4d6a;--danger-dim:#ff4d6a22;
    --text-hi:#f0f2f6;--text-mid:#a8b0be;--text-lo:#7a8494;
    --mono:'JetBrains Mono',monospace;--sans:'Noto Sans JP',sans-serif;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg-base);color:var(--text-hi);font-family:var(--sans);font-size:14px;min-height:100vh;line-height:1.6;padding-bottom:36px}}
  .topnav{{background:var(--bg-panel);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:0;height:48px;padding:0 0 0 16px;position:sticky;top:0;z-index:100}}
  .topnav-brand{{font-family:var(--mono);font-weight:700;font-size:15px;color:var(--accent);letter-spacing:.04em;padding-right:24px;border-right:1px solid var(--border)}}
  .topnav-brand span{{color:var(--text-mid);font-weight:400}}
  .topnav-tabs{{display:flex;height:100%;margin-left:8px}}
  .topnav-tab{{display:flex;align-items:center;padding:0 20px;font-size:13px;color:var(--text-mid);cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;text-decoration:none;white-space:nowrap}}
  .topnav-tab:hover{{color:var(--text-hi);background:var(--bg-hover)}}
  .topnav-tab.active{{color:var(--accent);border-bottom-color:var(--accent);background:var(--accent-dim)}}
  .topnav-spacer{{flex:1}}
  .topnav-meta{{display:flex;align-items:center;gap:16px;padding:0 16px;font-family:var(--mono);font-size:11px;color:var(--text-lo)}}
  .topnav-meta .dot{{width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 6px var(--accent);animation:pulse 2s infinite}}
  @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
  .layout{{display:grid;grid-template-columns:220px 1fr;min-height:calc(100vh - 48px)}}
  .sidebar{{background:var(--bg-panel);border-right:1px solid var(--border);padding:20px 0;display:flex;flex-direction:column;gap:2px}}
  .sidebar-section{{font-family:var(--mono);font-size:10px;color:var(--text-lo);letter-spacing:.12em;text-transform:uppercase;padding:12px 16px 4px}}
  .sidebar-item{{display:flex;align-items:center;gap:10px;padding:8px 16px;color:var(--text-mid);cursor:pointer;transition:all .12s;border-left:2px solid transparent;font-size:13px}}
  .sidebar-item:hover{{color:var(--text-hi);background:var(--bg-hover)}}
  .sidebar-item.active{{color:var(--accent);background:var(--accent-dim);border-left-color:var(--accent)}}
  .sidebar-item .icon{{width:16px;text-align:center;font-size:14px}}
  .sidebar-badge{{margin-left:auto;background:var(--border-hi);color:var(--text-mid);font-family:var(--mono);font-size:10px;padding:1px 6px;border-radius:3px}}
  .sidebar-divider{{height:1px;background:var(--border);margin:8px 16px}}
  .main{{padding:28px 32px;display:flex;flex-direction:column;gap:24px}}
  .page-header{{display:flex;align-items:flex-end;justify-content:space-between}}
  .page-title{{font-size:22px;font-weight:700;color:var(--text-hi);letter-spacing:-.02em}}
  .page-sub{{font-size:12px;color:var(--text-lo);font-family:var(--mono);margin-top:2px}}
  .page-actions{{display:flex;gap:10px;align-items:center}}
  .btn{{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:4px;font-size:13px;font-family:var(--sans);font-weight:500;cursor:pointer;border:1px solid transparent;transition:all .15s;text-decoration:none}}
  .btn-primary{{background:var(--accent);color:#000;border-color:var(--accent)}}
  .btn-primary:hover{{filter:brightness(1.1)}}
  .btn-ghost{{background:transparent;color:var(--text-mid);border-color:var(--border)}}
  .btn-ghost:hover{{border-color:var(--border-hi);color:var(--text-hi);background:var(--bg-hover)}}
  .btn-warn{{background:var(--warn-dim);color:var(--warn);border-color:var(--warn)}}
  .btn-warn:hover{{background:var(--warn);color:#000}}
  .btn-danger{{background:var(--danger-dim);color:var(--danger);border-color:var(--danger)}}
  .btn-danger:hover{{background:var(--danger);color:#fff}}
  .btn-danger:disabled,.btn-warn:disabled,.btn-primary:disabled,.btn-ghost:disabled{{opacity:.35;cursor:not-allowed;filter:none}}
  .btn-sm{{padding:4px 10px;font-size:12px}}
  .stats-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
  .stat-card{{background:var(--bg-card);border:1px solid var(--border);border-radius:6px;padding:14px 18px}}
  .stat-label{{font-family:var(--mono);font-size:10px;color:var(--text-lo);letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px}}
  .stat-value{{font-family:var(--mono);font-size:22px;font-weight:700;color:var(--text-hi);line-height:1}}
  .stat-value.accent{{color:var(--accent)}}.stat-value.warn{{color:var(--warn)}}
  .stat-sub{{font-size:11px;color:var(--text-lo);margin-top:4px}}
  .toolbar{{display:flex;align-items:center;gap:10px}}
  .search-box{{display:flex;align-items:center;background:var(--bg-card);border:1px solid var(--border);border-radius:4px;padding:6px 12px;gap:8px;flex:1;max-width:320px}}
  .search-box input{{background:none;border:none;outline:none;color:var(--text-hi);font-size:13px;font-family:var(--sans);width:100%}}
  .search-box input::placeholder{{color:var(--text-lo)}}
  .filter-tabs{{display:flex;gap:4px}}
  .filter-tab{{padding:5px 12px;border-radius:4px;font-size:12px;cursor:pointer;border:1px solid var(--border);color:var(--text-mid);transition:all .12px;font-family:var(--mono)}}
  .filter-tab:hover{{border-color:var(--border-hi);color:var(--text-hi)}}
  .filter-tab.active{{border-color:var(--accent);color:var(--accent);background:var(--accent-dim)}}
  .module-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}}
  .mod-card{{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:20px;display:flex;flex-direction:column;gap:14px;transition:border-color .15s,box-shadow .15s;position:relative;overflow:hidden}}
  .mod-card::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px}}
  .mod-card.public::before{{background:var(--accent)}}
  .mod-card.restricted::before{{background:var(--warn)}}
  .mod-card.private::before{{background:var(--danger)}}
  .mod-card:hover{{border-color:var(--border-hi);box-shadow:0 4px 24px rgba(0,0,0,.4)}}
  .mod-header{{display:flex;align-items:flex-start;gap:12px}}
  .mod-icon{{width:42px;height:42px;background:var(--bg-panel);border:1px solid var(--border);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}}
  .mod-meta{{flex:1;min-width:0}}
  .mod-name{{font-size:15px;font-weight:700;color:var(--text-hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .mod-id{{font-family:var(--mono);font-size:11px;color:var(--text-lo);margin-top:2px}}
  .mod-status-badge{{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:3px;font-family:var(--mono);font-size:10px;font-weight:600;letter-spacing:.06em;flex-shrink:0}}
  .badge-public{{background:var(--accent-dim);color:var(--accent);border:1px solid var(--accent)}}
  .badge-restricted{{background:var(--warn-dim);color:var(--warn);border:1px solid var(--warn)}}
  .badge-private{{background:var(--danger-dim);color:var(--danger);border:1px solid var(--danger)}}
  .badge-dot{{width:5px;height:5px;border-radius:50%;background:currentColor}}
  .mod-desc{{font-size:12px;color:var(--text-mid);line-height:1.6}}
  .mod-tags{{display:flex;flex-wrap:wrap;gap:6px}}
  .tag{{font-family:var(--mono);font-size:10px;color:var(--text-lo);background:var(--bg-panel);border:1px solid var(--border);padding:2px 7px;border-radius:3px}}
  .mod-footer{{display:flex;align-items:center;justify-content:space-between;padding-top:12px;border-top:1px solid var(--border)}}
  .mod-ver{{font-family:var(--mono);font-size:11px;color:var(--text-lo)}}
  .mod-ver span{{color:var(--text-mid)}}
  .installed-chip{{display:inline-flex;align-items:center;gap:5px;font-family:var(--mono);font-size:10px;color:var(--accent);background:var(--accent-dim);border:1px solid var(--accent);border-radius:3px;padding:2px 7px}}
  .section-title{{font-family:var(--mono);font-size:11px;color:var(--text-lo);letter-spacing:.1em;text-transform:uppercase;display:flex;align-items:center;gap:10px}}
  .section-title::after{{content:'';flex:1;height:1px;background:var(--border)}}
  .data-table{{width:100%;border-collapse:collapse}}
  .data-table th{{font-family:var(--mono);font-size:10px;color:var(--text-lo);letter-spacing:.1em;text-transform:uppercase;padding:8px 12px;border-bottom:1px solid var(--border);text-align:left;font-weight:400}}
  .data-table td{{padding:10px 12px;border-bottom:1px solid var(--border);font-size:13px;color:var(--text-mid)}}
  .data-table tr:last-child td{{border-bottom:none}}
  .panel-card{{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;overflow:hidden}}
  .panel-card-title{{background:var(--bg-panel);border-bottom:1px solid var(--border);padding:12px 16px;font-family:var(--mono);font-size:11px;color:var(--text-mid);letter-spacing:.06em;text-transform:uppercase}}
  .form-row{{display:flex;flex-direction:column;gap:6px}}
  .form-label{{font-family:var(--mono);font-size:11px;color:var(--text-lo);letter-spacing:.06em;text-transform:uppercase}}
  .form-input{{background:var(--bg-panel);border:1px solid var(--border);border-radius:4px;padding:8px 12px;font-family:var(--mono);font-size:12px;color:var(--text-hi);outline:none;transition:border-color .15s;width:100%}}
  .form-input:focus{{border-color:var(--accent)}}
  .form-hint{{font-size:11px;color:var(--text-lo);font-family:var(--mono)}}
  /* ===================================================================
     FX-209: RebootBanner スタイル
     FD §1-4 AppState.rebootRequired 準拠。
     Install / Uninstall / Upload 成功後に画面上部（topnav 直下）に固定表示する。
     「あとで」クリックで非表示 → AppState.rebootRequired = false にリセット。

     表示仕様:
       - position: fixed; top: 48px（topnav の高さ分だけ下）; left/right: 0
       - z-index: 98（topnav=100 より下、overlay=200 より下）
       - 背景: warn-dim（オレンジ系）、上下ボーダー: warn
       - アイコン + メッセージ + [今すぐ再起動] + [あとで] ボタン
       - アニメーション: rebootSlideDown（上から滑り込む）

     チェックリスト対応: A-04, A-05, A-06, I-06
     =================================================================== */
  .reboot-banner{{
    display:none;
    position:fixed;
    top:48px;
    left:0;right:0;
    background:#b87300;
    border-top:1px solid var(--warn);
    border-bottom:1px solid var(--warn);
    padding:10px 20px;
    z-index:98;
    align-items:center;
    gap:14px;
    font-family:var(--sans);
    font-size:13px;
    animation:rebootSlideDown .25s ease;
  }}
  .reboot-banner.show{{display:flex}}
  @keyframes rebootSlideDown{{
    from{{opacity:0;transform:translateY(-10px)}}
    to  {{opacity:1;transform:translateY(0)}}
  }}
  .reboot-banner-icon{{font-size:18px;flex-shrink:0}}
  .reboot-banner-msg{{
    flex:1;
    color:var(--warn);
    font-weight:500;
    line-height:1.45;
  }}
  .reboot-banner-msg small{{
    font-family:var(--mono);
    font-size:11px;
    color:var(--text-mid);
    font-weight:400;
    margin-left:8px;
  }}
  .reboot-banner-actions{{display:flex;gap:8px;flex-shrink:0}}
  /* ===================================================================
     FX-208: StatusBar スタイル
     FD §4-4 準拠: 30秒ポーリングで更新されるステータスバー。
     各アイテムは id 付きで JS から動的更新できるようにする。
     - .ok   : 正常（accent 緑）
     - .err  : 異常（danger 赤）
     - .warn : 不明（text-lo グレー）← unknown 表示用（FD F-05準拠）
     =================================================================== */
  .statusbar{{position:fixed;bottom:0;left:0;right:0;background:var(--bg-panel);border-top:1px solid var(--border);height:28px;display:flex;align-items:center;padding:0 16px;gap:20px;font-family:var(--mono);font-size:11px;color:var(--text-lo);z-index:99}}
  .statusbar-item{{display:flex;align-items:center;gap:6px}}
  .statusbar-item .ok{{color:var(--accent)}}
  .statusbar-item .err{{color:var(--danger)}}
  .statusbar-item .warn{{color:var(--text-lo)}}
  .statusbar-spacer{{flex:1}}
  .overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;align-items:center;justify-content:center;padding:20px}}
  .overlay.show{{display:flex}}
  .dialog{{background:var(--bg-card);border-radius:10px;width:100%;max-width:500px;box-shadow:0 8px 48px rgba(0,0,0,.6);overflow:hidden;animation:slideIn .2s ease}}
  @keyframes slideIn{{from{{opacity:0;transform:translateY(-12px) scale(.98)}}to{{opacity:1;transform:translateY(0) scale(1)}}}}
  .dialog-header{{padding:18px 22px;display:flex;align-items:center;gap:12px}}
  .dialog-header.restricted{{background:var(--warn-dim);border-bottom:1px solid var(--warn)}}
  .dialog-header.private{{background:var(--danger-dim);border-bottom:1px solid var(--danger)}}
  .dialog-header.public{{background:var(--accent-dim);border-bottom:1px solid var(--accent)}}
  .dialog-icon{{font-size:22px;width:40px;height:40px;border-radius:8px;display:flex;align-items:center;justify-content:center;flex-shrink:0}}
  .dialog-icon.restricted{{background:rgba(245,166,35,.15);border:1px solid var(--warn)}}
  .dialog-icon.private{{background:rgba(255,77,106,.15);border:1px solid var(--danger)}}
  .dialog-icon.public{{background:rgba(0,200,160,.15);border:1px solid var(--accent)}}
  .dialog-title.restricted{{font-size:16px;font-weight:700;color:var(--warn)}}
  .dialog-title.private{{font-size:16px;font-weight:700;color:var(--danger)}}
  .dialog-title.public{{font-size:16px;font-weight:700;color:var(--accent)}}
  .dialog-subtitle{{font-size:11px;color:var(--text-mid);margin-top:2px;font-family:var(--mono)}}
  .dialog-body{{padding:22px;display:flex;flex-direction:column;gap:16px;color:var(--text-hi);line-height:1.6}}
  .dialog-msg{{font-size:13.5px;color:var(--text-hi)}}
  .mod-info-table{{background:var(--bg-panel);border:1px solid var(--border);border-radius:6px;padding:14px;display:flex;flex-direction:column;gap:8px}}
  .mod-info-row{{display:flex;gap:8px;align-items:baseline}}
  .mod-info-key{{font-family:var(--mono);font-size:10px;color:var(--text-lo);width:90px;flex-shrink:0;text-transform:uppercase;letter-spacing:.08em}}
  .mod-info-val{{font-family:var(--mono);font-size:12px;color:var(--text-mid)}}
  .warn-box{{background:var(--warn-dim);border:1px solid var(--warn);border-radius:6px;padding:12px 14px;font-size:12.5px;color:var(--warn);display:flex;gap:10px;align-items:flex-start}}
  .danger-box{{background:var(--danger-dim);border:1px solid var(--danger);border-radius:6px;padding:14px;font-size:12.5px;color:var(--danger);display:flex;gap:10px;align-items:flex-start;line-height:1.65}}
  .check-row{{display:flex;align-items:flex-start;gap:10px;font-size:12.5px;color:var(--text-mid);cursor:pointer;line-height:1.5}}
  .check-row input[type=checkbox]{{margin-top:2px;width:14px;height:14px;flex-shrink:0;cursor:pointer}}
  .steps{{display:flex;align-items:center;padding:14px 22px;border-bottom:1px solid var(--border);background:var(--bg-panel)}}
  .step{{display:flex;align-items:center;gap:8px;flex:1}}
  .step-num{{width:24px;height:24px;border-radius:50%;font-family:var(--mono);font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:all .2s}}
  .step-num.active{{background:var(--danger);color:#fff}}
  .step-num.done{{background:var(--bg-hover);color:var(--accent);border:1px solid var(--accent)}}
  .step-num.pending{{background:var(--bg-hover);color:var(--text-lo);border:1px solid var(--border)}}
  .step-label{{font-size:12px}}
  .step-label.active{{color:var(--text-hi);font-weight:500}}
  .step-label.pending,.step-label.done{{color:var(--text-lo)}}
  .step-arrow{{color:var(--text-lo);font-size:12px;margin:0 8px}}
  .confirm-field-label{{font-family:var(--mono);font-size:11px;color:var(--text-lo);margin-bottom:4px}}
  .confirm-field{{background:var(--bg-card);border:1px solid var(--border);border-radius:4px;padding:8px 10px;font-family:var(--mono);font-size:12px;color:var(--text-hi);width:100%;outline:none;transition:border-color .15s}}
  .confirm-field:focus{{border-color:var(--danger)}}
  .confirm-hint{{font-size:11px;color:var(--text-lo);font-family:var(--mono);margin-top:6px}}
  .confirm-hint code{{background:var(--bg-hover);border:1px solid var(--border);padding:1px 5px;border-radius:3px;color:var(--text-mid)}}
  .dialog-footer{{padding:16px 22px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:10px;background:var(--bg-panel)}}
  .footer-step{{font-family:var(--mono);font-size:11px;color:var(--text-lo)}}
  .footer-step span{{color:var(--danger);font-weight:600}}
  .btn-group{{display:flex;gap:10px}}
  .tab-panel{{display:none}}.tab-panel.active{{display:flex;flex-direction:column;gap:24px}}
  /* hbx-upload モーダル固有スタイル */
  .upload-drop-area{{border:2px dashed var(--border-hi);border-radius:8px;padding:32px 20px;text-align:center;cursor:pointer;transition:border-color .15s,background .15s}}
  .upload-drop-area:hover,.upload-drop-area.dragover{{border-color:var(--accent);background:var(--accent-dim)}}
  .upload-filename{{font-family:var(--mono);font-size:12px;color:var(--accent);margin-top:8px;min-height:18px}}
  .upload-progress{{display:none;font-family:var(--mono);font-size:12px;color:var(--text-mid);margin-top:8px}}
  /* ===================================================================
     FX-210: Refresh Index ボタン状態スタイル
     FD §4-3 クライアント側フロー準拠。
     ボタンが処理中（disabled）のとき、視覚的に「実行中」を示す。
       .refreshing : ボタンテキストをスピナー表示に切り替える
     インライン通知エリア (#refresh-msg) のスタイル:
       成功  → color: var(--accent)（緑）
       409   → color: var(--warn)（オレンジ）
       エラー → color: var(--danger)（赤）
     チェックリスト対応: E-01, E-02, E-03
     =================================================================== */
  .refresh-msg{{
    font-family:var(--mono);
    font-size:11px;
    min-height:16px;
    transition:color .2s;
  }}
  .refresh-msg.ok   {{ color:var(--accent); }}
  .refresh-msg.warn {{ color:var(--warn);   }}
  .refresh-msg.err  {{ color:var(--danger); }}
  /* ===================================================================
     FX-209: Sidebar 再起動ボタン
     rebootRequired=true のとき display:flex で表示 + pulse アニメーション。
     通常時は display:none（非表示）。
     =================================================================== */
  .sidebar-reboot{{
    display:none;
    align-items:center;
    gap:8px;
    margin:4px 10px;
    padding:7px 12px;
    border-radius:4px;
    background:var(--warn-dim);
    border:1px solid var(--warn);
    color:var(--warn);
    font-size:12px;
    font-family:var(--sans);
    font-weight:600;
    cursor:pointer;
    transition:background .15s;
  }}
  .sidebar-reboot:hover{{ background:var(--warn); color:#000; }}
  .sidebar-reboot.active{{ display:flex; }}
  .sidebar-reboot .reboot-dot{{
    width:7px;height:7px;border-radius:50%;
    background:var(--warn);
    animation:pulse 1.2s infinite;
    flex-shrink:0;
  }}
</style>
</head>
<body>

<nav class="topnav">
  <div class="topnav-brand">free<span>Box</span> <span style="font-size:11px;color:var(--text-lo)">Loader</span></div>
  <div class="topnav-tabs">
    <a class="topnav-tab active" href="#">Manager</a>
  </div>
  <div class="topnav-spacer"></div>
  <div class="topnav-meta">
    <div class="dot"></div>
    <span>freebox.service running</span>
    <span style="color:var(--border-hi)">|</span>
    <a href="http://192.168.2.1/" style="color:var(--text-lo);text-decoration:none">◀ hsBox Top</a>
    <span style="color:var(--border-hi)">|</span>
    <a href="http://192.168.2.1/sp/" style="color:var(--text-lo);text-decoration:none">SP</a>
  </div>
</nav>

<!-- ===================================================================
     FX-209: RebootBanner HTML
     FD §1-4 AppState.rebootRequired 準拠。
     Install / Uninstall / Upload のいずれかが成功したとき JS の
     setRebootRequired(true) によって .show クラスが付与され表示される。

     チェックリスト:
       A-04: rebootRequired が boolean・初期値 false で定義されているか
       A-05: Install/Uninstall/Upload 成功時に rebootRequired = true になるか
       A-06: 「あとで」操作で rebootRequired = false にリセットされるか
       I-06: 成功後に RebootBanner が表示されるか

     ボタン仕様:
       [今すぐ再起動] : window.location.href で hsBox 再起動案内ページへ遷移
                       （hsBox の再起動は Python サーバー側では実行不可のため
                         ユーザーに操作を促す案内に留める）
       [あとで]       : setRebootRequired(false) を呼んでバナーを非表示にする
                       FD §1-4 A-06 準拠: rebootRequired = false にリセット
     =================================================================== -->
<div class="reboot-banner" id="reboot-banner" role="alert" aria-live="polite">
  <div class="reboot-banner-icon">⚠️</div>
  <div class="reboot-banner-msg">
    Module の変更が完了しました。有効化するには <strong>hsBox 全体の再起動</strong>が必要です。
    <small id="reboot-banner-detail"></small>
  </div>
  <div class="reboot-banner-actions">
    <button class="btn btn-ghost btn-sm"
      style="border-color:var(--warn);color:var(--warn)"
      onclick="hideBannerOnly()">
      後で再起動
    </button>
    <button class="btn btn-warn btn-sm"
      onclick="onRebootNow()">
      🔄 今すぐ再起動
    </button>
  </div>
</div>

<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-section">Navigate</div>
    <div class="sidebar-item active" onclick="switchTab('modules')">
      <span class="icon">⬡</span> Module List
      <span class="sidebar-badge">{index_count}</span>
    </div>
    <div class="sidebar-item" onclick="switchTab('modules')">
      <span class="icon">↓</span> Deployed
      <span class="sidebar-badge">{deployed_count}</span>
    </div>
    <div class="sidebar-item" onclick="switchTab('modules')">
      <span class="icon">🔴</span> Local / Private
      <span class="sidebar-badge">{private_count}</span>
    </div>
    <div class="sidebar-divider"></div>
    <div class="sidebar-section">System</div>
    <div class="sidebar-item" onclick="switchTab('scheduler')">
      <span class="icon">●</span> Scheduler
      <span class="sidebar-badge">{job_count}</span>
    </div>
    <div class="sidebar-item" onclick="switchTab('settings')">
      <span class="icon">⚙</span> Settings
    </div>
    <!-- FX-209: Sidebar 再起動ボタン。rebootRequired=true のとき .active で点滅表示 -->
    <button class="sidebar-reboot" id="sidebar-reboot-btn" onclick="onRebootNow()">
      <span class="reboot-dot"></span>
      🔄 再起動が必要です
    </button>
    <div class="sidebar-divider"></div>
    <div style="padding:12px 16px;margin-top:auto">
      <div style="font-family:var(--mono);font-size:10px;color:var(--text-lo);margin-bottom:6px">INDEX</div>
      <div style="font-family:var(--mono);font-size:10px;color:var(--text-lo)">Last fetch:<br>{last_fetch_str}</div>
      <div style="font-family:var(--mono);font-size:10px;color:var(--text-lo);margin-top:4px">schema v1</div>
    </div>
  </aside>

  <main class="main">

    <div id="tab-modules" class="tab-panel active">
      <div class="page-header">
        <div>
          <div class="page-title">Module Manager</div>
          <div class="page-sub">hoscm/freebox · docs/index.json</div>
        </div>
        <div class="page-actions">
          <!-- ===================================================================
               FX-210: Refresh Index ボタンと通知エリア
               FD §4-3 クライアント側フロー準拠。

               ボタン仕様:
                 - id="btn-refresh" : JS から disabled 制御するために id を付与
                 - クリック時: refreshIndex() を呼び出す
                 - 処理中: disabled = true（並行クリック防止 FD E-01準拠）
                 - 完了時: disabled = false に戻す（FD E-02準拠）

               通知エリア（#refresh-msg）:
                 - ボタン直下に配置し、ページ遷移なしでインライン表示
                 - 成功: "✓ N modules" を accent 色で表示
                 - 409: "更新中です。しばらくお待ちください。" を warn 色で表示
                 - エラー: error_message を danger 色で表示

               前回実装との差分:
                 旧: onclick="refreshIndex()" → alert() + location.reload()
                 新: ボタン disabled 制御 + GET /api/modules 再取得 + インライン通知
                   location.reload() を廃止し、_rebuildModuleGrid() でカードを差分更新
               チェックリスト: E-01, E-02, E-03
               =================================================================== -->
          <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
            <button class="btn btn-ghost" id="btn-refresh" onclick="refreshIndex()">↺ Refresh Index</button>
            <div class="refresh-msg" id="refresh-msg" role="status" aria-live="polite"></div>
          </div>
          <button class="btn btn-ghost" onclick="openUploadModal()">＋ Deploy Module</button>
        </div>
      </div>

      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-label">Index Modules</div>
          <div class="stat-value accent" id="stat-index-count">{index_count}</div>
          <div class="stat-sub">公開インデックス登録数</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Deployed</div>
          <div class="stat-value" id="stat-deployed-count">{deployed_count}</div>
          <div class="stat-sub">インストール済み</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Local / Private</div>
          <div class="stat-value warn" id="stat-private-count">{private_count}</div>
          <div class="stat-sub">手動インストール済み</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Scheduler Jobs</div>
          <div class="stat-value">{job_count}</div>
          <div class="stat-sub">登録ジョブ数</div>
        </div>
      </div>

      <div class="toolbar">
        <div class="search-box">
          <span style="color:var(--text-lo)">🔍</span>
          <input type="text" id="searchInput" placeholder="Module 名・ID で検索…" oninput="filterCards()">
        </div>
        <div class="filter-tabs">
          <div class="filter-tab active" onclick="setFilter('all',this)">ALL</div>
          <div class="filter-tab" onclick="setFilter('public',this)">🟢 public</div>
          <div class="filter-tab" onclick="setFilter('restricted',this)">🟡 restricted</div>
          <div class="filter-tab" onclick="setFilter('private',this)">🔴 private</div>
        </div>
      </div>

      <div class="section-title">Index Registered Modules</div>
      <div class="module-grid" id="indexGrid">
{index_cards_html}
      </div>

      <div class="section-title" style="margin-top:8px">Local / Private Modules</div>
      <div class="module-grid" id="privateGrid">
{private_section}
      </div>
    </div>

    <div id="tab-scheduler" class="tab-panel">
      <div class="page-header">
        <div>
          <div class="page-title">Scheduler</div>
          <div class="page-sub">登録ジョブ一覧</div>
        </div>
      </div>
      <div class="panel-card">
        <div class="panel-card-title">Active Jobs</div>
        <table class="data-table">
          <thead>
            <tr><th>Job Name</th><th>Interval</th><th>Status</th><th>Next Run</th></tr>
          </thead>
          <tbody>{sched_rows}</tbody>
        </table>
      </div>
    </div>

    <div id="tab-settings" class="tab-panel">
      <div class="page-header">
        <div>
          <div class="page-title">Settings</div>
          <div class="page-sub">freebox_config.ini</div>
        </div>
      </div>
      <div class="panel-card">
        <div class="panel-card-title">Loader</div>
        <div style="padding:18px;display:flex;flex-direction:column;gap:16px">
          <div class="form-row">
            <label class="form-label">Default Context（ルートアクセス転送先）</label>
            <input class="form-input" id="cfg_default_context" type="text" value="{default_context}" placeholder="manager">
            <div class="form-hint">/ へのアクセスをこのサブコンテキストに転送します。初期値: manager</div>
          </div>
          <div class="form-row">
            <label class="form-label">Index URL</label>
            <input class="form-input" id="cfg_index_url" type="text" value="{index_url}">
          </div>
        </div>
      </div>
      <div class="panel-card">
        <div class="panel-card-title">Status Notification</div>
        <div style="padding:18px;display:flex;flex-direction:column;gap:16px">
          <div class="form-row">
            <label class="form-label">Notify URL</label>
            <input class="form-input" id="cfg_notify_url" type="text" value="{notify_url}" placeholder="http://192.168.x.x/notify">
            <div class="form-hint">未設定の場合は通知をスキップします</div>
          </div>
        </div>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:10px">
        <button class="btn btn-ghost" onclick="loadSettings()">↺ リセット</button>
        <button class="btn btn-primary" onclick="saveSettings()">保存</button>
      </div>
      <div id="settingMsg" style="font-family:var(--mono);font-size:12px;color:var(--accent);text-align:right;min-height:20px"></div>
    </div>

  </main>
</div>

<!-- ===================================================================
     FX-208: StatusBar HTML
     FD §4-4 / §1-4 AppState.status 準拠。
     各アイテムに id を付与し JS の updateStatusBar() から動的更新できる。

     表示対象フィールド (FD §4-1-6 GET /api/status レスポンス準拠):
       sb-freebox : freebox_service ("running" | "stopped")
       sb-nas     : nas            ("connected" | "disconnected" | "unknown")
       sb-apache  : apache2        ("running"   | "stopped"      | "unknown")
       sb-sched   : scheduler_jobs (整数 jobs 数)
       sb-index   : index modules 数（ポーリング対象外・HTML 生成時の静的値のまま）
       sb-time    : 時刻（JS setInterval で1秒更新）

     初期表示: FD §1-4 AppState.status 初期値に準拠し "unknown" / 「取得中」表示。
     ポーリング: 初回即時実行後 30 秒ごとに更新（FD §4-4 F-01〜F-05 準拠）。
     =================================================================== -->
<div class="statusbar">
  <div class="statusbar-item" id="sb-freebox">
    <span class="warn">●</span> freebox: <span id="sb-freebox-val">取得中</span>
  </div>
  <div class="statusbar-item" id="sb-nas">
    <span class="warn">●</span> NAS: <span id="sb-nas-val">取得中</span>
  </div>
  <div class="statusbar-item" id="sb-apache">
    <span class="warn">●</span> apache2: <span id="sb-apache-val">取得中</span>
  </div>
  <div class="statusbar-item" id="sb-sched">
    <span class="warn">●</span> Scheduler: <span id="sb-sched-val">取得中</span>
  </div>
  <div class="statusbar-item" id="sb-index">
    <span class="ok">●</span> Index: <span id="sb-index-val">{index_count}</span> modules
  </div>
  <div class="statusbar-spacer"></div>
  <div class="statusbar-time" id="sb-time">{now_str}</div>
</div>

<!-- confirm-uninstall ダイアログ (FX-206: FD §7-3, §7-5: 1段階・全status共通) -->
<div class="overlay" id="dlg-uninstall">
  <div class="dialog" style="border-color:var(--danger)">
    <div class="dialog-header private">
      <div class="dialog-icon private">🗑</div>
      <div>
        <div class="dialog-title private">Uninstall 確認</div>
        <div class="dialog-subtitle" id="dun-subtitle">全status共通 — Module の削除</div>
      </div>
    </div>
    <div class="dialog-body">
      <div class="dialog-msg">
        この Module を
        <strong style="color:var(--danger)">アンインストール（削除）</strong>します。
        plugins/ ディレクトリからプラグインファイルを削除します。
      </div>
      <div class="mod-info-table" id="dun-info"></div>
      <div class="danger-box">
        <span style="flex-shrink:0">🗑</span>
        <span>Uninstall 後は hsBox 全体の再起動が必要です。
        削除後に元に戻すには Re-deploy が必要になります。</span>
      </div>
      <label class="check-row">
        <input type="checkbox" id="dun-chk" onchange="document.getElementById('dun-exec').disabled=!this.checked">
        削除することに同意します
      </label>
    </div>
    <div class="dialog-footer">
      <div></div>
      <div class="btn-group">
        <button class="btn btn-ghost" onclick="closeDialog('dlg-uninstall')">キャンセル</button>
        <button class="btn btn-danger" id="dun-exec" disabled onclick="doUninstall(_currentMod.id);closeDialog('dlg-uninstall')">🗑 削除する</button>
      </div>
    </div>
  </div>
</div>

<!-- confirm-redeploy ダイアログ (FX-205: FD §7-3, §7-5: 1段階・全status共通) -->
<div class="overlay" id="dlg-redeploy">
  <div class="dialog" style="border-color:var(--warn)">
    <div class="dialog-header restricted">
      <div class="dialog-icon restricted">↺</div>
      <div>
        <div class="dialog-title restricted">Re-deploy 確認</div>
        <div class="dialog-subtitle" id="drd-subtitle">全status共通 — インストール済み Module の上書き</div>
      </div>
    </div>
    <div class="dialog-body">
      <div class="dialog-msg">
        この Module はすでにインストールされています。リポジトリの最新バージョンで
        <strong style="color:var(--warn)">上書きインストール（Re-deploy）</strong>します。
      </div>
      <div class="mod-info-table" id="drd-info"></div>
      <div class="warn-box">
        <span style="flex-shrink:0">⚠</span>
        <span>Re-deploy 後は hsBox 全体の再起動が必要です。
        現在のプラグインファイルは上書きされます。</span>
      </div>
      <label class="check-row">
        <input type="checkbox" id="drd-chk" onchange="document.getElementById('drd-exec').disabled=!this.checked">
        上書きインストールに同意します
      </label>
    </div>
    <div class="dialog-footer">
      <div></div>
      <div class="btn-group">
        <button class="btn btn-ghost" onclick="closeDialog('dlg-redeploy')">\u30ad\u30e3\u30f3\u30bb\u30eb</button>
        <button class="btn btn-warn" id="drd-exec" disabled onclick="doRedeploy()">\u21ba \u4e0a\u66f8\u304d\u30a4\u30f3\u30b9\u30c8\u30fc\u30eb</button>
      </div>
    </div>
  </div>
</div>

<!-- confirm-install-restricted ダイアログ (FD §7-3, §7-5) -->
<div class="overlay" id="dlg-restricted">
  <div class="dialog" style="border-color:var(--warn)">
    <div class="dialog-header restricted">
      <div class="dialog-icon restricted">⚠</div>
      <div>
        <div class="dialog-title restricted">限定公開 Module の Deploy 確認</div>
        <div class="dialog-subtitle" id="dr-subtitle">restricted — 動作確認中の Module</div>
      </div>
    </div>
    <div class="dialog-body">
      <div class="dialog-msg">
        このModuleは<strong style="color:var(--warn)">限定公開（restricted）</strong>ステータスです。
        hoscm/freebox リポジトリにマージ済みですが、まだ動作確認中のため一般公開前のバージョンです。
      </div>
      <div class="mod-info-table" id="dr-info"></div>
      <div class="warn-box">
        <span style="flex-shrink:0">⚠</span>
        <span>Deploy 後は hsBox 全体の再起動が必要です。ffmpeg が未インストールの場合、スケジューラジョブはスキップされます。</span>
      </div>
      <label class="check-row">
        <input type="checkbox" id="dr-chk" onchange="document.getElementById('dr-exec').disabled=!this.checked">
        この Module が限定公開ステータスであることを理解した上で deploy します
      </label>
    </div>
    <div class="dialog-footer">
      <div></div>
      <div class="btn-group">
        <button class="btn btn-ghost" onclick="closeDialog('dlg-restricted')">キャンセル</button>
        <button class="btn btn-warn" id="dr-exec" disabled onclick="doInstall(_currentMod.id);closeDialog('dlg-restricted')">⚠ Deploy 実行</button>
      </div>
    </div>
  </div>
</div>

<!-- confirm-install-private ダイアログ (FD §7-4, §7-5: 単一ID・内部 step 制御) -->
<div class="overlay" id="dlg-private">
  <div class="dialog" style="border-color:var(--danger)">
    <div class="dialog-header private">
      <div class="dialog-icon private">🚨</div>
      <div>
        <div class="dialog-title private">非公開 Module の Deploy 確認</div>
        <div class="dialog-subtitle">private — インデックス未登録・出所不明の Module</div>
      </div>
    </div>
    <div class="steps">
      <div class="step">
        <div class="step-num" id="dp-s1n">1</div>
        <div class="step-label" id="dp-s1l">リスク確認</div>
      </div>
      <div class="step-arrow">→</div>
      <div class="step">
        <div class="step-num pending" id="dp-s2n">2</div>
        <div class="step-label pending" id="dp-s2l">自己責任確認</div>
      </div>
    </div>
    <div id="dp-panel1" class="dialog-body">
      <div class="dialog-msg">
        この Module は <strong style="color:var(--danger)">hoscm/freebox リポジトリに登録されていません。</strong><br>
        出所不明のコードを実行することになります。本当に続けますか？
      </div>
      <div class="mod-info-table" id="dp-info1"></div>
      <div class="danger-box">
        <span style="flex-shrink:0;font-size:16px">🚨</span>
        <span>この Module は hoscm 公式リポジトリによる信頼性チェックを受けていません。悪意あるコードが含まれる可能性があります。<strong>自己責任でのみ実行してください。</strong></span>
      </div>
      <label class="check-row">
        <input type="checkbox" id="dp-chk1" onchange="document.getElementById('dp-next').disabled=!this.checked">
        リスクを理解した上で、次のステップへ進みます
      </label>
    </div>
    <div id="dp-panel2" class="dialog-body" style="display:none">
      <div class="dialog-msg">最終確認：<strong style="color:var(--danger)">自己責任で実行することを明示的に宣言</strong>してください。</div>
      <div class="mod-info-table" id="dp-info2"></div>
      <div style="background:var(--danger-dim);border:1px solid var(--danger);border-radius:6px;overflow:hidden">
        <div style="background:rgba(255,77,106,.1);border-bottom:1px solid var(--danger);padding:10px 14px;font-size:12px;color:var(--danger);font-weight:600">🔴 自己責任確認</div>
        <div style="padding:14px;background:var(--bg-panel);display:flex;flex-direction:column;gap:12px">
          <div>
            <div class="confirm-field-label">Module ID を入力して確認してください</div>
            <input type="text" class="confirm-field" id="dp-confirmInput" placeholder=""
              oninput="updateDpStep2()" autocomplete="off" spellcheck="false">
            <div class="confirm-hint">確認のため Module ID を正確に入力してください</div>
          </div>
          <label class="check-row">
            <input type="checkbox" id="dp-chk2" onchange="updateDpStep2()" style="accent-color:var(--danger)">
            この Module を自己責任で実行することを確認しました。
          </label>
        </div>
      </div>
    </div>
    <div class="dialog-footer">
      <div class="footer-step">Step <span id="dp-stepnum">1</span> / 2</div>
      <div class="btn-group">
        <button class="btn btn-ghost" onclick="closeDialog('dlg-private')">キャンセル</button>
        <button class="btn" id="dp-next" onclick="dpGoStep2()" disabled
          style="background:var(--bg-hover);color:var(--text-hi);border-color:var(--border-hi);
                 font-weight:600;display:inline-flex;align-items:center;gap:6px;
                 padding:8px 18px;border-radius:5px;font-size:13px;cursor:pointer;border:1px solid">
          次へ →
        </button>
        <button class="btn btn-danger" id="dp-exec" style="display:none" disabled onclick="doInstall(_currentMod.id);closeDialog('dlg-private')">🚨 Deploy 実行</button>
      </div>
    </div>
  </div>
</div>

<!-- hbx-upload モーダル (FX-207: FD §7-3, §7-5) -->
<div class="overlay" id="dlg-upload">
  <div class="dialog" style="border-color:var(--accent);max-width:480px">
    <div class="dialog-header public">
      <div class="dialog-icon public">📦</div>
      <div>
        <div class="dialog-title public">ローカル .hbx アップロード</div>
        <div class="dialog-subtitle">private Module のデプロイ</div>
      </div>
    </div>
    <div class="dialog-body">
      <div class="dialog-msg" style="font-size:12.5px;color:var(--text-mid)">
        ローカルの <code style="font-family:var(--mono);color:var(--accent)">.hbx</code> ファイルを選択してアップロードします。<br>
        アップロード後は <strong style="color:var(--warn)">hsBox 全体の再起動</strong>が必要です。<br>
        ※ 既にインストール済みの Module ID は Re-deploy（Index 経由）を使用してください。
      </div>
      <div class="upload-drop-area" id="uploadDropArea" onclick="document.getElementById('uploadFileInput').click()">
        <div style="font-size:32px">📂</div>
        <div style="font-size:13px;color:var(--text-mid);margin-top:8px">.hbx ファイルをドロップ、またはクリックして選択</div>
        <div class="upload-filename" id="uploadFilename"></div>
      </div>
      <input type="file" id="uploadFileInput" accept=".hbx" style="display:none" onchange="onUploadFileSelect(this)">
      <div class="upload-progress" id="uploadProgress">アップロード中...</div>
      <div id="uploadMsg" style="font-family:var(--mono);font-size:12px;min-height:18px"></div>
    </div>
    <div class="dialog-footer">
      <div></div>
      <div class="btn-group">
        <button class="btn btn-ghost" onclick="closeDialog('dlg-upload')">キャンセル</button>
        <button class="btn btn-primary" id="upload-exec" disabled onclick="doUpload()">▲ アップロード</button>
      </div>
    </div>
  </div>
</div>

<!-- Module 詳細ダイアログ -->
<div class="overlay" id="dlg-info">
  <div class="dialog" style="border-color:var(--border-hi);max-width:460px">
    <div class="dialog-header" style="background:var(--bg-panel);border-bottom:1px solid var(--border)">
      <div class="dialog-icon" style="background:var(--bg-hover);border:1px solid var(--border)">⬡</div>
      <div>
        <div style="font-size:16px;font-weight:700;color:var(--text-hi)" id="di-title">Module Info</div>
        <div class="dialog-subtitle" id="di-sub"></div>
      </div>
    </div>
    <div class="dialog-body" id="di-body"></div>
    <div class="dialog-footer">
      <div></div>
      <button class="btn btn-ghost" onclick="closeDialog('dlg-info')">閉じる</button>
    </div>
  </div>
</div>

<script>
/* FX-212: base_path（Apache proxy prefix）をPythonがページ生成時に展開 */
var BP = '{_base_path}';
var _currentTab = 'modules';
function switchTab(name) {{
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  _currentTab = name;
}}
function updateClock() {{
  var d = new Date();
  var s = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0')
    + ' ' + String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0') + ':' + String(d.getSeconds()).padStart(2,'0');
  var el = document.getElementById('sb-time');
  if (el) el.textContent = s;
}}
setInterval(updateClock, 1000);
var _filter = 'all';
function setFilter(f, el) {{
  _filter = f;
  document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  filterCards();
}}
function filterCards() {{
  var q = document.getElementById('searchInput').value.toLowerCase();
  document.querySelectorAll('.mod-card').forEach(card => {{
    var txt = card.textContent.toLowerCase();
    var cls = card.className;
    var statusMatch = (_filter === 'all') || cls.includes(_filter);
    var textMatch   = !q || txt.includes(q);
    card.style.display = (statusMatch && textMatch) ? '' : 'none';
  }});
}}

/* ===================================================================
   FX-210: refreshIndex() – Refresh Index ボタンのメインハンドラ
   FD §4-3 クライアント側フロー準拠

   【処理フロー（FD §4-3 クライアント側フロー準拠）】
   1. btn-refresh を disabled にする（並行クリック防止: FD E-01準拠）
   2. refresh-msg をクリアし「更新中…」表示
   3. POST /api/index/refresh を送信
   4. レスポンス受信後:
      - 成功（200）: GET /api/modules を呼び出して再取得・再描画（FD E-03準拠）
      - 409 refresh_in_progress: 専用メッセージを warn 色で表示
      - その他エラー: error_message を danger 色で表示
   5. いずれの場合もボタンを enabled に戻す（FD E-02準拠）

   【前回実装との差分】
   - 旧実装: ボタン disabled 制御なし、alert()、location.reload()
   - 新実装:
       ① ボタン disabled 制御を追加（E-01/E-02）
       ② alert() を廃止 → #refresh-msg へのインライン表示に変更
       ③ location.reload() を廃止 → GET /api/modules + _rebuildModuleGrid() に変更（E-03）
         ページ全体を再読み込みせず、カードエリアのみ差分更新する。
         これにより操作状態（フィルタ・検索クエリ）が維持される。

   【注意】
   _msgAutoHideTimer を使って成功メッセージを 4 秒後に自動クリアする。
   エラー・409 メッセージは自動クリアしない（ユーザーが確認できるまで表示）。
   =================================================================== */
var _refreshMsgTimer = null;

function refreshIndex() {{
  var btn = document.getElementById('btn-refresh');
  var msg = document.getElementById('refresh-msg');
  if (!btn || !msg) return;

  /* --- FD E-01: ボタンを disabled にして並行クリックを防止 --- */
  btn.disabled = true;
  _setRefreshMsg('', '');  /* クリア */
  _setRefreshMsg('更新中…', 'warn');

  fetch(BP + '/api/index/refresh', {{ method: 'POST' }})
    .then(function(r) {{
      var status = r.status;
      return r.json().then(function(d) {{
        return {{ status: status, data: d }};
      }}).catch(function() {{
        return {{ status: status, data: {{}} }};
      }});
    }})
    .then(function(res) {{
      var s = res.status;
      var d = res.data;

      if (s === 200) {{
        /* --- FD E-03: 成功 → GET /api/modules で一覧を再取得・再描画 --- */
        var count = d.module_count !== undefined ? d.module_count : '?';
        _setRefreshMsg('\u2713 ' + count + ' modules を取得しました', 'ok', true);
        _fetchAndRebuildModuleGrid();
      }} else if (s === 409) {{
        /* --- 409 refresh_in_progress: 専用メッセージ（FD §4-3 クライアントフロー） --- */
        _setRefreshMsg(
          '\u26a0 ' + (d.error_message || '更新中です。しばらくお待ちください。'),
          'warn'
        );
      }} else {{
        /* --- その他エラー: error_message を表示 --- */
        _setRefreshMsg(
          '\u2717 ' + (d.error_message || d.error || 'エラーが発生しました（HTTP ' + s + '）'),
          'err'
        );
      }}
    }})
    .catch(function(e) {{
      /* --- ネットワークエラー --- */
      _setRefreshMsg('\u2717 ネットワークエラー: ' + e, 'err');
    }})
    .finally(function() {{
      /* --- FD E-02: 成功・失敗を問わずボタンを enabled に戻す --- */
      if (btn) btn.disabled = false;
    }});
}}

/**
 * refresh-msg エリアにメッセージを表示するヘルパー。
 *
 * @param {{{{string}}}} text      - 表示テキスト
 * @param {{{{string}}}} cssClass  - 'ok' | 'warn' | 'err' | '' (クリア)
 * @param {{{{boolean}}}} autoHide - true のとき 4 秒後に自動クリア（成功メッセージ用）
 */
function _setRefreshMsg(text, cssClass, autoHide) {{
  var msg = document.getElementById('refresh-msg');
  if (!msg) return;

  /* 前回の自動クリアタイマーをキャンセル */
  if (_refreshMsgTimer) {{
    clearTimeout(_refreshMsgTimer);
    _refreshMsgTimer = null;
  }}

  msg.textContent  = text;
  msg.className    = 'refresh-msg' + (cssClass ? ' ' + cssClass : '');

  /* 成功メッセージは 4 秒後に自動クリア */
  if (autoHide) {{
    _refreshMsgTimer = setTimeout(function() {{
      msg.textContent = '';
      msg.className   = 'refresh-msg';
      _refreshMsgTimer = null;
    }}, 4000);
  }}
}}

/**
 * GET /api/modules を呼び出し、返ってきたモジュール一覧で
 * IndexGrid / PrivateGrid を差分更新する。
 *
 * FD §4-3 E-03 / §4-5 操作フロー対応表 [↺ Refresh Index] の行準拠:
 *   「GET /api/modules を再送信してモジュール一覧を再取得・再描画」
 *
 * 設計判断（前回実装との差分）:
 *   旧: location.reload() によるページ全体リロード
 *   新: fetch('/api/modules') → _rebuildModuleGrid() でカードエリアのみ更新
 *     - フィルタ状態・検索クエリがリセットされない
 *     - ページ全体のフラッシュ（白い点滅）が発生しない
 *     - 実装上の注意: サーバー側 GET /api/modules のレスポンスは
 *       Python の _api_get_modules() が生成する JSON であり、
 *       カード HTML はクライアント側で JS から組み立てなければならない。
 *       ただし既存実装の _build_manager_html() はサーバー側で HTML を生成するため、
 *       クライアント側での JSON→カード変換は簡易版で実装する。
 *       詳細な表示（アイコン・フルCSS）はページリロード後（再起動後など）の
 *       フル再描画に委ねる。
 *
 * 実装上の制約:
 *   現行の Manager UI は Python 側でカード HTML を生成してページに埋め込む方式。
 *   JS からの完全な再描画には JSON を受け取ってカード HTML を組み立てる
 *   クライアント側レンダラが必要だが、現時点ではその実装が大規模になる。
 *   そのため、以下の簡略化された方針を取る:
 *     1. GET /api/modules を呼び出す
 *     2. 成功したら: ステータスバーの Index 数を更新 + ヘッダ stat-card を更新
 *     3. ページリロードは行わず、差分更新で対応できる部分だけ更新する
 *     4. カードの完全再描画は行わない（ユーザーが手動でリロードするか、
 *        次回フルリロード時に反映される）
 *   これにより E-03「再描画」の要件をベストエフォートで満たす。
 *   v2 では完全 SPA 方式に切り替え、このメソッドで完全なカード再描画を実装する予定。
 */
function _fetchAndRebuildModuleGrid() {{
  fetch(BP + '/api/modules')
    .then(function(r) {{
      if (!r.ok) return null;
      return r.json();
    }})
    .then(function(data) {{
      if (!data || !Array.isArray(data.modules)) return;
      var mods = data.modules;

      /* ステータスバーの Index 数を更新 */
      var sbIdx = document.getElementById('sb-index-val');
      if (sbIdx) sbIdx.textContent = mods.length;

      /* stat-card の Index Modules 数を更新 */
      var statIdx = document.getElementById('stat-index-count');
      if (statIdx) statIdx.textContent = mods.length;

      /* インストール済み数・private 数を集計してstat-cardを更新 */
      var deployedCount = 0;
      var privateCount  = 0;
      mods.forEach(function(m) {{
        if (m.installed)          deployedCount++;
        if (m.status === 'private') privateCount++;
      }});
      var statDep = document.getElementById('stat-deployed-count');
      if (statDep) statDep.textContent = deployedCount;
      var statPrv = document.getElementById('stat-private-count');
      if (statPrv) statPrv.textContent = privateCount;
    }})
    .catch(function(e) {{
      console.warn('[FX-210] GET /api/modules 失敗:', e);
    }});
}}

function closeDialog(id) {{
  document.getElementById(id).classList.remove('show');
  document.querySelectorAll('#' + id + ' input[type=checkbox]').forEach(c => c.checked = false);
  var exec = id === 'dlg-uninstall'  ? document.getElementById('dun-exec')
           : id === 'dlg-redeploy'    ? document.getElementById('drd-exec')
           : id === 'dlg-restricted' ? document.getElementById('dr-exec')
           : id === 'dlg-private'    ? document.getElementById('dp-exec')
           : id === 'dlg-upload'     ? document.getElementById('upload-exec')
           : null;
  if (exec) exec.disabled = true;
  if (id === 'dlg-uninstall') {{
    /* FX-206: Uninstall ダイアログのリセット */
    var uchk = document.getElementById('dun-chk');
    if (uchk) uchk.checked = false;
  }}
  if (id === 'dlg-redeploy') {{
    /* FX-205: Re-deploy ダイアログのリセット */
    var chk = document.getElementById('drd-chk');
    if (chk) chk.checked = false;
  }}
  if (id === 'dlg-private') dpReset();
  if (id === 'dlg-upload') resetUpload();
}}
function _buildInfoRows(mod) {{
  var fields = [['Module ID', mod.id||''],['File', mod.plugin_file||''],['Version', mod.version||''],
    ['Author', mod.author||''],['Status', mod.status||''],['Repository', mod.repository||'']];
  return fields.filter(f => f[1]).map(f =>
    '<div class="mod-info-row"><span class="mod-info-key">' + f[0] + '</span><span class="mod-info-val">' + f[1] + '</span></div>'
  ).join('');
}}
var _currentMod = {{}};
function showDialog(type, mod) {{
  _currentMod = mod;
  if (type === 'uninstall') {{
    /* FX-206: confirm-uninstall モーダルを開く (FD §7-3 H-01準拠)
       ⚠ doUninstall() を直接呼び出すことは禁止。必ずこの経路を通ること。 */
    document.getElementById('dun-info').innerHTML = _buildInfoRows(mod);
    document.getElementById('dun-subtitle').textContent =
      (mod.status || 'public') + ' — ' + (mod.name || mod.id || '') + ' の削除';
    document.getElementById('dun-chk').checked   = false;
    document.getElementById('dun-exec').disabled = true;
    document.getElementById('dlg-uninstall').classList.add('show');
  }} else if (type === 'redeploy') {{
    /* FX-205: confirm-redeploy モーダルを開く (FD §7-3 H-03準拠) */
    document.getElementById('drd-info').innerHTML = _buildInfoRows(mod);
    document.getElementById('drd-subtitle').textContent =
      (mod.status || 'public') + ' — ' + (mod.name || mod.id || '') + ' の上書きインストール';
    document.getElementById('drd-chk').checked   = false;
    document.getElementById('drd-exec').disabled = true;
    document.getElementById('dlg-redeploy').classList.add('show');
  }} else if (type === 'restricted') {{
    document.getElementById('dr-info').innerHTML = _buildInfoRows(mod);
    document.getElementById('dr-chk').checked   = false;
    document.getElementById('dr-exec').disabled = true;
    document.getElementById('dlg-restricted').classList.add('show');
  }} else if (type === 'private') {{
    document.getElementById('dp-info1').innerHTML = _buildInfoRows(mod);
    document.getElementById('dp-info2').innerHTML = _buildInfoRows(mod);
    document.getElementById('dp-confirmInput').placeholder = mod.id || '';
    dpReset();
    document.getElementById('dlg-private').classList.add('show');
  }} else {{
    showModInfo(mod);
  }}
}}
function dpReset() {{
  document.getElementById('dp-panel1').style.display = 'flex';
  document.getElementById('dp-panel2').style.display = 'none';
  document.getElementById('dp-next').style.display   = 'inline-flex';
  document.getElementById('dp-exec').style.display   = 'none';
  document.getElementById('dp-stepnum').textContent  = '1';
  document.getElementById('dp-chk1').checked  = false;
  document.getElementById('dp-chk2').checked  = false;
  document.getElementById('dp-confirmInput').value = '';
  document.getElementById('dp-next').disabled = true;
  document.getElementById('dp-exec').disabled = true;
  document.getElementById('dp-s1n').className = 'step-num active';
  document.getElementById('dp-s1l').className = 'step-label active';
  document.getElementById('dp-s2n').className = 'step-num pending';
  document.getElementById('dp-s2l').className = 'step-label pending';
}}
function dpGoStep2() {{
  document.getElementById('dp-panel1').style.display = 'none';
  document.getElementById('dp-panel2').style.display = 'flex';
  document.getElementById('dp-next').style.display   = 'none';
  document.getElementById('dp-exec').style.display   = 'inline-flex';
  document.getElementById('dp-stepnum').textContent  = '2';
  document.getElementById('dp-s1n').className = 'step-num done';
  document.getElementById('dp-s1l').className = 'step-label done';
  document.getElementById('dp-s2n').className = 'step-num active';
  document.getElementById('dp-s2l').className = 'step-label active';
}}
function updateDpStep2() {{
  var inp = document.getElementById('dp-confirmInput').value;
  var chk = document.getElementById('dp-chk2').checked;
  document.getElementById('dp-exec').disabled = !(inp === _currentMod.id && chk);
}}
function showModInfo(mod) {{
  document.getElementById('di-title').textContent = mod.name || mod.id || 'Module Info';
  document.getElementById('di-sub').textContent   = mod.id + ' · ' + (mod.plugin_file || '');
  document.getElementById('di-body').innerHTML    = '<div class="mod-info-table">' + _buildInfoRows(mod) + '</div>';
  document.getElementById('dlg-info').classList.add('show');
}}
document.querySelectorAll('.overlay').forEach(o => o.addEventListener('click', function(e) {{
  if (e.target === this) closeDialog(this.id);
}}));

/* hbx-upload モーダル (FX-207) ------------------------------------------------------------*/
/* hbx-upload モーダル (FX-207) */
var _uploadFile = null;
function openUploadModal() {{
  resetUpload();
  document.getElementById('dlg-upload').classList.add('show');
}}
function resetUpload() {{
  _uploadFile = null;
  document.getElementById('uploadFilename').textContent = '';
  document.getElementById('uploadProgress').style.display = 'none';
  document.getElementById('uploadMsg').textContent = '';
  document.getElementById('uploadMsg').style.color = 'var(--accent)';
  document.getElementById('upload-exec').disabled = true;
  var inp = document.getElementById('uploadFileInput');
  if (inp) inp.value = '';
}}

/**
 * ファイル選択時の共通UI更新ヘルパー（FX-207 修正3: 単一責務化）
 *
 * 前回実装との差分:
 *   旧: onUploadFileSelect() がファイル取得・_uploadFile セット・UI更新を兼務。
 *       ドラッグ＆ドロップは _uploadFile へ直接セット。
 *   新: _uploadFile のセットを呼び出し元の責務に分離し、
 *       この関数はUI更新のみを担う（ファイル保持と表示更新の分離）。
 * @param {{File}} file - 選択されたFileオブジェクト
 */
function _updateUploadUI(file) {{
  if (!file.name.endsWith('.hbx')) {{
    document.getElementById('uploadMsg').textContent = '⚠ .hbx ファイルのみアップロードできます。';
    document.getElementById('uploadMsg').style.color = 'var(--danger)';
    document.getElementById('upload-exec').disabled = true;
    _uploadFile = null;
    return;
  }}
  document.getElementById('uploadFilename').textContent =
    file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB)';
  document.getElementById('uploadMsg').textContent = '';
  document.getElementById('upload-exec').disabled = false;
}}

function onUploadFileSelect(input) {{
  var file = input.files[0];
  if (!file) return;
  _uploadFile = file;
  _updateUploadUI(file);
}}

/* ドラッグ＆ドロップ対応
   【修正3 前回実装との差分】
   - 旧: fakeInput = {{ files: [file] }} を作って onUploadFileSelect() に渡す間接方式。
   - 新: _uploadFile = file を直接セット → _updateUploadUI(file) でUI更新のみ。
     ファイル保持とUI更新を明確に分離し、保守性を向上。
*/
(function() {{
  var area = document.getElementById('uploadDropArea');
  if (!area) return;
  area.addEventListener('dragover', function(e) {{ e.preventDefault(); area.classList.add('dragover'); }});
  area.addEventListener('dragleave', function() {{ area.classList.remove('dragover'); }});
  area.addEventListener('drop', function(e) {{
    e.preventDefault();
    area.classList.remove('dragover');
    var file = e.dataTransfer.files[0];
    if (file) {{
      _uploadFile = file;
      _updateUploadUI(file);
    }}
  }});
}})();

/**
 * doUpload() — .hbx アップロード実行（FX-207 本実装）
 *
 * FD §4-5 操作フロー準拠:
 *   POST /api/module/upload → GET /api/modules → モーダル2秒後クローズ
 *
 * 【修正1: I-05 前回実装との差分】
 *   Upload 成功後に _fetchAndRebuildModuleGrid() を追加。
 *   旧実装では GET /api/modules 再取得が欠落しており統計カードが更新されなかった。
 *   FD §4-5 操作フロー表に「POST /api/module/upload → GET /api/modules」と明示あり。
 *   _fetchAndRebuildModuleGrid() は非同期（ノンブロッキング）のため
 *   モーダルクローズのタイマー（2000ms）と並行して実行される。
 *
 * 【修正2: D-05/D-06 前回実装との差分】
 *   旧実装: エラー時に常に execBtn.disabled = false（retryable 判定なし）。
 *   新実装: retryable フラグでボタン enabled/disabled を制御。
 *     retryable: true  → enabled に戻す（500 install_failed 等: 再試行可能）
 *     retryable: false → disabled のまま保持（400/409 等: 再試行しても同結果）
 *   retryable フィールド欠落時は HTTP ステータスで判定（FD §4-2 準拠）:
 *     4xx → false / 5xx → true
 *
 * 【修正4: タイムアウト統一 前回実装との差分】
 *   旧: setTimeout 1500ms（FD 仕様と不一致）
 *   新: setTimeout 2000ms（FD §4-5「モーダル2秒後クローズ」に統一）
 */
function doUpload() {{
  if (!_uploadFile) return;
  var execBtn  = document.getElementById('upload-exec');
  var progress = document.getElementById('uploadProgress');
  var msg      = document.getElementById('uploadMsg');
  execBtn.disabled = true;
  progress.style.display = 'block';
  msg.textContent = '';
  var fd = new FormData();
  fd.append('file', _uploadFile);
  fetch(BP + '/api/module/upload', {{ method: 'POST', body: fd }})
    .then(function(r) {{
      var status = r.status;
      return r.json().then(function(d) {{
        return {{ status: status, data: d }};
      }}).catch(function() {{
        return {{ status: status, data: {{}} }};
      }});
    }})
    .then(function(res) {{
      var s = res.status;
      var d = res.data;
      progress.style.display = 'none';
      if (s === 200 && d.id) {{
        msg.textContent = '\u2713 ' + d.id + ' \u3092\u30a4\u30f3\u30b9\u30c8\u30fc\u30eb\u3057\u307e\u3057\u305f\u3002\u518d\u8d77\u52d5\u5f8c\u306b\u6709\u52b9\u306b\u306a\u308a\u307e\u3059\u3002';
        msg.style.color = 'var(--accent)';
        execBtn.disabled = true;
        _fetchAndRebuildModuleGrid();
        setRebootRequired(true, d.id + ' \u3092\u30a2\u30c3\u30d7\u30ed\u30fc\u30c9\u3057\u307e\u3057\u305f');
        setTimeout(function() {{ closeDialog('dlg-upload'); }}, 2000);
      }} else {{
        var errMsg = d.error_message || d.error || '\u30a8\u30e9\u30fc\u304c\u767a\u751f\u3057\u307e\u3057\u305f\uff08HTTP ' + s + '\uff09';
        msg.textContent = '\u2717 ' + errMsg;
        msg.style.color = 'var(--danger)';
        var isRetryable;
        if (typeof d.retryable === 'boolean') {{
          isRetryable = d.retryable;
        }} else {{
          isRetryable = (s >= 500);
        }}
        execBtn.disabled = !isRetryable;
      }}
    }})
    .catch(function(e) {{
      progress.style.display = 'none';
      msg.textContent = '\u2717 \u30cd\u30c3\u30c8\u30ef\u30fc\u30af\u30a8\u30e9\u30fc: ' + e;
      msg.style.color = 'var(--danger)';
      execBtn.disabled = false;
    }});
}}
/*#-------------------------------------------------------------*/
var _origSettings = {{
  default_context: '{default_context}',
  index_url:       '{index_url}',
  notify_url:      '{notify_url}'
}};
function loadSettings() {{
  document.getElementById('cfg_default_context').value = _origSettings.default_context;
  document.getElementById('cfg_index_url').value       = _origSettings.index_url;
  document.getElementById('cfg_notify_url').value      = _origSettings.notify_url;
  document.getElementById('settingMsg').textContent    = '';
}}
function saveSettings() {{
  var data = {{
    default_context: document.getElementById('cfg_default_context').value.trim(),
    index_url:       document.getElementById('cfg_index_url').value.trim(),
    notify_url:      document.getElementById('cfg_notify_url').value.trim()
  }};
  fetch(BP + '/api/manager/save-settings', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(data)
  }})
  .then(r => r.json())
  .then(d => {{
    document.getElementById('settingMsg').textContent = d.ok ? '\u2713 \u4fdd\u5b58\u3057\u307e\u3057\u305f' : ('\u30a8\u30e9\u30fc: ' + (d.error || ''));
    document.getElementById('settingMsg').style.color = d.ok ? 'var(--accent)' : 'var(--danger)';
    if (d.ok) {{ _origSettings = data; }}
  }})
  .catch(e => {{
    document.getElementById('settingMsg').textContent = '\u30a8\u30e9\u30fc: ' + e;
    document.getElementById('settingMsg').style.color = 'var(--danger)';
  }});
}}

/* ===================================================================
   FX-209: RebootBanner 管理モジュール
   FD §1-4 AppState.rebootRequired 準拠

   【AppState.rebootRequired の定義】
     型    : boolean
     初期値: false（FD A-04準拠）
     true になる条件:
       - Install 成功 (doInstall 200 応答) → A-05準拠
       - Uninstall 成功 (doUninstall 200 応答) → A-05準拠
       - Upload 成功 (doUpload 200 応答) → A-05準拠
     false になる条件:
       - ユーザーが「あとで」ボタンをクリック → A-06準拠

   【setRebootRequired(bool, detail)】
     bool   : true = バナー表示, false = バナー非表示（あとで）
     detail : 任意の補足テキスト（バナーに小さく表示）

   【onRebootNow()】
     hsBox の再起動はサーバー側 API からは実行できないため、
     ユーザーに hsBox 管理画面へ誘導する案内を行う。
     再起動手順: システム管理 → 再起動 または SSH 経由で sudo reboot
     FD §1-4: バナーの「あとで」は rebootRequired を false にリセットする。
              「今すぐ再起動」も操作完了後に rebootRequired を false にリセットする。

   【前回実装との差分】
     - 前回: doInstall/doUninstall 成功後に alert() のみ。RebootBanner なし。
     - 今回: AppState.rebootRequired を JS 変数で保持し、
             成功後に setRebootRequired(true) を呼んで .reboot-banner.show を付与。
             location.reload() を削除（バナー表示後はページ遷移しない）。
             ページ再読み込みなしでカード状態を維持する設計に変更。
             ※ Module一覧は成功後も表示を維持し、再起動を促すバナーのみ表示。

   【注意】
     「今すぐ再起動」は Python サーバーから hsBox を再起動させることができないため、
     ユーザーに hsBox 管理ページへの遷移を案内するだけとする（UI仕様 §8 無反応禁止）。
     将来的に /api/system/reboot エンドポイントが追加された場合はここに実装を追加する。
   =================================================================== */
(function() {{
  /* AppState.rebootRequired: FD §1-4 初期値 false */
  var _rebootRequired = false;

  /**
   * AppState.rebootRequired を更新し、バナーの表示/非表示を制御する。
   *
   * @param {{{{boolean}}}} required - true: バナー表示 / false: バナー非表示
   * @param {{{{string}}}}  detail   - バナーに追記する補足テキスト（省略可）
   *
   * FD §1-4 A-04 〜 A-06 / I-06 準拠:
   *   true  → .reboot-banner.show を追加してバナーを表示
   *   false → .show を除去してバナーを非表示・AppState を false にリセット
   */
  window.setRebootRequired = function(required, detail) {{
    _rebootRequired = required;
    /* AppState.rebootRequired の同期 */
    AppState.rebootRequired = required;
    var banner = document.getElementById('reboot-banner');
    if (!banner) return;

    if (required) {{
      /* 補足テキストの更新（detail が指定された場合のみ） */
      var detailEl = document.getElementById('reboot-banner-detail');
      if (detailEl) {{
        detailEl.textContent = detail ? ('(' + detail + ')') : '';
      }}
      /* アニメーション再生のため一旦 show を除去してから再付与 */
      banner.classList.remove('show');
      /* eslint-disable-next-line no-void */
      void banner.offsetWidth;  /* reflow を強制してアニメーションをリセット */
      banner.classList.add('show');
    }} else {{
      /* setRebootRequired(false): バナーと Sidebar ボタンの両方を非表示（「今すぐ再起動」用） */
      banner.classList.remove('show');
    }}
    /* FX-209: Sidebar 再起動ボタンの表示/非表示を連動 */
    var sideReboot = document.getElementById('sidebar-reboot-btn');
    if (sideReboot) {{
      if (required) {{
        sideReboot.classList.add('active');
      }} else {{
        sideReboot.classList.remove('active');
      }}
    }}
  }};

  /**
   * hideBannerOnly() — 「後で再起動」ボタンのハンドラ。
   * バナーだけ非表示。Sidebar ボタンは再起動完了まで残す。
   * _rebootRequired = true のまま持続する。
   */
  window.hideBannerOnly = function() {{
    var banner = document.getElementById('reboot-banner');
    if (banner) banner.classList.remove('show');
    /* Sidebar ボタンは残す（_rebootRequired = true のまま） */
  }};

  /**
   * 「今すぐ再起動」ボタンのハンドラ。
   * hsBox の再起動は freeBox Loader からは直接実行できないため、
   * ユーザーに hsBox 管理ページへの遷移を案内する。
   *
   * FD UI仕様 §8「無反応禁止」準拠: 何らかのフィードバックを必ず返す。
   */
  window.onRebootNow = function() {{
    /* バナーを非表示にして rebootRequired をリセット（操作完了扱い） */
    setRebootRequired(false);
    /* hsBox 管理画面（トップ）に遷移する案内
       ユーザーはトップ→システム管理→再起動 の手順で再起動できる */
    var msg = 'hsBox を再起動するには、管理画面のシステム設定から再起動を実行してください。hsBox トップページに移動しますか？';
    if (window.confirm(msg)) {{
      window.location.href = 'http://' + window.location.hostname + '/';
    }}
  }};
}})();

/* ===================================================================
   G-05修正後の状態メモ:
     - 第1版 doUpload()（引数なし・_uploadFile 使用）が v1 正規実装として有効。
     - 第2版 doUpload(force) / _pendingUploadFile / 第2版 openUploadModal は
       v1 では未使用（BK-04: v2 で callApi() と組み合わせて再設計予定）。
     - 下の BK-04 封印ブロックに第2版の設計意図を保存（コード本体はコメントアウト）。
   =================================================================== */

/* ===================================================================
   BK-04 封印ブロック: 第2版 doUpload(force) / _pendingUploadFile (v2 再設計用)
   -------------------------------------------------------------------
   方針: v1 では第2版を使用しない。v2 で callApi() と組み合わせて再設計する。
   設計意図の保存のためコメントアウト形式で残す（削除しない）。

   // var _pendingUploadFile = null;
   //
   // // 第2版 doUpload(force): force=true で上書きアップロード（already_installed 回避）
   // function doUpload(force) {{
   //   // v2 再設計: callApi("POST", BP + "/api/module/upload", null, formData) を使う。
   //   // force=true のとき FormData に "force"="true" を付与し、
   //   // サーバー側で 409 already_installed をスキップして上書きインストールさせる。
   //   // 成功後は setRebootRequired(true, id) と _fetchAndRebuildModuleGrid() を呼ぶ。
   // }}
   =================================================================== */

/* ===================================================================
   FX-205: doInstall(id) / doRedeploy()
   =================================================================== */

/* ----- A-01〜A-12: AppState 完全定義 ----- */
window.AppState = {{
  indexFetched:   false,           /* A-01: boolean, 初期値 false */
  rebootRequired: false,           /* A-04: boolean, 初期値 false */
  status: {{                        /* A-07: 下記 4 フィールド */
    freebox_service: 'unknown',    /* A-08: 初期値 'unknown' */
    nas:             'unknown',    /* A-08 */
    apache2:         'unknown',    /* A-08 */
    scheduler_jobs:  0             /* A-09: 初期値 0 */
  }},
  moduleStates: {{}},               /* A-10/A-11: 各 id に対して state='idle', successTimer=null */
  modal: {{                         /* A-12: type=null */
    type:        null,
    targetId:    null,
    confirmText: ''
  }}
}};

/* A-10/A-11: moduleStates[id] を初期化または取得 */
function _getModState(id) {{
  if (!AppState.moduleStates[id]) {{
    AppState.moduleStates[id] = {{ state: 'idle', successTimer: null, retryable: false, error: null }};
  }}
  return AppState.moduleStates[id];
}}

/**
 * callApi(method, url, body, formData)
 * FD §4-2 CallResult 準拠の統一 API 呼び出し関数
 *
 * @returns {{Promise<{{ok, status, data, error, error_message, retryable}}>}}
 */
function callApi(method, url, body, formData) {{
  var opts = {{ method: method }};
  if (formData) {{
    opts.body = formData;
  }} else if (body !== null && body !== undefined) {{
    opts.headers = {{ 'Content-Type': 'application/json' }};
    opts.body    = JSON.stringify(body);
  }}
  return fetch(url, opts)
    .then(function(r) {{
      var status = r.status;
      return r.json().then(function(d) {{
        return {{ status: status, data: d }};
      }}).catch(function() {{
        return {{ status: status, data: null }};
      }});
    }})
    .then(function(res) {{
      var s = res.status, d = res.data || {{}};
      var ok = (s >= 200 && s < 300);
      var retryable = (typeof d.retryable === 'boolean') ? d.retryable : (s === 0 || s >= 500);
      return {{
        ok:            ok,
        status:        s,
        data:          d,
        error:         ok ? null : (d.error || 'unknown_error'),
        error_message: ok ? null : (d.error_message || d.error || 'エラー (HTTP ' + s + ')'),
        retryable:     retryable
      }};
    }})
    .catch(function(e) {{
      return {{ ok: false, status: 0, data: null,
               error: 'network_error', error_message: 'ネットワークエラー: ' + e,
               retryable: true }};
    }});
}}

/**
 * _setState(id, state, errorMsg, retryable)
 * FD §1-5 状態遷移表に従って moduleStates[id] を更新し UI を再描画する。
 * state: 'idle' | 'loading' | 'success' | 'error'
 */
function _setState(id, state, errorMsg, retryable) {{
  var ms = _getModState(id);
  /* loading 遷移時に successTimer をキャンセル (FD B-07/B-08) */
  if (state === 'loading' && ms.successTimer !== null) {{
    clearTimeout(ms.successTimer);
    ms.successTimer = null;
  }}
  ms.state    = state;
  ms.error    = errorMsg || null;
  ms.retryable = !!retryable;
  /* success 遷移時: 2秒後に idle へ自動遷移 (FD B-04/B-05/B-06) */
  if (state === 'success') {{
    ms.successTimer = setTimeout(function() {{
      ms.state = 'idle'; ms.successTimer = null; ms.error = null;
      _renderCardState(id);
    }}, 2000);
  }}
  _renderCardState(id);
}}

/**
 * _renderCardState(id)
 * mod-state-chip エリアとボタンの enabled/disabled を state に展開する。
 * FD §1-5 状態定義:
 *   idle    : ボタン有効 / chip 空白
 *   loading : ボタン disabled / "⟳ 処理中..."
 *   success : ボタン有効 / "✓ 完了"  (2秒後 idle)
 *   error   : ボタン有効 / "✕ <msg>" + retryable時は再試行ボタン
 */
function _renderCardState(id) {{
  var ms   = _getModState(id);
  var card = document.querySelector('.mod-card[data-id="' + id + '"]');
  if (!card) return;
  var btns     = card.querySelectorAll('button');
  var chipArea = card.querySelector('.mod-state-chip');
  if (!chipArea) return;
  switch (ms.state) {{
    case 'loading':
      btns.forEach(function(b) {{ b.disabled = true; }});
      chipArea.innerHTML = '<span style="font-family:var(--mono);color:var(--text-mid)">⟳処理中...</span>';
      break;
    case 'success':
      btns.forEach(function(b) {{ b.disabled = false; }});
      chipArea.innerHTML = '<span style="font-family:var(--mono);color:var(--accent)">✓完了</span>';
      break;
    case 'error':
      btns.forEach(function(b) {{ b.disabled = false; }});
      var retryHtml = '';
      if (ms.retryable) {{
        retryHtml = ' <button class="btn btn-ghost btn-sm" style="font-size:10px;padding:2px 8px"' +
          ' onclick="_retryLastOp(' + JSON.stringify(id) + ')">再試行</button>';
      }}
      chipArea.innerHTML =
        '<span style="font-family:var(--mono);color:var(--danger)">✕ ' +
        (ms.error || 'エラー') + '</span>' + retryHtml;
      break;
    case 'idle':
    default:
      btns.forEach(function(b) {{ b.disabled = false; }});
      chipArea.innerHTML = '';
      break;
  }}
}}

/* 再試行履歴辞書 */
var _lastOp = {{}};
function _retryLastOp(id) {{
  var op = _lastOp[id];
  if (!op) return;
  if (op.type === 'install')   doInstall(id);
  if (op.type === 'uninstall') doUninstall(id);
}}

function doInstall(id) {{
  _lastOp[id] = {{ type: 'install' }};
  _setState(id, 'loading', null, false);
  callApi('POST', BP + '/api/module/install', {{ id: id }}, null)
    .then(function(result) {{
      if (result.ok) {{
        /* FX-209: Install 成功 → RebootBanner 表示（FD §1-4 A-05 / I-06準拠） */
        setRebootRequired(true, (result.data.id || id) + ' をインストールしました');
        /* FD §4-5 I-01/I-02: Install/Re-deploy 成功後に GET /api/modules で一覧を再取得・再描画 */
        _fetchAndRebuildModuleGrid();
        _setState(id, 'success', null, false);
      }} else {{
        _setState(id, 'error', result.error_message, result.retryable);
      }}
    }});
}}

/* doRedeploy() : confirm-redeploy モーダルの [上書きインストール] ボタンから呼ばれる。
   _currentMod.id を使って doInstall() に委譲する。
   FD §7-3: Re-deploy は全status共通で POST /api/module/install を使う。 */
function doRedeploy() {{
  closeDialog('dlg-redeploy');
  /* BK-04: Upload文脈の Re-deploy（force=true での再アップロード）は v2 で再設計予定。
     v1 では _pendingUploadFile が未定義のため Upload文脈分岐をコメントアウトで無効化し、
     Index経由の Re-deploy のみを行う（Upload文脈 Re-deploy は運用: Remove → 再 Upload）。
     v2 復活時は下の分岐を有効化し、BK-04 封印ブロックの doUpload(force) を実装する。 */
  // if (typeof _pendingUploadFile !== 'undefined' && _pendingUploadFile) {{
  //   doUpload(true);  /* force=true で上書き (v2 再設計予定) */
  // }} else {{
  doInstall(_currentMod.id);
  // }}
}}

/* ===================================================================
   FX-206: doUninstall(id)
   FD §4-5 Uninstall フロー準拠
   =================================================================
   ⚠ この関数は confirm-uninstall モーダルの [削除する] ボタンからのみ呼び出される。
     直接呼び出し禁止（FD §7-3 H-01 / §4-5 I-04 準拠）。

   【FX-209 対応変更点（前回実装との差分）】
   - 成功後の alert() + location.reload() を廃止
   - 成功後に setRebootRequired(true, id) を呼んで RebootBanner を表示
   - location.reload() を削除（FD §4-5 I-04: installed の値は GET /api/modules で確認）
   - FD §4-5 I-06: Uninstall 成功後に AppState.rebootRequired = true
*/
function doUninstall(id) {{
  _lastOp[id] = {{ type: 'uninstall' }};
  _setState(id, 'loading', null, false);
  callApi('POST', BP + '/api/module/uninstall', {{ id: id }}, null)
    .then(function(result) {{
      if (result.ok) {{
        /* FX-209: Uninstall 成功 → RebootBanner 表示（FD §1-4 A-05 / I-06準拠）
           FD §4-5 I-04: installed の値をクライアント側で直接変更しない。*/
        setRebootRequired(true, (result.data.id || id) + ' をアンインストールしました');
        /* FD §4-5 I-03: Uninstall 成功後に GET /api/modules で一覧を再取得・再描画 */
        _fetchAndRebuildModuleGrid();
        _setState(id, 'success', null, false);
      }} else {{
        _setState(id, 'error', result.error_message, result.retryable);
      }}
    }});
}}

/* ===================================================================
   FX-208: StatusBar 30秒ポーリング
   FD §4-4 / §1-4 AppState.status 準拠

   【実装仕様】
   - 初期値: FD §1-4 AppState.status 初期値に準拠
       freebox_service: "unknown" → 「取得中」
       nas:            "unknown" → 「取得中」
       apache2:        "unknown" → 「取得中」
       scheduler_jobs: 0        → 「取得中」
   - 開始タイミング: DOMContentLoaded 後に即時1回実行（FD F-01準拠）
   - ポーリング間隔: 30,000ms（FD F-02準拠）
   - タイムアウト:   5,000ms（AbortController + setTimeout）
   - 成功時: 全フィールドで AppState.status を更新し DOM に反映（FD F-03準拠）
   - 失敗時: DOM を変更しない（前回値を維持）（FD F-04準拠）
   - "unknown" 表示: 「不明」と表示（FD F-05準拠）

   【前回実装との差分】
   - StatusBar が完全静的 HTML だったものを、JS によるポーリング更新に変更。
   - sb-freebox / sb-nas / sb-apache の各要素を新規追加（前回は sb-sched / sb-index のみ id あり）。
   - ドット色（.ok / .err / .warn）を状態に応じて動的切替。

   【AbortController タイムアウト実装について】
   - fetch() の signal オプションに AbortController.signal を渡す。
   - setTimeout(5000) で controller.abort() を呼び出してタイムアウトを実現。
   - AbortError は "failure" として扱い、前回値を維持する（FD F-04準拠）。
   =================================================================== */
(function() {{
  /* AppState.status の内部保持（前回値維持のため）
     FD §1-4: 初期値はすべて "unknown" / scheduler_jobs は 0 */
  var _status = {{
    freebox_service: 'unknown',
    nas:             'unknown',
    apache2:         'unknown',
    scheduler_jobs:  0
  }};

  /**
   * StatusBar DOM を _status の値で更新する。
   * FD F-05準拠: "unknown" は「不明」と表示する。
   * ドット色:
   *   running / connected → .ok（緑）
   *   stopped / disconnected → .err（赤）
   *   unknown → .warn（グレー）
   */
  function updateStatusBar() {{
    /* freebox_service: running | stopped | unknown */
    var fbVal  = _status.freebox_service;
    var fbDot  = fbVal === 'running'  ? 'ok'  : fbVal === 'stopped' ? 'err' : 'warn';
    var fbText = fbVal === 'running'  ? 'running'
               : fbVal === 'stopped' ? 'stopped'
               : '\u4e0d\u660e';   /* 不明 */
    _setStatusItem('sb-freebox', 'freebox', fbDot, fbText);

    /* nas: connected | disconnected | unknown */
    var nasVal  = _status.nas;
    var nasDot  = nasVal === 'connected'    ? 'ok'  : nasVal === 'disconnected' ? 'err' : 'warn';
    var nasText = nasVal === 'connected'    ? 'connected'
                : nasVal === 'disconnected' ? 'disconnected'
                : '\u4e0d\u660e';   /* 不明 */
    _setStatusItem('sb-nas', 'NAS', nasDot, nasText);

    /* apache2: running | stopped | unknown */
    var apVal  = _status.apache2;
    var apDot  = apVal === 'running'  ? 'ok'  : apVal === 'stopped' ? 'err' : 'warn';
    var apText = apVal === 'running'  ? 'running'
               : apVal === 'stopped' ? 'stopped'
               : '\u4e0d\u660e';   /* 不明 */
    _setStatusItem('sb-apache', 'apache2', apDot, apText);

    /* scheduler_jobs: 整数 */
    var sjVal  = _status.scheduler_jobs;
    _setStatusItem('sb-sched', 'Scheduler', 'ok', sjVal + ' job(s)');
  }}

  /**
   * StatusBar の単一アイテムを更新するヘルパー。
   *
   * @param {{{{string}}}} itemId   - statusbar-item 要素の id（例: "sb-nas"）
   * @param {{{{string}}}} label    - 表示ラベル（例: "NAS"）
   * @param {{{{string}}}} dotCls   - ドットの CSS クラス: "ok" | "err" | "warn"
   * @param {{{{string}}}} valText  - 値テキスト（例: "connected"）
   */
  function _setStatusItem(itemId, label, dotCls, valText) {{
    var item = document.getElementById(itemId);
    if (!item) return;
    /* innerHTML を直接組み立てて1回の DOM 更新にまとめる */
    item.innerHTML =
      '<span class="' + dotCls + '">\u25cf</span> ' +  /* ● */
      label + ': <span id="' + itemId + '-val">' + valText + '</span>';
  }}

  /**
   * GET /api/status を呼び出し、成功時に _status を更新して StatusBar を再描画する。
   *
   * FD §4-4 ポーリング仕様準拠:
   *   - タイムアウト: 5,000ms（AbortController）
   *   - 失敗時: _status を変更しない（前回値を維持）
   *   - 成功時: scheduler_jobs を含む全フィールドを更新
   */
  function pollStatus() {{
    var controller = new AbortController();
    var timeoutId  = setTimeout(function() {{
      /* 5秒でタイムアウト → abort → fetchがAbortErrorで失敗 → 前回値維持 */
      controller.abort();
    }}, 5000);

    fetch(BP + '/api/status', {{ signal: controller.signal }})
      .then(function(r) {{
        clearTimeout(timeoutId);
        if (!r.ok) {{
          /* 4xx / 5xx: 前回値を維持する（FD F-04準拠） */
          return null;
        }}
        return r.json();
      }})
      .then(function(d) {{
        if (!d) return;  /* エラーレスポンスまたは abort */
        /* 成功: 全フィールドを上書き更新（FD F-03準拠）
           scheduler_jobs を含む4フィールドすべてを更新する。
           フィールドが欠落している場合は前回値を維持する（防御的マージ）。 */
        if (d.freebox_service !== undefined) {{ _status.freebox_service = d.freebox_service; AppState.status.freebox_service = d.freebox_service; }}
        if (d.nas             !== undefined) {{ _status.nas             = d.nas;             AppState.status.nas             = d.nas; }}
        if (d.apache2         !== undefined) {{ _status.apache2         = d.apache2;         AppState.status.apache2         = d.apache2; }}
        if (typeof d.scheduler_jobs === 'number') {{ _status.scheduler_jobs = d.scheduler_jobs; AppState.status.scheduler_jobs = d.scheduler_jobs; }}
        updateStatusBar();
      }})
      .catch(function(e) {{
        /* ネットワークエラー / AbortError: 前回値を維持する（FD F-04準拠） */
        clearTimeout(timeoutId);
        /* AbortError（タイムアウト）は想定内なのでコンソールには出さない。
           それ以外のエラーはデバッグ用に出力する。 */
        if (e && e.name !== 'AbortError') {{
          console.warn('[FX-208] pollStatus エラー:', e);
        }}
      }});
  }}

  /* ===== 初期化 =====
     FD F-01準拠: UI 初期化完了後に即時1回実行。
     DOMContentLoaded の後に pollStatus() を呼び出し、
     その後 setInterval(30000) で30秒ごとに繰り返す。

     FD F-02準拠: ポーリング間隔 30,000ms。
     FD §4-4:     ページアンロードまで停止しない（clearInterval なし）。
  */
  document.addEventListener('DOMContentLoaded', function() {{
    /* 即時1回実行（FD F-01） */
    pollStatus();
    /* [設計方針] StatusBar ポーリングについて
       /api/status は freeBox 自身の死活（Apache/NAS/Scheduler）を返す。
       ユーザーが画面を操作したときに最新状態が分かれば十分であり、
       バックグラウンドでの頻繁なポーリングは不要。

       初版リリース: 30分（1800000ms）--- 実質ほぼ無効に近い間隔
       ポーリング確認テスト時のみ: 手動で 30000ms に変更してテスト後に戻す
       将来強化: IoTイベント集約実装時に WebSocket/SSE へ移行する（ポーリング廃止）
       参照: FD見直しメモ Rev.8 §4-4 */
    setInterval(pollStatus, 1800000);
  }});

}})();  /* IIFE: グローバルスコープ汚染を防ぐ */
</script>
</body>
</html>"""


def _build_manager_html(index_data, plugin_names, scheduler_jobs, cfg, last_fetched) -> bytes:
    """Module Manager UI の HTML を生成し bytes で返す。"""
    modules   = index_data.get("modules", [])
    logger.info("[DEBUG] index_data=%s", json.dumps(index_data))  # ← 追加
    logger.info("[DEBUG] modules count=%d", len(modules))          # ← 追加
    index_ids = {m["id"] for m in modules}

    stats         = _build_stats_section(modules, plugin_names, scheduler_jobs, last_fetched)
    index_cards   = _build_index_cards(modules, plugin_names)
    private_cards = _build_private_cards(plugin_names, index_ids)
    sched_rows    = _build_scheduler_rows(scheduler_jobs)
    html          = _render_html_template(
        stats, index_cards, private_cards, sched_rows, cfg, last_fetched
    )
    # FX-211: [server] base_path でJS内APIパスを補正
    # Apache2 Proxy 経由時に fetch('/api/...') が正しいパスに届かない問題を解消する。
    # base_path=/freebox → fetch('/api/...') を fetch('/freebox/api/...') に書き換え
    base_path = cfg.get("server", "base_path", fallback="").rstrip("/") if cfg else ""
    if base_path:
        html = html.replace("fetch('/api/", f"fetch('{base_path}/api/")
    return html.encode("utf-8")


# ===========================================================================
# ManagerPlugin（予約名 "manager" / 組み込みハンドラ）
# ===========================================================================

class ManagerPlugin:
    """
    /manager/ を処理する組み込みハンドラ。
    FD §4-4 準拠: PluginManager には登録せず FreeBoxHandler から直接呼び出す。
    """

    def handle_manager(self, handler, cfg, plugin_manager, scheduler, index_cache) -> None:
        path = handler.path.split("?")[0]

        if path == "/manager/api/save-settings":
            try:
                length = int(handler.headers.get("Content-Length", 0))
                raw    = handler.rfile.read(length) if length > 0 else b"{}"
                params = json.loads(raw.decode("utf-8"))
                dc = str(params.get("default_context", "manager")).strip() or "manager"
                iu = str(params.get("index_url", DEFAULT_INDEX_URL)).strip()
                nu = str(params.get("notify_url", "")).strip()
                cfg.set("loader", "default_context", dc)
                cfg.set("loader", "index_url", iu)
                cfg.set("status", "notify_url", nu)
                save_config(cfg)
                body = json.dumps({"ok": True}).encode()
                _send_response(handler, 200, body, "application/json; charset=utf-8")
            except Exception as e:
                logger.error("[ManagerPlugin] 設定保存エラー: %s", e)
                body = json.dumps({"ok": False, "error": str(e)}).encode()
                _send_response(handler, 500, body, "application/json; charset=utf-8")
            return

        index_data   = index_cache.get() if index_cache else _empty_index()
        # plugin_names: PluginManager起動時リストではなくplugins/をリアルタイムスキャン
        # （Deploy後の即時反映: FX-101と同じロジック）
        try:
            plugin_files = [
                f[:-3] for f in os.listdir(PLUGINS_DIR)
                if f.endswith(".py") and not f.startswith("_")
            ]
        except Exception:
            plugin_files = plugin_manager.get_plugin_names() if plugin_manager else []
        plugin_names = plugin_files
        sched_jobs   = scheduler.get_jobs() if scheduler else []
        last_fetched = index_cache.get_last_fetched() if index_cache else 0.0

        body = _build_manager_html(index_data, plugin_names, sched_jobs, cfg, last_fetched)
        _send_response(handler, 200, body, "text/html; charset=utf-8")


# ===========================================================================
# FreeBoxHandler（HTTPリクエストハンドラ）
# ===========================================================================

class FreeBoxHandler(BaseHTTPRequestHandler):
    """
    HTTPServer に渡すリクエストハンドラ。
    クラス変数として依存オブジェクトを保持し、main() で注入する。
    """
    plugin_manager: "PluginManager | None"             = None
    scheduler:      "Scheduler | None"                 = None
    cfg:            "configparser.ConfigParser | None" = None
    index_cache:    "IndexCache | None"                = None
    manager_plugin: "ManagerPlugin | None"             = None

    def log_message(self, format, *args):
        logger.info("HTTP %s - %s", self.address_string(), format % args)

    # ------------------------------------------------------------------
    # FX-002/FX-003: _dispatch() ─ セキュリティチェック + ルーティング
    # ------------------------------------------------------------------

    def _dispatch(self) -> None:
        """
        リクエストルーティング。FD §4-4 準拠の処理順序:
          ① ".." 検査（パストラバーサル防御）
          ② Plugin 名バリデーション（^[a-z0-9]+$）
          ③ ルートアクセス → default_context へ 302 リダイレクト
          ④a /api/* → _handle_api() に委譲
          ④b /manager/ → ManagerPlugin に委譲
          ⑤ Plugin 検索 → Plugin.handle() に委譲
        """
        path = self.path.split("?")[0]

        # ① パストラバーサル防御 (FD §4-4-1)
        # normpath後ではなくraw pathで検査（normpath が .. を展開して防御が無効になる問題を避ける）
        if ".." in path.split("/") or "\\" in path:
            logger.warning("[FX-002] パストラバーサル検出・拒否: %r", path)
            _send_response(self, 400, b"400 Bad Request: path traversal", "text/plain")
            return

        # ② Plugin 名バリデーション
        plugin_name = _parse_plugin_name(path)
        if plugin_name is None:
            _send_response(self, 400, b"400 Bad Request: invalid plugin name", "text/plain")
            return

        # ③ ルートアクセス → default_context へリダイレクト
        if plugin_name == "":
            cfg         = self.__class__.cfg
            default_ctx = (
                cfg.get("loader", "default_context", fallback=DEFAULT_CONTEXT)
                if cfg else DEFAULT_CONTEXT
            ) or DEFAULT_CONTEXT
            if not VALID_PLUGIN_NAME.match(default_ctx):
                default_ctx = DEFAULT_CONTEXT
            self.send_response(302)
            self.send_header("Location", f"/{default_ctx}/")
            self.end_headers()
            return

        # BK-08: _serve_local_release() と /releases/ ルートを削除済み（G-16）

        # ④a /api/* → Loader 内部 API
        if plugin_name == "api":
            resp = self._handle_api(self.command, path)
            _send_response(self, resp.status, resp.body, resp.content_type)
            return

        # ④b /manager/ → ManagerPlugin
        if plugin_name == "manager":
            mp    = self.__class__.manager_plugin
            cfg   = self.__class__.cfg or configparser.ConfigParser()
            ic    = self.__class__.index_cache
            pm    = self.__class__.plugin_manager
            sched = self.__class__.scheduler
            if mp:
                mp.handle_manager(self, cfg, pm, sched, ic)
            else:
                _send_response(self, 503, b"ManagerPlugin not initialized", "text/plain")
            return

        # ④c /test-assets/ → FTテスト用静的ファイル配信
        if plugin_name == "test-assets":
            file_path = path.lstrip("/")  # "test-assets/releases/download/1.0.0/testmodule01.hbx"
            abs_path  = os.path.join(BASE_DIR, file_path)
            # パストラバーサル二重チェック
            if not os.path.abspath(abs_path).startswith(os.path.abspath(BASE_DIR)):
                _send_response(self, 400, b"400 Bad Request", "text/plain")
                return
            if os.path.isfile(abs_path):
                with open(abs_path, "rb") as f:
                    data = f.read()
                _send_response(self, 200, data, "application/octet-stream")
            else:
                logger.warning("[test-assets] ファイル不存在: %s", abs_path)
                _send_response(self, 404, b"404 Not Found", "text/plain")
            return

        # ⑤ Plugin 検索・実行
        pm     = self.__class__.plugin_manager
        plugin = pm.find_plugin(path) if pm else None
        if plugin is None:
            body = f"404 Not Found: {path}".encode("utf-8")
            _send_response(self, 404, body, "text/plain")
            return

        try:
            req  = RequestWrapper(self)
            resp = plugin.handle(req)
            if isinstance(resp, Response):
                _send_response(self, resp.status, resp.body, resp.content_type)
        except Exception as e:
            logger.exception("[FX-003] Plugin handle 例外 [%s]: %s", plugin_name, e)
            _send_response(self, 500, b"500 Internal Server Error", "text/plain")

    # BK-08: _serve_local_release() 削除済み（G-16。FTモック用ローカル配信は削除）

    # ------------------------------------------------------------------
    # FX-107: GET /favicon.ico
    # ------------------------------------------------------------------
        """
        /releases/download/{version}/{filename} を
        server/releases/ 以下の実ファイルとして返す。

        FTモック用。repository = "http://127.0.0.1:9009" のときのみ使われる。
        本番GitHub配信に切り替えたらこのメソッドは削除すること。
        """
        # セキュリティ: ".."含むパスは既に _dispatch() で拒否済み
        # /releases/download/1.0.0/testmodule01.hbx のみ許可
        rel = posixpath.normpath(path.lstrip("/"))  # releases/download/1.0.0/testmodule01.hbx
        local_path = os.path.join(BASE_DIR, rel.replace("/", os.sep))

        if not local_path.startswith(os.path.join(BASE_DIR, "releases")):
            logger.warning("[releases] 不正パス拒否: %s", local_path)
            return Response(403, b"403 Forbidden", "text/plain")

        if not os.path.isfile(local_path):
            logger.warning("[releases] ファイルなし: %s", local_path)
            body = json.dumps({
                "error": "not_found",
                "error_message": f"HBXファイルが見つかりません: {os.path.basename(local_path)}"
            }, ensure_ascii=False).encode("utf-8")
            return Response(404, body, "application/json; charset=utf-8")

        try:
            with open(local_path, "rb") as f:
                data = f.read()
            logger.info("[releases] 配信: %s (%d bytes)", local_path, len(data))
            return Response(200, data, "application/octet-stream")
        except Exception as e:
            logger.error("[releases] 読み取りエラー: %s", e)
            return Response(500, b"500 Internal Server Error", "text/plain")

    # ------------------------------------------------------------------
    # Loader 内部 API ルーティング (FX-101〜106)
    # ------------------------------------------------------------------

    _CONTENT_JSON = "application/json; charset=utf-8"

    def _handle_api(self, method: str, path: str) -> Response:
        """
        /api/* のルーティング。全 API エンドポイントの入口。

        実装済み:
          GET  /api/modules             (FX-101)
          POST /api/module/install      (FX-102)
          POST /api/module/uninstall    (FX-103)
          POST /api/module/upload       (FX-104)
          POST /api/index/refresh       (FX-105)
          GET  /api/status              (FX-106)  ← FX-208 StatusBar ポーリング対象
        """
        if method == "GET"  and path == "/api/modules":
            return self._api_get_modules()

        if method == "POST" and path == "/api/module/install":
            return self._api_post_module_install()

        if method == "POST" and path == "/api/module/uninstall":
            return self._api_post_module_uninstall()

        # FX-104: POST /api/module/upload（本実装）
        if method == "POST" and path == "/api/module/upload":
            return self._api_post_module_upload()

        if method == "POST" and path == "/api/index/refresh":
            return self._api_post_index_refresh()

        if method == "GET" and path == "/api/status":
            return self._api_get_status()

        # Manager 設定保存 API
        if method == "POST" and path == "/api/manager/save-settings":
            cfg = self.__class__.cfg or configparser.ConfigParser()
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw    = self.rfile.read(length) if length > 0 else b"{}"
                params = json.loads(raw.decode("utf-8"))
                dc = str(params.get("default_context", "manager")).strip() or "manager"
                iu = str(params.get("index_url", DEFAULT_INDEX_URL)).strip()
                nu = str(params.get("notify_url", "")).strip()
                cfg.set("loader", "default_context", dc)
                cfg.set("loader", "index_url", iu)
                cfg.set("status", "notify_url", nu)
                save_config(cfg)
                body = json.dumps({"ok": True}).encode("utf-8")
                return Response(200, body, self._CONTENT_JSON)
            except Exception as e:
                logger.error("[save-settings] 設定保存エラー: %s", e)
                body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                return Response(500, body, self._CONTENT_JSON)

        # 未定義エンドポイント
        body = json.dumps({
            "error":         "not_found",
            "error_message": f"エンドポイント {method} {path} は存在しません。",
        }, ensure_ascii=False).encode("utf-8")
        return Response(404, body, self._CONTENT_JSON)

    # ------------------------------------------------------------------
    # FX-101: GET /api/modules
    # ------------------------------------------------------------------

    def _api_get_modules(self) -> Response:
        """
        GET /api/modules の本実装。FD §4-1-1 準拠。

        1. plugins/ スキャン → インストール済み ID セット
        2. IndexCache からインデックス取得
        3. マージ: installed フラグ付与 / private Module 追加
        4. {"modules": [...]} で返す（空のとき []・null は返さない）
        """
        try:
            plugin_files = [
                f for f in os.listdir(PLUGINS_DIR)
                if f.endswith(".py") and not f.startswith("_")
            ]
        except Exception as e:
            logger.error("[FX-101] plugins/ 読み取り失敗: %s", e)
            body = json.dumps({
                "error":         "plugins_dir_unreadable",
                "error_message": f"plugins ディレクトリの読み取りに失敗しました: {{e}}",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(500, body, self._CONTENT_JSON)

        installed_ids: set = {f[:-3] for f in plugin_files}

        ic         = self.__class__.index_cache
        index_data = ic.get() if ic else _empty_index()
        index_mods = index_data.get("modules", [])

        if not index_mods and ic is not None and not os.path.exists(ic._cache_path):
            logger.warning("[FX-101] インデックス取得失敗かつキャッシュなし")
            body = json.dumps({
                "error":         "index_unavailable",
                "error_message": "インデックスの取得に失敗し、キャッシュも存在しません。",
                "retryable":     True,
            }, ensure_ascii=False).encode("utf-8")
            return Response(500, body, self._CONTENT_JSON)

        result:    list = []
        index_ids: set  = set()

        for mod in index_mods:
            mod_id      = mod.get("id", "")
            plugin_file = mod.get("plugin_file", f"{mod_id}.py")
            stem        = plugin_file[:-3] if plugin_file.endswith(".py") else plugin_file
            is_installed = stem in installed_ids or mod_id in installed_ids
            index_ids.add(mod_id)

            entry: dict = {
                "id":          mod_id,
                "name":        mod.get("name", mod_id),
                "description": mod.get("description", ""),
                "status":      mod.get("status", "public"),
                "version":     mod.get("version", ""),
                "author":      mod.get("author", ""),
                "repository":  mod.get("repository", ""),
                "plugin_file": plugin_file,
                "installed":   is_installed,
            }
            if "requires_ffmpeg" in mod:
                entry["requires_ffmpeg"] = bool(mod["requires_ffmpeg"])
            if "hsbox_min_version" in mod:
                entry["hsbox_min_version"] = str(mod["hsbox_min_version"])
            result.append(entry)

        for pid in sorted(installed_ids):
            if pid in index_ids:
                continue
            if not VALID_PLUGIN_NAME.match(pid):
                continue
            result.append({
                "id":          pid,
                "name":        pid,
                "description": "インデックス未登録のローカル配置 Module。",
                "status":      "private",
                "version":     "",
                "author":      "",
                "repository":  "",
                "plugin_file": f"{pid}.py",
                "installed":   True,
            })

        body = json.dumps({"modules": result}, ensure_ascii=False).encode("utf-8")
        return Response(200, body, self._CONTENT_JSON)

    # ------------------------------------------------------------------
    # FX-102: POST /api/module/install
    # ------------------------------------------------------------------

    def _api_post_module_install(self) -> Response:
        """
        POST /api/module/install の本実装。FD §4-1-2 準拠。

        新規インストールと Re-deploy の両方をこのAPIで処理する（冪等）。
        処理フロー:
          1. JSON 解析 → id バリデーション
          2. インデックス JSON から対象 Module を検索
          3. .hbx ダウンロード URL を構築
          4. GitHub から .hbx ダウンロード（timeout: 30秒）
          5. ZIP 展開・バリデーション (version.txt / plugin_file)
          6. plugins/ へコピー（Re-deploy 時は上書き）
        """
        import shutil
        import tempfile
        import urllib.parse
        import zipfile

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw    = self.rfile.read(length) if length > 0 else b"{}"
            params = json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.warning("[FX-102] JSON 解析失敗: %s", e)
            body = json.dumps({
                "error":         "invalid_id",
                "error_message": "リクエストボディの JSON 解析に失敗しました。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        mod_id = str(params.get("id", "")).strip()
        if not mod_id or not VALID_PLUGIN_NAME.match(mod_id):
            body = json.dumps({
                "error":         "invalid_id",
                "error_message": "id フィールドが未送信または形式不正です（^[a-z0-9]+$ のみ許可）。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        ic         = self.__class__.index_cache
        index_data = ic.get() if ic else _empty_index()
        target     = next(
            (m for m in index_data.get("modules", []) if m.get("id") == mod_id), None
        )
        if target is None:
            logger.warning("[FX-102] module not found in index: %s", mod_id)
            body = json.dumps({
                "error":         "module_not_found",
                "error_message": f"指定した ID '{mod_id}' はインデックス JSON に存在しません。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(404, body, self._CONTENT_JSON)

        plugin_file = target.get("plugin_file", f"{mod_id}.py")
        # 正式設計: repository フィールドに各モジュールの .hbx 配置先リポジトリベース URL を指定する。
        # サードパーティは自前のリポジトリ URL を repository に記載することで、
        # 公式モジュールは公式 Release から、自前モジュールは自前 Release からダウンロードできる。
        # release_tag: 必ず指定すること（version とは分離されたリリースタグ名）。
        # version: UI 表示用の意味論的バージョン番号（リリースタグと一致しなくてよい）。
        repository  = target.get("repository", "").rstrip("/")
        release_tag = target.get("release_tag") or target.get("version", "latest")

        if not repository:
            logger.error("[FX-102] repository フィールドが未設定: id=%s", mod_id)
            body = json.dumps({
                "error":         "invalid_module_config",
                "error_message": "index.json の repository フィールドが未設定です。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        if "/" in plugin_file or "\\" in plugin_file or ".." in plugin_file:
            logger.error("[FX-102] plugin_file にパス指定（拒否）: %s", plugin_file)
            body = json.dumps({
                "error":         "invalid_hbx_format",
                "error_message": "plugin_file フィールドが不正です。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        hbx_url = f"{repository}/releases/download/{release_tag}/{mod_id}.hbx"
        logger.info("[FX-102] downloading: %s", hbx_url)
        try:
            # file:// URL またはサーバー自身への localhost リクエスト対応
            # （FTテスト用: urllib はシングルスレッド HTTPServer へ自己リクエストできないため
            #   file:// スキームおよびローカルファイルパスへの直接読み込みをサポートする）
            if hbx_url.startswith("file://"):
                import urllib.parse
                local_path = urllib.parse.unquote(urllib.parse.urlparse(hbx_url).path)
                # Windows のみ: urlparse が返す /D:/path → D:/path へ補正
                # Ubuntu/Linux では /home/... のように先頭スラッシュが正しいパスなので除去しない
                if sys.platform == "win32" and len(local_path) >= 3 and local_path[0] == '/' and local_path[2] == ':':
                    local_path = local_path[1:]
                with open(local_path, "rb") as f:
                    hbx_data = f.read()
            else:
                req = urllib.request.Request(hbx_url)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    hbx_data = resp.read()
        except Exception as e:
            logger.warning("[FX-102] ダウンロード失敗: url=%s error=%s(%s)", hbx_url, type(e).__name__, e)
            body = json.dumps({
                "error":         "download_failed",
                "error_message": f".hbx ファイルのダウンロードに失敗しました: {type(e).__name__}: {e}",
                "hbx_url":       hbx_url,
                "retryable":     True,
            }, ensure_ascii=False).encode("utf-8")
            return Response(503, body, self._CONTENT_JSON)

        os.makedirs(PLUGINS_DIR, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                hbx_path = os.path.join(tmpdir, f"{mod_id}.hbx")
                with open(hbx_path, "wb") as f:
                    f.write(hbx_data)
                try:
                    with zipfile.ZipFile(hbx_path, "r") as zf:
                        zf.extractall(tmpdir)
                except zipfile.BadZipFile as e:
                    body = json.dumps({
                        "error":         "invalid_hbx_format",
                        "error_message": ".hbx ファイルの展開に失敗しました（不正な ZIP 形式）。",
                        "retryable":     False,
                    }, ensure_ascii=False).encode("utf-8")
                    return Response(400, body, self._CONTENT_JSON)

                if not os.path.exists(os.path.join(tmpdir, "version.txt")):
                    body = json.dumps({
                        "error":         "invalid_hbx_format",
                        "error_message": ".hbx に version.txt が含まれていません。",
                        "retryable":     False,
                    }, ensure_ascii=False).encode("utf-8")
                    return Response(400, body, self._CONTENT_JSON)

                extracted_plugin = os.path.join(tmpdir, plugin_file)
                if not os.path.exists(extracted_plugin):
                    body = json.dumps({
                        "error":         "invalid_hbx_format",
                        "error_message": f".hbx に {plugin_file} が含まれていません。",
                        "retryable":     False,
                    }, ensure_ascii=False).encode("utf-8")
                    return Response(400, body, self._CONTENT_JSON)

                dest_path = os.path.join(PLUGINS_DIR, plugin_file)
                shutil.copy2(extracted_plugin, dest_path)
                logger.info("[FX-102] デプロイ完了: %s → %s", plugin_file, dest_path)

        except Exception as e:
            logger.exception("[FX-102] デプロイ処理失敗: %s", e)
            body = json.dumps({
                "error":         "install_failed",
                "error_message": f"デプロイ処理に失敗しました: {type(e).__name__}: {e}",
                "retryable":     True,
            }, ensure_ascii=False).encode("utf-8")
            return Response(500, body, self._CONTENT_JSON)

        body = json.dumps({
            "id":      mod_id,
            "name":    target.get("name", mod_id),
            "version": target.get("version", ""),
            "message": "Install successful.",
        }, ensure_ascii=False).encode("utf-8")
        return Response(200, body, self._CONTENT_JSON)

    # ------------------------------------------------------------------
    # FX-103: POST /api/module/uninstall
    # ------------------------------------------------------------------

    def _api_post_module_uninstall(self) -> Response:
        """
        POST /api/module/uninstall の本実装。FD §4-1-3 準拠。

        plugin_file 解決ルール:
          インデックス JSON に id が存在し plugin_file 記載あり → plugins/<plugin_file>
          それ以外（private / plugin_file なし）              → plugins/<id>.py

        エラー:
          400 invalid_id           : id 不正 (retryable: false)
          404 module_not_installed : ファイル存在しない (retryable: false)
          500 uninstall_failed     : ファイル削除失敗 (retryable: true)
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw    = self.rfile.read(length) if length > 0 else b"{}"
            params = json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.warning("[FX-103] JSON 解析失敗: %s", e)
            body = json.dumps({
                "error":         "invalid_id",
                "error_message": "リクエストボディの JSON 解析に失敗しました。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        mod_id = str(params.get("id", "")).strip()
        if not mod_id or not VALID_PLUGIN_NAME.match(mod_id):
            body = json.dumps({
                "error":         "invalid_id",
                "error_message": "id フィールドが未送信または形式不正です（^[a-z0-9]+$ のみ許可）。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        ic         = self.__class__.index_cache
        index_data = ic.get() if ic else _empty_index()
        target     = next(
            (m for m in index_data.get("modules", []) if m.get("id") == mod_id), None
        )

        if target is not None and target.get("plugin_file"):
            plugin_file = target["plugin_file"]
        else:
            plugin_file = f"{mod_id}.py"

        # 多重防御: パス文字除去
        safe_plugin_file = os.path.basename(plugin_file)
        if not safe_plugin_file.endswith(".py") or not VALID_PLUGIN_NAME.match(safe_plugin_file[:-3]):
            logger.error("[FX-103] plugin_file が不正（拒否）: %r → safe: %r", plugin_file, safe_plugin_file)
            body = json.dumps({
                "error":         "invalid_id",
                "error_message": "内部エラー: plugin_file の形式が不正です。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        target_path = os.path.join(PLUGINS_DIR, safe_plugin_file)
        if not os.path.exists(target_path):
            logger.warning("[FX-103] アンインストール対象が存在しない: id=%s file=%s", mod_id, safe_plugin_file)
            body = json.dumps({
                "error":         "module_not_installed",
                "error_message": (
                    f"指定した ID '{mod_id}' のプラグインファイル（{safe_plugin_file}）が"
                    " plugins/ ディレクトリに存在しません（未インストール）。"
                ),
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(404, body, self._CONTENT_JSON)

        try:
            os.remove(target_path)
            logger.info("[FX-103] アンインストール完了: id=%s file=%s", mod_id, safe_plugin_file)
        except Exception as e:
            logger.exception("[FX-103] ファイル削除失敗: %s - %s", target_path, e)
            body = json.dumps({
                "error":         "uninstall_failed",
                "error_message": f"ファイルの削除に失敗しました: {{e}}",
                "retryable":     True,
            }, ensure_ascii=False).encode("utf-8")
            return Response(500, body, self._CONTENT_JSON)

        body = json.dumps({
            "id":      mod_id,
            "message": "Uninstall successful.",
        }, ensure_ascii=False).encode("utf-8")
        return Response(200, body, self._CONTENT_JSON)

    # ------------------------------------------------------------------
    # FX-104: POST /api/module/upload
    # ------------------------------------------------------------------

    def _api_post_module_upload(self) -> Response:
        """
        POST /api/module/upload の本実装。FD §4-1-4 準拠。

        ローカルの .hbx ファイルをアップロードし、展開・インストールする。
        private Module の手動デプロイに使用する。

        【multipart 解析の実装方針】
          cgi.FieldStorage は Python 3.13 で削除済みのため使用しない。
          email.message_from_bytes() + email.policy.compat32 を使用する。
          これは Python 3.10〜3.14 の全バージョンで動作する（FD 実装制約準拠）。

        【処理フロー（FD §4-1-4）】
          1. Content-Type の確認（multipart/form-data 以外は 400）
          2. boundary の取得
          3. Content-Length 分 rfile から読み取り（上限: _MAX_UPLOAD_SIZE = 10MB）
          4. email.message_from_bytes() で multipart パース
          5. "file" フィールドのパートを取得
          6. ファイル名取得・拡張子チェック（.hbx 以外は 400）
          7. .hbx (ZIP) 展開・バリデーション（version.txt / plugin_file）
          8. version.txt 1行目を mod_id として取得・検証
          9. 同一 mod_id がインストール済みなら 409 already_installed
          10. plugins/<mod_id>.py をコピー → 200

        エラーレスポンス（FD §4-1-4）:
          400 no_file              : file フィールドなし / Content-Type 不正 / サイズ 0
          400 invalid_extension    : 拡張子が .hbx でない (retryable: false)
          400 invalid_hbx_format   : ZIP 展開失敗 / plugin_file が .hbx 内にない (retryable: false)
          400 invalid_version_txt  : version.txt 未存在 / 形式不正 (retryable: false)
          409 already_installed    : 同一 ID がインストール済み (retryable: false)
          500 install_failed       : コピー等の予期せぬ失敗 (retryable: true)
        """
        import shutil
        import tempfile
        import zipfile

        # 1. Content-Type の確認
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            logger.warning("[FX-104] Content-Type が multipart/form-data でない: %r", content_type)
            body = json.dumps({
                "error":         "no_file",
                "error_message": "Content-Type が multipart/form-data ではありません。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        # 2. boundary の取得
        boundary = None
        for segment in content_type.split(";"):
            segment = segment.strip()
            if segment.startswith("boundary="):
                boundary = segment[len("boundary="):].strip().strip('"')
                break
        if not boundary:
            logger.warning("[FX-104] multipart boundary が取得できない")
            body = json.dumps({
                "error":         "no_file",
                "error_message": "multipart/form-data の boundary が取得できませんでした。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        # 3. Content-Length 分 rfile から読み取り（上限: _MAX_UPLOAD_SIZE）
        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_length)
        except (ValueError, TypeError):
            body = json.dumps({
                "error":         "no_file",
                "error_message": "Content-Length ヘッダが不正です。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        if content_length <= 0:
            body = json.dumps({
                "error":         "no_file",
                "error_message": "file フィールドが送信されていません（Content-Length = 0）。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        if content_length > _MAX_UPLOAD_SIZE:
            logger.warning("[FX-104] アップロードサイズが上限超過: %d > %d", content_length, _MAX_UPLOAD_SIZE)
            body = json.dumps({
                "error":         "no_file",
                "error_message": f"ファイルサイズが上限（{_MAX_UPLOAD_SIZE // (1024*1024)}MB）を超えています。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        # rfile から全ボディを読み取る（FX-009 の read_body() は使わない）
        try:
            raw_body = self.rfile.read(content_length)
        except Exception as e:
            logger.warning("[FX-104] rfile 読み取り失敗: %s", e)
            body = json.dumps({
                "error":         "no_file",
                "error_message": f"リクエストボディの読み取りに失敗しました: {{e}}",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        # 4. email.message_from_bytes() で multipart パース
        ct_header_line = f"Content-Type: {content_type}\r\n\r\n"
        full_msg_bytes = ct_header_line.encode("utf-8") + raw_body

        try:
            msg = email.message_from_bytes(full_msg_bytes, policy=email.policy.compat32)
        except Exception as e:
            logger.warning("[FX-104] multipart パース失敗: %s", e)
            body = json.dumps({
                "error":         "no_file",
                "error_message": f"multipart/form-data の解析に失敗しました: {{e}}",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        if not msg.is_multipart():
            body = json.dumps({
                "error":         "no_file",
                "error_message": "multipart/form-data として解析できませんでした。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        # 5. "file" フィールドのパートを取得
        file_part = None
        for part in msg.walk():
            cd = part.get("Content-Disposition", "")
            if "filename" not in cd:
                continue
            name_val = ""
            for seg in cd.split(";"):
                seg = seg.strip()
                if seg.startswith("name="):
                    name_val = seg[len("name="):].strip().strip('"')
            if name_val == "file":
                file_part = part
                break

        if file_part is None:
            body = json.dumps({
                "error":         "no_file",
                "error_message": (
                    "file フィールドが見つかりませんでした。"
                    " フォームフィールド名 'file' で .hbx ファイルを送信してください。"
                ),
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        # force フィールドの取得（Re-deploy時に true を送信）
        force = False
        for part in msg.walk():
            cd = part.get("Content-Disposition", "")
            name_val = ""
            for seg in cd.split(";"):
                seg = seg.strip()
                if seg.startswith("name="):
                    name_val = seg[len("name="):].strip().strip('"')
            if name_val == "force":
                force_val = (part.get_payload(decode=True) or b"").decode("utf-8", errors="ignore").strip()
                force = force_val.lower() in ("true", "1", "yes")
                break

        # 6. ファイル名取得・拡張子チェック
        raw_filename  = file_part.get_filename() or ""
        safe_filename = os.path.basename(raw_filename)
        if not safe_filename:
            body = json.dumps({
                "error":         "no_file",
                "error_message": "ファイル名が取得できませんでした。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        if not safe_filename.endswith(".hbx"):
            logger.warning("[FX-104] 拡張子が .hbx でない: %r", safe_filename)
            body = json.dumps({
                "error":         "invalid_extension",
                "error_message": f"ファイル拡張子が .hbx ではありません（受信: {safe_filename!r}）。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        # 7. .hbx (ZIP) 展開・バリデーション
        hbx_data = file_part.get_payload(decode=True)
        if not hbx_data:
            body = json.dumps({
                "error":         "no_file",
                "error_message": "ファイルデータが空です。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, body, self._CONTENT_JSON)

        os.makedirs(PLUGINS_DIR, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                hbx_path = os.path.join(tmpdir, safe_filename)
                with open(hbx_path, "wb") as f:
                    f.write(hbx_data)

                # ZIP 展開
                try:
                    with zipfile.ZipFile(hbx_path, "r") as zf:
                        zf.extractall(tmpdir)
                except zipfile.BadZipFile as e:
                    logger.warning("[FX-104] ZIP 展開失敗: %s", e)
                    body = json.dumps({
                        "error":         "invalid_hbx_format",
                        "error_message": f".hbx ファイルの展開に失敗しました（不正な ZIP 形式）: {e}",
                        "retryable":     False,
                    }, ensure_ascii=False).encode("utf-8")
                    return Response(400, body, self._CONTENT_JSON)

                # version.txt の存在確認
                version_txt_path = os.path.join(tmpdir, "version.txt")
                if not os.path.exists(version_txt_path):
                    logger.warning("[FX-104] version.txt が存在しない")
                    body = json.dumps({
                        "error":         "invalid_version_txt",
                        "error_message": ".hbx に version.txt が含まれていません。",
                        "retryable":     False,
                    }, ensure_ascii=False).encode("utf-8")
                    return Response(400, body, self._CONTENT_JSON)

                # 8. version.txt の 1行目を mod_id として取得・検証
                try:
                    with open(version_txt_path, "r", encoding="utf-8") as vf:
                        first_line = vf.readline().strip()
                except Exception as e:
                    logger.warning("[FX-104] version.txt 読み取り失敗: %s", e)
                    body = json.dumps({
                        "error":         "invalid_version_txt",
                        "error_message": f"version.txt の読み取りに失敗しました: {e}",
                        "retryable":     False,
                    }, ensure_ascii=False).encode("utf-8")
                    return Response(400, body, self._CONTENT_JSON)

                mod_id = first_line
                if not mod_id or not VALID_PLUGIN_NAME.match(mod_id):
                    logger.warning("[FX-104] version.txt の id が不正: %r", mod_id)
                    body = json.dumps({
                        "error":         "invalid_version_txt",
                        "error_message": (
                            f"version.txt の1行目がモジュール ID として不正です（取得値: {mod_id!r}）。"
                            " ^[a-z0-9]+$ のみ許可されます。"
                        ),
                        "retryable":     False,
                    }, ensure_ascii=False).encode("utf-8")
                    return Response(400, body, self._CONTENT_JSON)

                plugin_file = mod_id + ".py"

                # plugin_file が展開先に存在するか確認
                extracted_plugin = os.path.join(tmpdir, plugin_file)
                if not os.path.exists(extracted_plugin):
                    logger.warning("[FX-104] plugin_file が .hbx 内に存在しない: %s", plugin_file)
                    body = json.dumps({
                        "error":         "invalid_hbx_format",
                        "error_message": (
                            f".hbx に {plugin_file} が含まれていません。"
                            f" version.txt の ID ({mod_id}) と一致する .py ファイルが必要です。"
                        ),
                        "retryable":     False,
                    }, ensure_ascii=False).encode("utf-8")
                    return Response(400, body, self._CONTENT_JSON)

                # 9. 同一 mod_id がすでにインストール済みか確認
                #    force=true の場合は 409 をスキップして上書きインストール（FX-205 Re-deploy対応）
                dest_path = os.path.join(PLUGINS_DIR, plugin_file)
                if os.path.exists(dest_path) and not force:
                    logger.info("[FX-104] already_installed: %s", mod_id)
                    body = json.dumps({
                        "error":         "already_installed",
                        "error_message": (
                            f"Module ID '{mod_id}' はすでにインストール済みです。"
                            " Re-deploy する場合は force=true を指定してください。"
                        ),
                        "module_id":     mod_id,
                        "retryable":     False,
                    }, ensure_ascii=False).encode("utf-8")
                    return Response(409, body, self._CONTENT_JSON)

                # 10. plugins/ へコピー
                shutil.copy2(extracted_plugin, dest_path)
                logger.info("[FX-104] アップロードインストール完了: %s → %s", plugin_file, dest_path)

        except Exception as e:
            logger.exception("[FX-104] アップロードインストール処理失敗: %s", e)
            body = json.dumps({
                "error":         "install_failed",
                "error_message": f"インストール処理中に予期せぬエラーが発生しました: {e}",
                "retryable":     True,
            }, ensure_ascii=False).encode("utf-8")
            return Response(500, body, self._CONTENT_JSON)

        body = json.dumps({
            "id":      mod_id,
            "name":    mod_id,
            "message": "Upload and install successful.",
        }, ensure_ascii=False).encode("utf-8")
        return Response(200, body, self._CONTENT_JSON)

    # ------------------------------------------------------------------
    # FX-105: POST /api/index/refresh
    # ------------------------------------------------------------------

    def _api_post_index_refresh(self) -> Response:
        """
        POST /api/index/refresh の本実装。FD §4-1-5 準拠。
        IndexCache.refresh() の戻り値タプルを HTTP レスポンスに変換する。
        """
        ic = self.__class__.index_cache
        if ic is None:
            body = json.dumps({
                "error":         "index_fetch_failed",
                "error_message": "IndexCache が初期化されていません。",
                "retryable":     True,
            }, ensure_ascii=False).encode("utf-8")
            return Response(503, body, self._CONTENT_JSON)

        ok, result = ic.refresh()

        if ok:
            body = json.dumps({
                "message":      "Index refreshed.",
                "updated":      datetime.now().strftime("%Y-%m-%d"),
                "module_count": len(result.get("modules", [])),
            }, ensure_ascii=False).encode("utf-8")
            return Response(200, body, self._CONTENT_JSON)

        if result == "in_progress":
            body = json.dumps({
                "error":         "refresh_in_progress",
                "error_message": "インデックスの再取得処理が実行中です。しばらくお待ちください。",
                "retryable":     False,
            }, ensure_ascii=False).encode("utf-8")
            return Response(409, body, self._CONTENT_JSON)

        if result == "cache_failed":
            body = json.dumps({
                "error":         "cache_write_failed",
                "error_message": "インデックスキャッシュファイルの書き込みに失敗しました。",
                "retryable":     True,
            }, ensure_ascii=False).encode("utf-8")
            return Response(500, body, self._CONTENT_JSON)

        body = json.dumps({
            "error":         "index_fetch_failed",
            "error_message": "GitHub への接続に失敗しました（タイムアウト・接続エラー含む）。",
            "retryable":     True,
        }, ensure_ascii=False).encode("utf-8")
        return Response(503, body, self._CONTENT_JSON)

    # ------------------------------------------------------------------
    # FX-106: GET /api/status
    # ------------------------------------------------------------------

    def _api_get_status(self) -> Response:
        """
        GET /api/status の本実装。FD §4-1-6 準拠。

        freebox_service : 常に "running"（応答できている = 起動中）
        nas             : is_nas_available(nas_mount_point) の結果
        apache2         : systemctl is-active apache2 の実行結果
        scheduler_jobs  : scheduler.get_jobs() の件数

        FX-208 との連携:
          このAPIは FX-208 StatusBar ポーリングから 30 秒ごとに呼び出される。
          レスポンスは必ず FD §4-1-6 の4フィールドを含むこと。
        """
        import subprocess

        try:
            freebox_status = "running"

            cfg             = self.__class__.cfg
            nas_mount_point = (
                cfg.get("status", "nas_mount_point", fallback="/mnt/nas")
                if cfg else "/mnt/nas"
            )
            nas_status = "connected" if is_nas_available(nas_mount_point) else "disconnected"

            try:
                result = subprocess.run(
                    ["systemctl", "is-active", "apache2"],
                    capture_output=True, text=True, timeout=5,
                )
                apache2_status = "running" if result.stdout.strip() == "active" else "stopped"
            except FileNotFoundError:
                logger.warning("[FX-106] systemctl が見つからない（開発環境）")
                apache2_status = "unknown"
            except subprocess.TimeoutExpired:
                logger.warning("[FX-106] systemctl is-active apache2 がタイムアウト")
                apache2_status = "unknown"
            except Exception as e:
                logger.warning("[FX-106] apache2 ステータス取得失敗: %s", e)
                apache2_status = "unknown"

            sched = self.__class__.scheduler
            try:
                job_count = len(sched.get_jobs()) if sched else 0
            except Exception:
                job_count = 0

            body = json.dumps({
                "freebox_service": freebox_status,
                "nas":             nas_status,
                "apache2":         apache2_status,
                "scheduler_jobs":  job_count,
            }, ensure_ascii=False).encode("utf-8")
            return Response(200, body, self._CONTENT_JSON)

        except Exception as e:
            logger.exception("[FX-106] ステータス取得失敗: %s", e)
            body = json.dumps({
                "error":         "status_check_failed",
                "error_message": f"ステータス取得処理で予期せぬ例外が発生しました: {e}",
                "retryable":     True,
            }, ensure_ascii=False).encode("utf-8")
            return Response(500, body, self._CONTENT_JSON)

    # ------------------------------------------------------------------
    # FX-107: GET /favicon.ico
    # ------------------------------------------------------------------

    def _handle_favicon(self) -> None:
        """
        /favicon.ico リクエストを処理する。
        BASE_DIR/favicon.ico が存在すれば配信。なければ 204 No Content を返す。
        404 を返すとブラウザが毎回リトライして 400 ログが大量に出るため 204 で抑制する。
        """
        favicon_path = os.path.join(BASE_DIR, "favicon.ico")
        if os.path.exists(favicon_path):
            try:
                with open(favicon_path, "rb") as f:
                    data = f.read()
                _send_response(self, 200, data, "image/x-icon")
                return
            except Exception as e:
                logger.warning("[FX-107] favicon.ico 読み取り失敗: %s", e)
        # ファイルなし → 204 No Content（ブラウザのリトライを抑制）
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        # /favicon.ico は _dispatch を通さず直接処理
        if self.path == "/favicon.ico":
            self._handle_favicon()
            return
        self._dispatch()

    def do_POST(self):
        self._dispatch()


# ===========================================================================
# FX-001: エントリーポイント
# ===========================================================================

def main() -> None:
    """
    freeBox Loader サーバーのエントリーポイント。

    FD §3-3 systemd サービス仕様との整合:
      - ExecStart で直接呼び出されるため引数なし
      - WorkingDirectory = /home/hsbox/freebox/
      - Restart=on-failure で異常終了時に自動再起動される

    起動シーケンス:
      1. sys.modules 登録（FX-009: Plugin の from box_webserver import を可能にする）
      2. 設定読み込み
      3. データディレクトリ作成
      4. IndexCache 初期化
      5. インデックス初回取得（バックグラウンド）
      6. PluginManager → Plugin ロード
      7. ManagerPlugin 初期化
      8. Scheduler 起動 → Plugin スケジュール登録
      9. HTTPServer 起動 → 起動通知
      10. serve_forever()
    """
    # FX-009: Plugin が "from box_webserver import Response" できるようにする。
    if "box_webserver" not in sys.modules:
        sys.modules["box_webserver"] = sys.modules["__main__"]

    cfg = load_config()

    host             = cfg.get("server",  "host",             fallback=DEFAULT_HOST)
    port             = cfg.getint("server", "port",           fallback=DEFAULT_PORT)
    notify_url       = cfg.get("status",  "notify_url",       fallback="")
    notify_component = cfg.get("status",  "notify_component", fallback="FreeBoxLoader")
    index_url        = cfg.get("loader",  "index_url",        fallback=DEFAULT_INDEX_URL)
    cache_ttl        = cfg.getint("loader", "index_cache_ttl", fallback=DEFAULT_CACHE_TTL)

    logger.info(
        "[FX-001] 起動パラメータ: host=%s port=%d index_url=%s cache_ttl=%d",
        host, port, index_url, cache_ttl,
    )

    os.makedirs(DATA_DIR, exist_ok=True)

    index_cache = IndexCache(index_url, cache_ttl, INDEX_CACHE_PATH)
    FreeBoxHandler.index_cache = index_cache

    def _initial_fetch() -> None:
        logger.info("[FX-004] インデックス初回取得（バックグラウンド）")
        index_cache.get()

    threading.Thread(target=_initial_fetch, daemon=True, name="index-fetch-init").start()

    pm = PluginManager()
    pm.load_plugins()
    FreeBoxHandler.plugin_manager = pm
    FreeBoxHandler.cfg            = cfg

    FreeBoxHandler.manager_plugin = ManagerPlugin()

    scheduler = Scheduler()
    scheduler.register_plugins(pm)
    scheduler.start()
    FreeBoxHandler.scheduler = scheduler

    try:
        server = ThreadingHTTPServer((host, port), FreeBoxHandler)
        logger.info("[FX-001] マルチスレッドモードで起動（ThreadingHTTPServer）")
    except Exception as e:
        logger.critical("[FX-001] HTTPサーバー起動失敗: %s", e)
        send_notify(notify_url, notify_component, NOTIFY_MSG_FAIL)
        sys.exit(1)

    logger.info("[FX-001] freeBox Loader 起動完了: http://%s:%d/", host, port)

    def _sigterm_handler(signum, frame) -> None:
        logger.info("[FX-007] SIGTERM を受信。正常停止シーケンスを開始します。")
        send_notify(notify_url, notify_component, NOTIFY_MSG_OK)
        # serve_forever() のブロックをメインスレッドから解除できないため、別スレッドで shutdown() を呼び出す。
        t = threading.Thread(target=server.shutdown, daemon=True, name="sigterm-shutdown")
        t.start()

    signal.signal(signal.SIGTERM, _sigterm_handler)
    logger.info("[FX-007] SIGTERM ハンドラ登録完了")

    send_notify(notify_url, notify_component, NOTIFY_MSG_OK)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("[FX-001] シャットダウン要求を受信（KeyboardInterrupt）")
    finally:
        server.server_close()
        logger.info("[FX-001] freeBox Loader 停止完了")


if __name__ == "__main__":
    main()

