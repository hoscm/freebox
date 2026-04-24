#!/usr/bin/env python3
"""
atomcam2.py - freeBox Module: ATOMCAM2 キャプチャ Plugin

ATOMCAM2のRTSPストリームからffmpegで1フレームをキャプチャし、
NASに定期保存する。10分間隔で実行。
"""

import configparser
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

# [FIX-6] 設計書§4-2-1の規約どおり、ファイル冒頭で一度だけ import する。
# スタンドアロン実行（python atomcam2.py）や開発・テスト時は ImportError で
# フォールバック定義を使用する。handle() 内での多段フォールバックを廃止。
try:
    from box_webserver import Response
except ImportError:
    # スタンドアロン実行時のフォールバック（開発・テスト用）
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
_DEFAULT_NAS_DIR   = "/mnt/nas_cam"
_DEFAULT_RTSP_PORT = "554"

# このPluginファイルが置かれているディレクトリ
_PLUGIN_DIR  = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_PLUGIN_DIR, "atomcam2_config.ini")

# [FIX-7] モジュールスコープの定数としてコンパイル（ループ内での毎回コンパイルを廃止）
_MAC_RE = re.compile(r'(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}')
_IP_RE  = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')


# ---------------------------------------------------------------------------
# 設定値 dataclass
# ---------------------------------------------------------------------------

@dataclass
class _CaptureConfig:
    """キャプチャ処理に必要な設定値を保持する dataclass"""
    mac:       str
    rtsp_user: str
    rtsp_pass: str
    rtsp_port: str
    nas_dir:   str


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
    if os.path.exists(_CONFIG_PATH):
        cfg.read(_CONFIG_PATH, encoding="utf-8")
        logger.info("[atomcam2] 設定ファイル読み込み: %s", _CONFIG_PATH)
    else:
        logger.warning("[atomcam2] 設定ファイルなし。デフォルト値を使用: %s", _CONFIG_PATH)
    return cfg


# ---------------------------------------------------------------------------
# NAS確認
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
# MACアドレスからIPを解決
# ---------------------------------------------------------------------------
def _resolve_ip_from_mac(mac: str) -> "str | None":
    """ARPテーブルを検索してMACアドレスに対応するIPを返す。見つからなければNone。"""
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
                normalized_found = found_mac.replace(":", "").replace("-", "").lower()
                if normalized_found == target_mac:
                    ip_match = _IP_RE.search(line)
                    if ip_match:
                        return ip_match.group(1)
    except FileNotFoundError:
        logger.error("[atomcam2] arp コマンドが見つかりません（net-tools 未インストール）")
    except subprocess.CalledProcessError as e:
        logger.warning("[atomcam2] arp -a 実行エラー (returncode=%d)", e.returncode)
    except subprocess.TimeoutExpired:
        logger.warning("[atomcam2] arp -a タイムアウト")
    except Exception as e:
        logger.warning("[atomcam2] ARP検索エラー: %s", e)
    return None


# ---------------------------------------------------------------------------
# ffmpegでキャプチャ
# ---------------------------------------------------------------------------
def _capture_frame(rtsp_url: str, save_path: str) -> bool:
    """ffmpegでRTSPから1フレームを保存する。成功すればTrue。"""
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
        logger.warning("[atomcam2] ffmpegタイムアウト")
        return False
    except subprocess.CalledProcessError as e:
        logger.warning("[atomcam2] ffmpeg失敗 (returncode=%d)", e.returncode)
        return False
    except FileNotFoundError:
        logger.error("[atomcam2] ffmpegが見つかりません。インストールを確認してください。")
        return False
    except Exception as e:
        logger.warning("[atomcam2] ffmpeg例外: %s", e)
        return False


