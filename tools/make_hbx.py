#!/usr/bin/env python3
"""
make_hbx.py  -  freeBox Module .hbx パッケージビルドツール
=============================================================
使い方:
    python tools/make_hbx.py <module_id> <plugin_file> <version> <output_dir>

引数:
    module_id    モジュール ID（英小文字・数字・ハイフン）
    plugin_file  Plugin 実装ファイルのパス（.py）
    version      バージョン番号（例: 1.0.0）
    output_dir   出力先ディレクトリ

出力:
    <output_dir>/<module_id>.hbx

詳細は docs/hbx_build_tool_guide.md を参照してください。
"""

import argparse
import re
import sys
import zipfile
from pathlib import Path

# モジュール ID の正規表現（box_webserver.py 内の VALID_PLUGIN_NAME と同一）
_VALID_MODULE_ID = re.compile(r'^[a-z0-9][a-z0-9\-]*[a-z0-9]$')


def _validate_module_id(module_id: str) -> None:
    """モジュール ID の形式チェック。不正なら SystemExit。"""
    if not _VALID_MODULE_ID.match(module_id):
        print(
            f"Error: Invalid module_id '{module_id}'.\n"
            f"  Must match: ^[a-z0-9][a-z0-9\\-]*[a-z0-9]$\n"
            f"  Example: mymodule, my-module, cam2",
            file=sys.stderr,
        )
        sys.exit(1)


def _validate_version(version: str) -> None:
    """バージョン番号の形式チェック（semver 推奨・空文字禁止）。"""
    if not version or not re.match(r'^\d+\.\d+\.\d+', version):
        print(
            f"Error: Invalid version '{version}'.\n"
            f"  Must be semver format: X.Y.Z (e.g. 1.0.0)",
            file=sys.stderr,
        )
        sys.exit(1)


def build_hbx(module_id: str, plugin_file: Path, version: str, output_dir: Path) -> Path:
    """
    .hbx パッケージを生成する。

    .hbx の内部構造:
        <module_id>.hbx (ZIP)
          ├── <module_id>.py   ← Plugin 実装ファイル
          └── version.txt      ← バージョン情報

    version.txt の形式:
        1行目: module_id
        2行目: version
    """
    _validate_module_id(module_id)
    _validate_version(version)

    if not plugin_file.exists():
        print(f"Error: Plugin file not found: {plugin_file}", file=sys.stderr)
        sys.exit(1)

    if plugin_file.suffix.lower() != '.py':
        print(
            f"Error: Plugin file must be a .py file: {plugin_file}",
            file=sys.stderr,
        )
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{module_id}.hbx"

    version_txt_content = f"{module_id}\n{version}\n"

    with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        # Plugin ファイルを <module_id>.py として格納
        zf.write(plugin_file, arcname=f"{module_id}.py")
        # version.txt を格納
        zf.writestr("version.txt", version_txt_content)

    print(f"Built: {output_path}")
    print(f"  module_id : {module_id}")
    print(f"  version   : {version}")
    print(f"  contents  : {module_id}.py, version.txt")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="freeBox Module .hbx パッケージビルドツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  python tools/make_hbx.py mymodule ./mymodule.py 1.0.0 ./dist/\n\n"
            "詳細は docs/hbx_build_tool_guide.md を参照してください。"
        ),
    )
    parser.add_argument("module_id",   help="モジュール ID（例: mymodule, my-module）")
    parser.add_argument("plugin_file", help="Plugin 実装 .py ファイルのパス")
    parser.add_argument("version",     help="バージョン番号（例: 1.0.0）")
    parser.add_argument("output_dir",  help="出力先ディレクトリ（存在しない場合は作成）")

    args = parser.parse_args()

    build_hbx(
        module_id=args.module_id,
        plugin_file=Path(args.plugin_file),
        version=args.version,
        output_dir=Path(args.output_dir),
    )


if __name__ == "__main__":
    main()
