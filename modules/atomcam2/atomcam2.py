#!/usr/bin/env python3
"""
atomcam2.py - freeBox Plugin: ATOMCAM2 キャプチャ

ATOMCAM2 の RTSP ストリームから ffmpeg で1フレームをキャプチャし、
NAS に定期保存する。10分間隔で自動実行。

ディレクトリ構造（G-21 対応）:
  plugins/atomcam2.py              ← 本ファイル（エントリポイント）
  plugins/atomcam2/
    atomcam2_config.ini            ← 設定ファイル（mac/rtsp/nas 設定）

エンドポイント一覧:
  GET  /atomcam2/        設定フォームつきステータス画面
  GET  /atomcam2/status  最終キャプチャ状態を JSON で返す
  GET  /atomcam2/config  現在の設定値を JSON で返す
  POST /atomcam2/capture 手動キャプチャを実行し結果を JSON で返す
  POST /atomcam2/config  設定値を保存する（JSON ボディ）

v1 スコープ:
  - 設定 GUI（MAC アドレス・RTSP・NAS パス）
  - 即時キャプチャ（POST /capture）
  - 定期キャプチャ（10分スケジューラ）
  - キャプチャ成否ステータス表示
  - MAC アドレスから IP を ARP で解決（DHCP 環境対応）

v2 以降:
  - キャプチャ間隔の変更
  - 複数カメラ対応
"""

import configparser
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

try:
    from box_webserver import Response
except ImportError:
    from dataclasses import dataclass as _dc

    @_dc
    class Response:  # type: ignore[no-redef]
        status:       int   = 200
        body:         bytes = b""
        content_type: str   = "text/plain"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# デフォルト設定値
# ---------------------------------------------------------------------------
_DEFAULT_MAC       = ""
_DEFAULT_USER      = "admin"
_DEFAULT_PASS      = ""
_DEFAULT_NAS_DIR   = "/mnt/nas"
_DEFAULT_RTSP_PORT = "554"

# G-21: 設定ファイルをサブディレクトリ plugins/atomcam2/ に配置する
_PLUGIN_DIR  = os.path.dirname(os.path.abspath(__file__))
_SUBDIR      = os.path.join(_PLUGIN_DIR, "atomcam2")
_CONFIG_PATH = os.path.join(_SUBDIR, "atomcam2_config.ini")

_MAC_RE = re.compile(r'(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}')
_IP_RE  = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

# 設定キーの許可リスト（POST /atomcam2/config で受け付けるキー名）
_ALLOWED_CONFIG_KEYS = frozenset({
    "mac", "rtsp_user", "rtsp_pass", "rtsp_port", "nas_dir"
})


# ---------------------------------------------------------------------------
# 設定値 dataclass
# ---------------------------------------------------------------------------
@dataclass
class _CaptureConfig:
    mac:       str
    rtsp_user: str
    rtsp_pass: str
    rtsp_port: str
    nas_dir:   str


# ---------------------------------------------------------------------------
# NAS 確認
# ---------------------------------------------------------------------------
try:
    from box_webserver import is_nas_available as _is_nas_available
except ImportError:
    def _is_nas_available(mount_point: str) -> bool:  # type: ignore[misc]
        try:
            return os.path.ismount(mount_point) and os.access(mount_point, os.W_OK)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 設定読み込み
# ---------------------------------------------------------------------------
def _load_plugin_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg["camera"] = {
        "mac":       _DEFAULT_MAC,
        "rtsp_user": _DEFAULT_USER,
        "rtsp_pass": _DEFAULT_PASS,
        "rtsp_port": _DEFAULT_RTSP_PORT,
    }
    cfg["storage"] = {
        "nas_dir": _DEFAULT_NAS_DIR,
    }
    # G-21: サブディレクトリが存在しない場合は作成する（初回起動時の安全策）
    os.makedirs(_SUBDIR, exist_ok=True)

    if os.path.exists(_CONFIG_PATH):
        cfg.read(_CONFIG_PATH, encoding="utf-8")
        logger.info("[atomcam2] 設定ファイル読み込み: %s", _CONFIG_PATH)
    else:
        logger.warning("[atomcam2] 設定ファイルなし。デフォルト値を使用: %s", _CONFIG_PATH)
    return cfg


