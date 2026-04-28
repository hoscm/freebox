#!/usr/bin/env python3
"""
make_hbx.py  -  freeBox .hbx パッケージビルドツール
=====================================================
【モード】
  --type module（デフォルト）: サードパーティ Plugin モジュールのビルド
  --type loader             : freeBox Loader 本体のビルド

【使い方】

  # Module ビルド（サードパーティ Plugin）
  python tools/make_hbx.py <module_id> <plugin_file> <version> <output_dir>
  python tools/make_hbx.py atomcam2 ./atomcam2.py 1.0.0 ./dist/

  # Loader ビルド（freeBox Loader 本体）
  python tools/make_hbx.py --type loader [src_dir] [output.hbx]
  python tools/make_hbx.py --type loader                              # デフォルト
  python tools/make_hbx.py --type loader freebox/loader freebox-base.hbx

【引数（module モード）】
  module_id    モジュール ID（英小文字・数字・ハイフン）
  plugin_file  Plugin 実装ファイルのパス（.py）
  version      バージョン番号（例: 1.0.0）
  output_dir   出力先ディレクトリ

【引数（loader モード）】
  src_dir      Loader ソースディレクトリ（デフォルト: freebox/loader/）
  output.hbx   出力ファイル（デフォルト: freebox-base.hbx）

【出力】
  <output_dir>/<module_id>.hbx  (module モード)
  freebox-base.hbx              (loader モード デフォルト)

詳細は docs/hbx_build_tool_guide.md を参照してください。

---
【version.txt 仕様について】（G-26 正式移行）

  module モード（2行形式）:
    行1: module_id
    行2: version

  loader モード（正規5行形式・G-26 確定）:
    行1: pdname  = モジュール名（hsBox がアクセスするコンテキスト名）
    行2: obb     = 適用可能な最低ベースビルド番号
    行3: obv     = 適用可能な最古の hsBox フルバージョン
    行4: nwv     = 適用可能な最新の hsBox フルバージョン
    行5: thisv   = このパッチ自体のバージョン（semver 形式）

  詳細は freebox-loader-skill.md §6-10 および freebox/docs/specification.md §7 参照。
"""

import argparse
import fnmatch
import os
import re
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 共通定数
# ---------------------------------------------------------------------------

# モジュール ID の正規表現（box_webserver.py 内の VALID_PLUGIN_NAME と同一）
_VALID_MODULE_ID = re.compile(r'^[a-z0-9][a-z0-9\-]*[a-z0-9]$')
_VERSION_RE      = re.compile(r'^\d+\.\d+')

# ---------------------------------------------------------------------------
# デフォルト設定（loader モード）
# ---------------------------------------------------------------------------
_SCRIPT_DIR  = Path(__file__).resolve().parent          # freebox/tools/
_FREEBOX_DIR = _SCRIPT_DIR.parent                       # freebox/
_REPO_ROOT   = _FREEBOX_DIR.parent                     # openBox/

DEFAULT_LOADER_SRC    = _FREEBOX_DIR / "loader"
DEFAULT_LOADER_OUTPUT = _REPO_ROOT / "freebox-base.hbx"

# ---------------------------------------------------------------------------
# loader モード: 必須ファイル（freebox/loader/ からの相対パス）
# ---------------------------------------------------------------------------
# ⚠️ 実際の freebox/loader/ 構造（G-25 確認）に合わせて定義する。
# build_hbx.py（旧ツール）の REQUIRED_FILES とは異なる点に注意。
# 差異の詳細は freebox-loader-skill.md §6-10 参照。
LOADER_REQUIRED_FILES = [
    "version.txt",
    "run.sh",
    "conf/freebox.conf",
    "conf/freebox.service",
    "conf/freebox_config.ini.template",
    "server/box_webserver.py",
    "server/merge_config.py",
    "www/index.php",     # run.sh が /home/hsbox/www/freebox/index.php へコピーする
]

# ---------------------------------------------------------------------------
# loader モード: 除外設定
# ---------------------------------------------------------------------------
LOADER_EXCLUDE_PATTERNS = {
    ".git", ".gitignore", "__pycache__", ".DS_Store", "*.pyc", "*.pyo", "*.hbx",
}
LOADER_EXCLUDE_DIRS = {
    "server/data",      # 実行時キャッシュ
    "server/plugins",   # 実行時プラグイン
    "server/releases",  # 旧FTモック用（削除済みだが念のため除外）
}
LOADER_EXCLUDE_FILES = {
    "server/make_test_hbx.py",  # 開発用スクリプト（存在する場合）
}

# run.sh に実行権限を付与
_EXEC_FILES  = {"run.sh"}
_PERM_EXEC   = 0o755 << 16
_PERM_NORMAL = 0o644 << 16