# ---------------------------------------------------------------------------
# Plugin クラス
# ---------------------------------------------------------------------------
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

    def can_handle(self, path: str) -> bool:
        return (
            path.startswith("/") and
            re.split(r"[^0-9A-Za-z]+", path[1:])[0] == "atomcam2"
        )

    def handle(self, req) -> Response:
        path = req.path.split("?")[0].rstrip("/")

        if req.method == "POST" and path == "/atomcam2/capture":
            self._do_capture()
            body = json.dumps(self._last_capture, ensure_ascii=False).encode("utf-8")
            return Response(200, body, "application/json; charset=utf-8")

        if path == "/atomcam2/status":
            body = json.dumps(self._last_capture, ensure_ascii=False).encode("utf-8")
            return Response(200, body, "application/json; charset=utf-8")

        body = self._render_html().encode("utf-8")
        return Response(200, body, "text/html; charset=utf-8")

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
            logger.warning("[atomcam2] NAS未接続、キャプチャをスキップ: %s", nas_dir)
            self._set_status("skip_nas", f"NAS未接続: {nas_dir}")
            return False
        return True

    def _resolve_ip(self, mac: str) -> "str | None":
        ip = _resolve_ip_from_mac(mac)
        if not ip:
            logger.warning("[atomcam2] カメラIPを解決できません (MAC=%s)", mac)
            self._set_status("skip_ip", f"IP解決失敗 (MAC={mac})")
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
        rtsp_url = f"rtsp://{cfg.rtsp_user}:{cfg.rtsp_pass}@{ip}:{cfg.rtsp_port}/live"
        rtsp_url_for_log = f"rtsp://{cfg.rtsp_user}:***@{ip}:{cfg.rtsp_port}/live"
        logger.info("[atomcam2] キャプチャ開始: %s → %s", rtsp_url_for_log, save_path)
        ok = _capture_frame(rtsp_url, save_path)
        if ok:
            logger.info("[atomcam2] 保存完了: %s", save_path)
            self._set_status("ok", "キャプチャ成功", saved_path=save_path)
        else:
            logger.warning("[atomcam2] キャプチャ失敗: %s", save_path)
            self._set_status("error", "ffmpegキャプチャ失敗")

    def _set_status(self, status: str, message: str, saved_path: "str | None" = None) -> None:
        self._last_capture = {
            "status":     status,
            "timestamp":  datetime.now().isoformat(timespec="seconds"),
            "saved_path": saved_path,
            "message":    message,
        }

    def _render_html(self) -> str:
        lc = self._last_capture
        status_icon = {
            "init":     "⚪",
            "ok":       "🟢",
            "skip_nas": "🟡",
            "skip_ip":  "🟡",
            "error":    "🔴",
        }.get(lc["status"], "⚪")

        saved      = lc.get("saved_path") or "―"
        ts         = lc.get("timestamp")  or "―"
        msg        = lc.get("message")    or "―"
        nas_dir    = self._cfg.get("storage", "nas_dir", fallback=_DEFAULT_NAS_DIR)
        mac        = self._cfg.get("camera",  "mac",     fallback="(未設定)")
        nas_status = "✅ 接続中" if _is_nas_available(nas_dir) else "❌ 未接続"

        return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>ATOMCAM2 Plugin</title>
<style>
  body {{ font-family: sans-serif; padding: 1.5rem; background: #f5f5f5; }}
  h1   {{ font-size: 1.3rem; margin-bottom: 1rem; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 600px; background: #fff; }}
  th, td {{ text-align: left; padding: 0.5rem 0.8rem; border: 1px solid #ddd; }}
  th {{ background: #eee; width: 35%; }}
  .btn {{
    margin-top: 1rem; padding: 0.5rem 1.2rem;
    background: #4a90d9; color: #fff; border: none;
    border-radius: 4px; cursor: pointer; font-size: 0.9rem;
  }}
  .btn:hover {{ background: #357abf; }}
  #result {{ margin-top: 0.8rem; font-size: 0.85rem; color: #333; }}
</style>
</head>
<body>
<h1>📷 ATOMCAM2 Plugin</h1>
<table>
  <tr><th>ステータス</th><td>{status_icon} {lc['status']}</td></tr>
  <tr><th>最終実行</th><td>{ts}</td></tr>
  <tr><th>メッセージ</th><td>{msg}</td></tr>
  <tr><th>保存パス</th><td>{saved}</td></tr>
  <tr><th>NAS ({nas_dir})</th><td>{nas_status}</td></tr>
  <tr><th>カメラMAC</th><td>{mac}</td></tr>
</table>
<button class="btn" onclick="doCapture()">今すぐキャプチャ</button>
<div id="result"></div>
<script>
const API_BASE = '/freebox/atomcam2';
async function doCapture() {{
  document.getElementById('result').textContent = '実行中...';
  try {{
    const r = await fetch(API_BASE + '/capture', {{ method: 'POST' }});
    const j = await r.json();
    document.getElementById('result').textContent =
      j.status + ' / ' + j.message + ' / ' + (j.saved_path || '―');
  }} catch(e) {{
    document.getElementById('result').textContent = 'エラー: ' + e;
  }}
}}
</script>
</body>
</html>
"""
