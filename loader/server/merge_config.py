#!/usr/bin/env python3
"""
merge_config.py - freeBox 設定マージユーティリティ

Usage:
    merge_config.py <既存設定ファイル> <テンプレートファイル>

動作:
    - テンプレートに存在し、既存設定に存在しないセクション・キーのみ追加する
    - 既存キーの値は変更しない
    - 既存にのみ存在するセクション・キーは削除しない
    - 冪等性保証（複数回実行しても結果が変わらない）
"""

import configparser
import os
import sys


def load_ini(path: str, label: str) -> configparser.RawConfigParser:
    """INIファイルを読み込んで返す。失敗時はエラー終了。"""
    cfg = configparser.RawConfigParser()
    cfg.optionxform = str  # キー名の大文字小文字を保持

    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg.read_file(f)
    except FileNotFoundError:
        print(f"[ERROR] {label} が見つかりません: {path}", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"[ERROR] {label} の読み込み権限がありません: {path}", file=sys.stderr)
        sys.exit(1)
    except configparser.Error as e:
        print(f"[ERROR] {label} のINIパースに失敗しました: {e}", file=sys.stderr)
        sys.exit(1)

    return cfg


def merge(existing: configparser.RawConfigParser,
          template: configparser.RawConfigParser) -> int:
    """
    templateの内容をexistingにマージする（新規セクション・キーのみ追加）。
    追加件数を返す。
    """
    added = 0

    for section in template.sections():
        if not existing.has_section(section):
            existing.add_section(section)
            print(f"  [ADD SECTION] [{section}]")

        for key, value in template.items(section):
            if not existing.has_option(section, key):
                existing.set(section, key, value)
                print(f"  [ADD KEY]     [{section}] {key} = {value}")
                added += 1
            # else: 既存キーは変更しない

    return added


def save_ini(cfg: configparser.RawConfigParser, path: str) -> None:
    """
    INIファイルをアトミックに上書き保存する。
    [FIX-軽微] save_ini() 内に try/finally で _cleanup を呼ぶ形に整理。
      tmp ファイルの後始末を save_ini() が一手に担うことで、
      呼び出し元が _cleanup() を知る必要がなくなる（DRY）。
    """
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            cfg.write(f)
        os.replace(tmp_path, path)
    except PermissionError:
        print(f"[ERROR] 設定ファイルへの書き込み権限がありません: {path}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"[ERROR] 設定ファイルの書き込みに失敗しました: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # 例外の有無にかかわらず一時ファイルを削除（os.replace 成功後は存在しないため無害）
        _cleanup(tmp_path)


def _cleanup(path: str) -> None:
    """一時ファイルを削除する（存在しない場合は無視）。"""
    try:
        os.remove(path)
    except OSError:
        pass


def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: merge_config.py <既存設定ファイル> <テンプレートファイル>",
            file=sys.stderr,
        )
        sys.exit(1)

    existing_path = sys.argv[1]
    template_path = sys.argv[2]

    print(f"[merge_config] 既存設定 : {existing_path}")
    print(f"[merge_config] テンプレート: {template_path}")

    existing = load_ini(existing_path, "既存設定ファイル")
    template = load_ini(template_path, "テンプレートファイル")

    added = merge(existing, template)

    if added == 0:
        print("[merge_config] 追加するキーはありませんでした（変更なし）")
        return

    save_ini(existing, existing_path)
    print(f"[merge_config] 完了: {added} 件のキーを追加しました → {existing_path}")


if __name__ == "__main__":
    main()