# ---------------------------------------------------------------------------
# MAC アドレスから IP を解決
# ---------------------------------------------------------------------------
def _resolve_ip_from_mac(mac: str) -> "str | None":
    """
    MAC アドレスから ARP テーブルで IP を解決する。
    v1: 毎回 MAC から解決（DHCP 環境で IP が変わっても追従できる）。
    v2 以降: IP が設定済みの場合は IP を直接使用し、失敗時のみ MAC で再解決する予定。
    """
    if not mac:
        return None

    target_mac = mac.replace(":", "").replace("-", "").lower()

    if os.name != "nt":
        try:
            subprocess.run(
                ["arp-scan", "--localnet", "--retry=2"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except FileNotFoundError:
            logger.debug("[atomcam2] arp-scan が見つかりません（スキップ）")
        except subprocess.TimeoutExpired:
            logger.warning("[atomcam2] arp-scan タイムアウト")
        except Exception as e:
            logger.warning("[atomcam2] arp-scan 実行エラー: %s", e)

    try:
        encoding = "cp932" if os.name == "nt" else "utf-8"
        result = subprocess.check_output(["arp", "-a"], timeout=5).decode(encoding, errors="replace")
        for line in result.splitlines():
            found_macs = _MAC_RE.findall(line)
            for found_mac in found_macs:
                normalized = found_mac.replace(":", "").replace("-", "").lower()
                if normalized == target_mac:
                    ip_match = _IP_RE.search(line)
                    if ip_match:
                        return ip_match.group(1)
    except FileNotFoundError:
        logger.error("[atomcam2] arp コマンドが見つかりません")
    except subprocess.CalledProcessError as e:
        logger.warning("[atomcam2] arp -a 実行エラー (returncode=%d)", e.returncode)
    except subprocess.TimeoutExpired:
        logger.warning("[atomcam2] arp -a タイムアウト")
    except Exception as e:
        logger.warning("[atomcam2] ARP 検索エラー: %s", e)
    return None


# ---------------------------------------------------------------------------
# ffmpeg でキャプチャ
# ---------------------------------------------------------------------------
def _capture_frame(rtsp_url: str, save_path: str) -> bool:
    cmd = [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-ss", "00:00:01",
        "-vframes", "1",
        "-q:v", "2",
        "-y", save_path,
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=30,
        )
        return True
    except subprocess.TimeoutExpired:
        logger.warning("[atomcam2] ffmpeg タイムアウト")
        return False
    except subprocess.CalledProcessError as e:
        logger.warning("[atomcam2] ffmpeg 失敗 (returncode=%d)", e.returncode)
        return False
    except FileNotFoundError:
        logger.error("[atomcam2] ffmpeg が見つかりません")
        return False
    except Exception as e:
        logger.warning("[atomcam2] ffmpeg 例外: %s", e)
        return False


# ===========================================================================
# Plugin クラス
# ===========================================================================
class Plugin:
    """ATOMCAM2 キャプチャ Plugin"""

    def __init__(self):
        self._cfg = _load_plugin_config()
        self._last_capture: dict = {
            "status":     "init",
            "timestamp":  None,
            "saved_path": None,
            "message":    "未実行",
        }

    # ------------------------------------------------------------------
    # ルーティング
    # ------------------------------------------------------------------
    def can_handle(self, path: str) -> bool:
        return (
            path.startswith("/") and
            re.split(r"[^0-9A-Za-z]+", path[1:])[0] == "atomcam2"
        )

    def handle(self, req) -> Response:
        path = req.path.split("?")[0].rstrip("/")

        # POST /atomcam2/capture
        if req.method == "POST" and path == "/atomcam2/capture":
            self._do_capture()
            body = json.dumps(self._last_capture, ensure_ascii=False).encode("utf-8")
            return Response(200, body, "application/json; charset=utf-8")

        # GET /atomcam2/status
        if req.method == "GET" and path == "/atomcam2/status":
            body = json.dumps(self._last_capture, ensure_ascii=False).encode("utf-8")
            return Response(200, body, "application/json; charset=utf-8")

        # GET /atomcam2/config
        if req.method == "GET" and path == "/atomcam2/config":
            return self._handle_get_config()

        # POST /atomcam2/config
        if req.method == "POST" and path == "/atomcam2/config":
            return self._handle_post_config(req)

        # GET /atomcam2/ (トップ画面)
        body = self._render_html().encode("utf-8")
        return Response(200, body, "text/html; charset=utf-8")

    # ------------------------------------------------------------------
    # スケジューラ登録
    # ------------------------------------------------------------------
    def register_schedule(self, scheduler) -> None:
        scheduler.schedule(
            name="atomcam2_capture",
            interval_minutes=10,
            func=self.capture,
            timeout_minutes=5,
        )

    def capture(self) -> None:
        try:
            self._do_capture()
        except Exception as e:
            logger.exception("[atomcam2] capture() 予期せぬ例外: %s", e)
            self._set_status("error", f"予期せぬ例外: {e}")

    # ------------------------------------------------------------------
    # GET /atomcam2/config
    # ------------------------------------------------------------------
    def _handle_get_config(self) -> Response:
        """現在の設定値を JSON で返す。パスワードは伏字にしない。"""
        data = {
            "mac":       self._cfg.get("camera",  "mac",       fallback=_DEFAULT_MAC),
            "rtsp_user": self._cfg.get("camera",  "rtsp_user", fallback=_DEFAULT_USER),
            "rtsp_pass": self._cfg.get("camera",  "rtsp_pass", fallback=_DEFAULT_PASS),
            "rtsp_port": self._cfg.get("camera",  "rtsp_port", fallback=_DEFAULT_RTSP_PORT),
            "nas_dir":   self._cfg.get("storage", "nas_dir",   fallback=_DEFAULT_NAS_DIR),
        }
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return Response(200, body, "application/json; charset=utf-8")

    # ------------------------------------------------------------------
    # POST /atomcam2/config
    # ------------------------------------------------------------------
    def _handle_post_config(self, req) -> Response:
        """
        設定値を JSON ボディで受け取り、atomcam2_config.ini に保存する。

        受け付けるフィールド:
          mac       : カメラの MAC アドレス（空文字も可）
          rtsp_user : RTSP ユーザー名
          rtsp_pass : RTSP パスワード（空文字も可）
          rtsp_port : RTSP ポート番号（数字文字列）
          nas_dir   : NAS 保存先ディレクトリの絶対パス

        エラーレスポンス:
          400 : JSON 解析失敗 / 不正フィールド名 / rtsp_port が数字でない
          500 : 設定ファイル書き込み失敗
        """
        try:
            body_bytes = req.read_body()
            params = json.loads(body_bytes.decode("utf-8"))
        except Exception as e:
            logger.warning("[atomcam2] POST /config JSON 解析失敗: %s", e)
            err = json.dumps({
                "error": "invalid_request",
                "message": "JSON の解析に失敗しました。",
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, err, "application/json; charset=utf-8")

        if not isinstance(params, dict):
            err = json.dumps({
                "error": "invalid_request",
                "message": "リクエストボディはオブジェクト形式にしてください。",
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, err, "application/json; charset=utf-8")

        # 許可されていないキーの拒否
        unknown_keys = set(params.keys()) - _ALLOWED_CONFIG_KEYS
        if unknown_keys:
            err = json.dumps({
                "error": "invalid_request",
                "message": f"不明なフィールド: {sorted(unknown_keys)}",
            }, ensure_ascii=False).encode("utf-8")
            return Response(400, err, "application/json; charset=utf-8")

        # rtsp_port は数字のみ許可
        if "rtsp_port" in params:
            port_str = str(params["rtsp_port"]).strip()
            if not port_str.isdigit():
                err = json.dumps({
                    "error": "invalid_request",
                    "message": "rtsp_port は数字のみ指定できます。",
                }, ensure_ascii=False).encode("utf-8")
                return Response(400, err, "application/json; charset=utf-8")
            params["rtsp_port"] = port_str

        # 設定を更新
        if "camera" not in self._cfg:
            self._cfg["camera"] = {}
        if "storage" not in self._cfg:
            self._cfg["storage"] = {}

        camera_keys  = {"mac", "rtsp_user", "rtsp_pass", "rtsp_port"}
        storage_keys = {"nas_dir"}

        for key, val in params.items():
            if key in camera_keys:
                self._cfg["camera"][key] = str(val)
            elif key in storage_keys:
                self._cfg["storage"][key] = str(val)

        # G-21: サブディレクトリが存在しない場合は作成してから書き込む
        os.makedirs(_SUBDIR, exist_ok=True)
        try:
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                self._cfg.write(f)
            logger.info("[atomcam2] 設定を保存しました: %s", _CONFIG_PATH)
        except Exception as e:
            logger.error("[atomcam2] 設定ファイル書き込み失敗: %s", e)
            err = json.dumps({
                "error": "save_failed",
                "message": f"設定ファイルの書き込みに失敗しました: {e}",
            }, ensure_ascii=False).encode("utf-8")
            return Response(500, err, "application/json; charset=utf-8")

        result = json.dumps({"message": "設定を保存しました。"}, ensure_ascii=False).encode("utf-8")
        return Response(200, result, "application/json; charset=utf-8")

    # ------------------------------------------------------------------
    # キャプチャ処理
    # ------------------------------------------------------------------
    def _read_capture_config(self) -> _CaptureConfig:
        c = self._cfg
        return _CaptureConfig(
            mac=c.get("camera",  "mac",       fallback=_DEFAULT_MAC),
            rtsp_user=c.get("camera",  "rtsp_user", fallback=_DEFAULT_USER),
            rtsp_pass=c.get("camera",  "rtsp_pass", fallback=_DEFAULT_PASS),
            rtsp_port=c.get("camera",  "rtsp_port", fallback=_DEFAULT_RTSP_PORT),
            nas_dir=c.get("storage", "nas_dir",   fallback=_DEFAULT_NAS_DIR),
        )

    def _do_capture(self) -> None:
        cfg = self._read_capture_config()
        if not self._check_nas(cfg.nas_dir):
            return
        ip = self._resolve_ip(cfg.mac)
        if not ip:
            return
        save_path = self._prepare_save_path(cfg.nas_dir)
        if not save_path:
            return
        self._run_capture(cfg, ip, save_path)

    def _check_nas(self, nas_dir: str) -> bool:
        if not _is_nas_available(nas_dir):
            logger.warning("[atomcam2] NAS 未接続、キャプチャをスキップ: %s", nas_dir)
            self._set_status("skip_nas", f"NAS 未接続: {nas_dir}")
            return False
        return True

    def _resolve_ip(self, mac: str) -> "str | None":
        ip = _resolve_ip_from_mac(mac)
        if not ip:
            logger.warning("[atomcam2] カメラ IP を解決できません (MAC=%s)", mac)
            self._set_status("skip_ip", f"IP 解決失敗 (MAC={mac})")
        return ip

    def _prepare_save_path(self, nas_dir: str) -> "str | None":
        today_str = datetime.now().strftime("%Y-%m-%d")
        daily_dir = os.path.join(nas_dir, today_str)
        try:
            os.makedirs(daily_dir, exist_ok=True)
        except Exception as e:
            logger.error("[atomcam2] ディレクトリ作成失敗: %s - %s", daily_dir, e)
            self._set_status("error", f"ディレクトリ作成失敗: {e}")
            return None
        filename = datetime.now().strftime("%Y%m%d_%H%M%S.jpg")
        return os.path.join(daily_dir, filename)

    def _run_capture(self, cfg: _CaptureConfig, ip: str, save_path: str) -> None:
        rtsp_url         = f"rtsp://{cfg.rtsp_user}:{cfg.rtsp_pass}@{ip}:{cfg.rtsp_port}/live"
        rtsp_url_for_log = f"rtsp://{cfg.rtsp_user}:***@{ip}:{cfg.rtsp_port}/live"
        logger.info("[atomcam2] キャプチャ開始: %s → %s", rtsp_url_for_log, save_path)
        ok = _capture_frame(rtsp_url, save_path)
        if ok:
            logger.info("[atomcam2] 保存完了: %s", save_path)
            self._set_status("ok", "キャプチャ成功", saved_path=save_path)
        else:
            logger.warning("[atomcam2] キャプチャ失敗: %s", save_path)
            self._set_status("error", "ffmpeg キャプチャ失敗")

    def _set_status(self, status: str, message: str, saved_path: "str | None" = None) -> None:
        self._last_capture = {
            "status":     status,
            "timestamp":  datetime.now().isoformat(timespec="seconds"),
            "saved_path": saved_path,
            "message":    message,
        }

    # ------------------------------------------------------------------
    # HTML レンダリング
    # ------------------------------------------------------------------
    def _render_html(self) -> str:
        lc = self._last_capture
        status_icon = {
            "init":     "&#9898;",
            "ok":       "&#128994;",
            "skip_nas": "&#128993;",
            "skip_ip":  "&#128993;",
            "error":    "&#128308;",
        }.get(lc["status"], "&#9898;")

        saved      = lc.get("saved_path") or "&#x2015;"
        ts         = lc.get("timestamp")  or "&#x2015;"
        msg        = lc.get("message")    or "&#x2015;"
        nas_dir    = self._cfg.get("storage", "nas_dir",   fallback=_DEFAULT_NAS_DIR)
        mac        = self._cfg.get("camera",  "mac",       fallback="(未設定)")
        rtsp_user  = self._cfg.get("camera",  "rtsp_user", fallback=_DEFAULT_USER)
        rtsp_pass  = self._cfg.get("camera",  "rtsp_pass", fallback="")
        rtsp_port  = self._cfg.get("camera",  "rtsp_port", fallback=_DEFAULT_RTSP_PORT)

        # キャプチャ成否をステータス表示（NAS 接続状態ではなくキャプチャ結果を主表示）
        capture_status_label = {
            "init":     "未実行",
            "ok":       "成功",
            "skip_nas": "NAS 未接続のためスキップ",
            "skip_ip":  "カメラ IP 解決失敗のためスキップ",
            "error":    "失敗",
        }.get(lc["status"], lc["status"])

        nas_available = _is_nas_available(nas_dir)
        nas_status = "&#10003; 接続中" if nas_available else "&#10007; 未接続"

        # HTML 特殊文字のエスケープ（設定値を属性・テキストに埋め込む際に使用）
        def _he(s: str) -> str:
            return (s.replace("&", "&amp;")
                     .replace("<", "&lt;")
                     .replace(">", "&gt;")
                     .replace('"', "&quot;"))

        return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>ATOMCAM2 Plugin</title>
<style>
  body  {{ font-family: sans-serif; padding: 1.5rem; background: #f5f5f5; }}
  h1   {{ font-size: 1.3rem; margin-bottom: 1rem; }}
  h2   {{ font-size: 1.1rem; margin: 1.5rem 0 0.5rem; border-bottom: 1px solid #ccc; padding-bottom: 0.3rem; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 640px; background: #fff; margin-bottom: 1rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.8rem; border: 1px solid #ddd; }}
  th {{ background: #eee; width: 35%; }}
  .form-row {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.6rem; max-width: 640px; }}
  .form-row label {{ width: 180px; flex-shrink: 0; font-size: 0.9rem; }}
  .form-row input {{ flex: 1; padding: 0.35rem 0.5rem; border: 1px solid #ccc; border-radius: 3px; font-size: 0.9rem; }}
  .form-row .hint {{ font-size: 0.78rem; color: #888; margin-left: 0.3rem; white-space: nowrap; }}
  .btn {{
    margin-top: 0.5rem; padding: 0.45rem 1.2rem;
    background: #4a90d9; color: #fff; border: none;
    border-radius: 4px; cursor: pointer; font-size: 0.9rem;
  }}
  .btn:hover {{ background: #357abf; }}
  .btn-save {{ background: #4caf50; }}
  .btn-save:hover {{ background: #388e3c; }}
  .status-ok   {{ color: #27ae60; font-weight: bold; }}
  .status-err  {{ color: #e74c3c; font-weight: bold; }}
  .status-warn {{ color: #f39c12; font-weight: bold; }}
  #result, #config-result {{ margin-top: 0.6rem; font-size: 0.85rem; color: #333; min-height: 1.2em; }}
  .mac-hint {{ font-size: 0.8rem; color: #555; margin-top: 0.3rem; max-width: 640px; }}
</style>
</head>
<body>
<h1>&#128247; ATOMCAM2 Plugin</h1>

<h2>キャプチャ状態</h2>
<table>
  <tr><th>最終キャプチャ</th><td>{status_icon} <span class="{'status-ok' if lc['status'] == 'ok' else 'status-err' if lc['status'] == 'error' else 'status-warn'}">{capture_status_label}</span></td></tr>
  <tr><th>実行日時</th><td>{ts}</td></tr>
  <tr><th>メッセージ</th><td>{msg}</td></tr>
  <tr><th>保存パス</th><td>{saved}</td></tr>
  <tr><th>NAS ({_he(nas_dir)})</th><td>{nas_status}</td></tr>
</table>
<button class="btn" onclick="doCapture()">今すぐキャプチャ</button>
<div id="result"></div>

<h2>設定</h2>
<p style="font-size:0.85rem;color:#555;max-width:640px;margin-bottom:0.8rem;">
  カメラの MAC アドレスを設定すると、DHCP で IP が変わっても自動で追従します。<br>
  MAC アドレスはカメラ底面または ATOM Cam アプリの「デバイス情報」から確認できます。
</p>
<div class="form-row">
  <label>カメラ MAC アドレス</label>
  <input type="text" id="cfg-mac" value="{_he(mac)}" placeholder="AA:BB:CC:DD:EE:FF">
  <span class="hint">ARP で IP を自動解決</span>
</div>
<div class="form-row">
  <label>RTSP ユーザー名</label>
  <input type="text" id="cfg-rtsp-user" value="{_he(rtsp_user)}">
</div>
<div class="form-row">
  <label>RTSP パスワード</label>
  <input type="password" id="cfg-rtsp-pass" value="{_he(rtsp_pass)}">
</div>
<div class="form-row">
  <label>RTSP ポート</label>
  <input type="text" id="cfg-rtsp-port" value="{_he(rtsp_port)}" placeholder="554">
</div>
<div class="form-row">
  <label>NAS 保存先ディレクトリ</label>
  <input type="text" id="cfg-nas-dir" value="{_he(nas_dir)}" placeholder="/mnt/nas_cam">
</div>
<button class="btn btn-save" onclick="saveConfig()">設定を保存</button>
<div id="config-result"></div>

<script>
const API_BASE = '/freebox/atomcam2';

async function doCapture() {{
  document.getElementById('result').textContent = '実行中...';
  try {{
    const r = await fetch(API_BASE + '/capture', {{ method: 'POST' }});
    const j = await r.json();
    const statusMap = {{
      ok:       '✅ キャプチャ成功',
      skip_nas: '⚠️ NAS 未接続のためスキップ',
      skip_ip:  '⚠️ カメラ IP を解決できませんでした',
      error:    '❌ キャプチャ失敗',
    }};
    const label = statusMap[j.status] || j.status;
    const path  = j.saved_path ? ' → ' + j.saved_path : '';
    document.getElementById('result').textContent = label + ' / ' + j.message + path;
  }} catch (e) {{
    document.getElementById('result').textContent = 'エラー: ' + e;
  }}
}}

async function saveConfig() {{
  const el = id => document.getElementById(id).value;
  const payload = {{
    mac:       el('cfg-mac'),
    rtsp_user: el('cfg-rtsp-user'),
    rtsp_pass: el('cfg-rtsp-pass'),
    rtsp_port: el('cfg-rtsp-port'),
    nas_dir:   el('cfg-nas-dir'),
  }};
  document.getElementById('config-result').textContent = '保存中...';
  try {{
    const r = await fetch(API_BASE + '/config', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(payload),
    }});
    const j = await r.json();
    if (r.ok) {{
      document.getElementById('config-result').textContent = '✅ ' + (j.message || '保存しました。');
    }} else {{
      document.getElementById('config-result').textContent = '❌ エラー: ' + (j.message || r.status);
    }}
  }} catch (e) {{
    document.getElementById('config-result').textContent = 'エラー: ' + e;
  }}
}}
</script>
</body>
</html>
"""
