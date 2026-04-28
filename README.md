# freebox
freeBox - Open Source version of hsBox

freeBox (v1.3 Beta) — The Agile Platform for Your Ideas  
freeBox は、USB起動のLiveモードで動作する、軽量かつ堅牢な「スマートデバイス・プラットフォーム」です。 hsBoxの設計思想を継承し、スマートホーム、スモールオフィス、エッジコンピューティングなど、あらゆるシーンで利用者自身が自由に拡張、強化することができる「基盤」となることを目指しています。

---

## 🔌 freeBox Loader

freeBox Loader は hsBox 上で動作する Plugin 管理サーバーです。  
Web UI（Manager）から Plugin モジュールを取得・インストール・管理できます。

### クイックスタート

1. [Releases](https://github.com/hoscm/freebox/releases/latest) から `freebox-base.hbx` をダウンロード
2. hsBox にアップロードして `bash run.sh` でインストール
3. ブラウザで `http://<hsBox のIP>/freebox/manager/` を開く

詳細は **[Getting Started](docs/getting_started.md)** を参照してください。

### ドキュメント

| ドキュメント | 内容 |
|------------|------|
| [docs/getting_started.md](docs/getting_started.md) | インストール手順・基本操作 |
| [docs/specification.md](docs/specification.md) | アーキテクチャ・仕様概要 |
| [docs/module_dev_guide.md](docs/module_dev_guide.md) | Plugin モジュールの開発方法 |
| [docs/plugin_dev_guide.md](docs/plugin_dev_guide.md) | Plugin クラスの実装リファレンス |
| [docs/run_sh_guide.md](docs/run_sh_guide.md) | インストールスクリプト（run.sh）の実装方法 |
| [docs/hbx_build_tool_guide.md](docs/hbx_build_tool_guide.md) | .hbx パッケージのビルド方法 |
| [docs/loader_maintenance_guide.md](docs/loader_maintenance_guide.md) | バージョン管理・リリース運用 |

---

## 🌟 プロジェクトの狙い：自由な拡張性

freeBoxは単なるOSではありません。誰もが自分のアイデアを形にし、共有できるプラットフォームです。　現在は主にPythonベースのコアシステムの構築と、GUIからの拡張機能実装に注力しています。

**For Power Users (Advanced):** 堅牢なLive環境をベースに、独自のスクリプトやサービスを自由に追加・拡張できます。

**For Every Users (Beginner):** GitHub上で公開される様々な「機能モジュール」を選択するだけで、専門知識がなくても必要な機能をすぐに手に入れることができます。

---

## 🏗 プラットフォームとしての特徴

**Immutable Base:** Liveモードによるクリーンな動作。何度再起動しても、常に最適な状態からスタートします。

**Modular Design:** croncmd.txt を介したタスク管理など、機能の追加・変更が容易な設計を採用しています。

**Eco-System:** 将来的に、世界中の開発者が作った「freeBox用モジュール」を組み合わせて、あなただけの専用ボックスを作れる世界を目指します。

---

## ⚠️ 知っておいていただきたいこと

freeBoxは、毎回クリーンな状態で起動する「Liveモード」を採用しています。  
そのため、一般的なパソコンの設定変更とは異なり、専用の手順以外で行った変更は保存されません。これはシステムを常に安全・清潔に保つための仕組みです。

詳しいカスタマイズ方法については、情報共有ページに記載する予定です。

---

## 📜 ライセンス

GNU GPL v3 — このプラットフォームの自由を、すべてのユーザーに。
