# freeBox 実装仕様（hsBox1.3 向け）

このドキュメントでは、  
freeBox を hsBox1.3 の環境上で *実際にどのように構成・動作させるか* を整理した仕様を示します。

本仕様は 2026年1月時点での考え方・構成案をまとめたものであり、  
今後の検討により変更される可能性があります。:contentReference[oaicite:1]{index=1}

---

## 1. freeBox の構成要素

freeBox は、以下の 3 つの構成要素で成り立つことを想定しています。:contentReference[oaicite:2]{index=2}

---

### 1-1. freeBox Module（フリーボックスモジュール）

**概要**
- GitHub から取得する最小単位の機能コンポーネントです。
- 利用者が追加・削除・選択できる単位として設計されています。:contentReference[oaicite:3]{index=3}

**特徴**
- 各機能は “モジュール単位” で提供される
- 必要な機能だけを組み合わせて利用可能
- モジュール名の表記は  
  `fbx-module-*` の形式を想定しています（例: `fbx-module-weather`）:contentReference[oaicite:4]{index=4}

---

### 1-2. freeBox Loader（フリーボックスローダー）

**概要**
- freeBox Module を *hsBox 上で扱うための選択・管理機構* です。:contentReference[oaicite:5]{index=5}

**主な役割**
1. freeBox Module の一覧化  
2. モジュールごとの有効化／無効化  
3. 依存関係や説明の提示  

**動作イメージ**
- hsBox の管理画面や CLI 上で  
  Module の一覧を確認できる
- 利用者がモジュールを選択し、有効化・無効化を行う

この Loader は、  
モジュールを「使える形」にする **仕組みとして非常に重要** であり、  
freeBox の中核的なコンポーネントと位置づけられます。:contentReference[oaicite:6]{index=6}

---

### 1-3. freeBox Base Module（ベースモジュール）

**概要**
- freeBox 基盤となるモジュールです。
- .hbx 形式で提供されるモジュールとして設計されています。:contentReference[oaicite:7]{index=7}

**想定用途**
- freeBox 全体をまとめる母体  
- 必要な初期設定や Loader を含む  
- freeBox を *hsBox 上で動作させる基本セット* として配布

**形式**
- モジュールファイル名: `freebox-base.hbx`
- 識別 ID: `freebox.base`:contentReference[oaicite:8]{index=8}

これは、hsBox のアップデートやパッチ適用の仕組みと同様に  
**hsBox に導入することで初めて freeBox が動作する**構造になっています。:contentReference[oaicite:9]{index=9}

---

## 2. 実装と運用のポイント

### 2-1. hsBox1.3 オプションパッチとの関連

hsBox1.3 ではオプションパッチ機能が仕様として存在します。  
freeBox の仕組みもこの形式に倣い、

- モジュール単位で拡張できる
- 既存のアップデートの仕組みに組み込む

という形で設計されています。:contentReference[oaicite:10]{index=10}

これは、  
既存の hsBox アップデートと同じ管理方式を使うことで  
**整合性のある拡張性を確保する**ための設計です。:contentReference[oaicite:11]{index=11}

---

## 3. 名前と内部表記

freeBox の内部構成要素には次のような表記ルールを想定します。:contentReference[oaicite:12]{index=12}

- **freeBox Module** → `fbx-module-*`
- **freeBox Base Module** → ID: `freebox.base`
- **Loader** → 管理機構として専用の名前空間を持つ

👉 この命名規則は、  
モジュール管理や GitHub 連携をスムーズにするための  
内部仕様として意図されています。:contentReference[oaicite:13]{index=13}

---

## 4. 今後の検討事項

本仕様は検討段階のため、以下のような点も今後整理・更新する予定です：

- freeBox Module のディレクトリ構成  
- GitHub 上の公開方法・バージョニング  
- CLI・UI を通じた管理インターフェース  
- 他の hsBox バージョンとの互換性設計

これらは順次 GitHub Pages で公開・更新していきます。
---
[← freeBox トップへ戻る](/freebox/)