# ===========================================================================
# 共通ユーティリティ
# ===========================================================================

def _abort(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _log(msg: str) -> None:
    print(msg)


# ===========================================================================
# module モード
# ===========================================================================

def _validate_module_id(module_id: str) -> None:
    if not _VALID_MODULE_ID.match(module_id):
        _abort(
            f"Invalid module_id '{module_id}'.\n"
            f"  Must match: ^[a-z0-9][a-z0-9\\-]*[a-z0-9]$\n"
            f"  Example: mymodule, my-module, cam2"
        )


def _validate_version(version: str) -> None:
    if not version or not re.match(r'^\d+\.\d+\.\d+', version):
        _abort(
            f"Invalid version '{version}'.\n"
            f"  Must be semver format: X.Y.Z (e.g. 1.0.0)"
        )


def build_module_hbx(module_id: str, plugin_file: Path, version: str, output_dir: Path) -> Path:
    """
    module モード: .hbx パッケージを生成する。

    .hbx の内部構造:
        <module_id>.hbx (ZIP)
          ├── <module_id>.py   ← Plugin 実装ファイル
          └── version.txt      ← 2行形式（module_id / version）
    """
    _validate_module_id(module_id)
    _validate_version(version)

    if not plugin_file.exists():
        _abort(f"Plugin file not found: {plugin_file}")
    if plugin_file.suffix.lower() != '.py':
        _abort(f"Plugin file must be a .py file: {plugin_file}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{module_id}.hbx"

    version_txt = f"{module_id}\n{version}\n"

    with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(plugin_file, arcname=f"{module_id}.py")
        zf.writestr("version.txt", version_txt)

    _log(f"Built: {output_path}")
    _log(f"  module_id : {module_id}")
    _log(f"  version   : {version}")
    _log(f"  contents  : {module_id}.py, version.txt")
    return output_path


# ===========================================================================
# loader モード
# ===========================================================================

def _is_loader_excluded(rel: Path) -> bool:
    rel_posix = rel.as_posix()
    if rel_posix in LOADER_EXCLUDE_FILES:
        return True
    for ex_dir in LOADER_EXCLUDE_DIRS:
        if rel_posix == ex_dir or rel_posix.startswith(ex_dir + "/"):
            return True
    for part in rel.parts:
        if part in LOADER_EXCLUDE_PATTERNS:
            return True
    for pattern in LOADER_EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(rel.name, pattern):
            return True
    return False


def _collect_loader_files(src: Path) -> list:
    return sorted(
        p for p in src.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and not _is_loader_excluded(p.relative_to(src))
    )


def _validate_loader_src(src: Path) -> None:
    if not src.exists() or not src.is_dir():
        _abort(f"Loader src directory not found: {src}")

    missing = [r for r in LOADER_REQUIRED_FILES if not (src / r).exists()]
    if missing:
        _abort("必須ファイルが不足しています:\n" + "\n".join(f"  - {f}" for f in missing))
    _log("[OK] 必須ファイルの検証: すべて存在します")


def _validate_loader_version_txt(src: Path) -> str:
    """
    version.txt を読み込んでバージョン情報を検証し、thisv を返す。

    正規5行形式（G-26 確定）:
      行1: pdname  = モジュール名（英字）
      行2: obb     = 最低ベースビルド番号（数字）
      行3: obv     = 最古の hsBox バージョン（X.XX.XX.XX 形式）
      行4: nwv     = 最新の hsBox バージョン（X.XX.XX.XX 形式）
      行5: thisv   = このパッチのバージョン（semver 形式）

    詳細は freebox-loader-skill.md §6-10 参照。
    """
    path = src / "version.txt"
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    if len(lines) < 5:
        _abort(
            f"version.txt は正規5行形式が必要です（現在 {len(lines)} 行）。\n"
            f"  期待する形式:\n"
            f"    行1: pdname  (例: freebox)\n"
            f"    行2: obb     (例: 269)\n"
            f"    行3: obv     (例: 1.03.01.01)\n"
            f"    行4: nwv     (例: 1.03.02.99)\n"
            f"    行5: thisv   (例: 1.0.0)\n"
            f"  詳細は freebox-loader-skill.md §6-10 参照。"
        )

    pdname = lines[0]
    obb    = lines[1]
    obv    = lines[2]
    nwv    = lines[3]
    thisv  = lines[4]

    # pdname: 英字・数字のみ
    if not re.match(r'^[a-z][a-z0-9]*$', pdname):
        _abort(f"version.txt 行1（pdname）の形式が不正です: {pdname!r}\n  期待形式: 英小文字・数字（例: freebox）")

    # obb: 数字のみ
    if not re.match(r'^\d+$', obb):
        _abort(f"version.txt 行2（obb）の形式が不正です: {obb!r}\n  期待形式: 数字（例: 269）")

    # obv / nwv: X.XX.XX.XX 形式
    ver_pat = re.compile(r'^\d+\.\d{2}\.\d{2}\.\d{2}$')
    if not ver_pat.match(obv):
        _abort(f"version.txt 行3（obv）の形式が不正です: {obv!r}\n  期待形式: X.XX.XX.XX（例: 1.03.01.01）")
    if not ver_pat.match(nwv):
        _abort(f"version.txt 行4（nwv）の形式が不正です: {nwv!r}\n  期待形式: X.XX.XX.XX（例: 1.03.02.99）")

    # thisv: semver
    if not _VERSION_RE.match(thisv):
        _abort(f"version.txt 行5（thisv）の形式が不正です: {thisv!r}\n  期待形式: X.Y[.Z]（例: 1.0.0）")

    _log(f"[OK] version.txt の検証: pdname={pdname}, obb={obb}, obv={obv}, nwv={nwv}, thisv={thisv}")
    return thisv


def build_loader_hbx(src: Path, output: Path) -> None:
    """
    loader モード: freeBox Loader 本体の .hbx をビルドする。

    freebox/loader/ の実際の構造（G-25 確認済み）を前提とする。
    ZIP 内のパスは src からの相対パスをそのまま使用（フラット化しない）。
    run.sh には実行権限（0o755）を付与する。
    """
    _log("=" * 60)
    _log("freeBox Loader .hbx ビルド  [make_hbx.py --type loader]")
    _log("=" * 60)
    _log(f"入力ディレクトリ : {src}")
    _log(f"出力ファイル     : {output}")
    _log("")

    _log("--- 検証開始 ---")
    _validate_loader_src(src)
    thisv = _validate_loader_version_txt(src)
    _log("--- 検証完了 ---\n")

    files = _collect_loader_files(src)
    arcnames = []

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            arcname = str(f.relative_to(src)).replace("\\", "/")
            info = zipfile.ZipInfo(arcname)
            info.compress_type = zipfile.ZIP_DEFLATED
            fname = arcname.split("/")[-1]
            info.external_attr = _PERM_EXEC if fname in _EXEC_FILES else _PERM_NORMAL
            with open(f, "rb") as fp:
                zf.writestr(info, fp.read())
            arcnames.append(arcname)

    size_kb = output.stat().st_size / 1024
    _log("\n" + "=" * 60)
    _log(f"[完了] {output.name}  ({size_kb:.1f} KB)")
    _log("=" * 60)
    _log(f"  thisv   : {thisv}")
    _log(f"\n収録ファイル ({len(arcnames)} 件):")
    for name in arcnames:
        perm = "rwxr-xr-x" if name.split("/")[-1] in _EXEC_FILES else "rw-r--r--"
        _log(f"  {perm}  {name}")
    _log("")
    _log("=" * 60)


# ===========================================================================
# エントリーポイント
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="freeBox .hbx パッケージビルドツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例（module モード）:\n"
            "  python tools/make_hbx.py atomcam2 ./atomcam2.py 1.0.0 ./dist/\n\n"
            "例（loader モード）:\n"
            "  python tools/make_hbx.py --type loader\n"
            "  python tools/make_hbx.py --type loader freebox/loader freebox-base.hbx\n\n"
            "詳細は docs/hbx_build_tool_guide.md を参照してください。"
        ),
    )
    parser.add_argument(
        "--type",
        choices=["module", "loader"],
        default="module",
        dest="build_type",
        help="ビルドタイプ: module（デフォルト）または loader",
    )
    parser.add_argument("args", nargs="*", help="位置引数（モードによって異なる）")

    opts = parser.parse_args()

    if opts.build_type == "loader":
        # loader モード: [src_dir] [output.hbx]
        pos = opts.args
        if len(pos) == 0:
            src    = DEFAULT_LOADER_SRC
            output = DEFAULT_LOADER_OUTPUT
        elif len(pos) == 2:
            src    = Path(pos[0]).resolve()
            output = Path(pos[1])
            if not output.is_absolute():
                output = Path.cwd() / output
        else:
            parser.error("loader モードの引数は 0 個または 2 個（src_dir output.hbx）です")
        build_loader_hbx(src, output)

    else:
        # module モード: module_id plugin_file version output_dir
        pos = opts.args
        if len(pos) != 4:
            parser.error("module モードの引数は 4 個です: module_id plugin_file version output_dir")
        build_module_hbx(
            module_id=pos[0],
            plugin_file=Path(pos[1]),
            version=pos[2],
            output_dir=Path(pos[3]),
        )


if __name__ == "__main__":
    main()
