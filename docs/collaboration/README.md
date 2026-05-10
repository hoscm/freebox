# hoscm コラボレーションプログラム

サードパーティ（個人開発者・パートナー企業）と hoscm の協業形態を体系化した制度文書を、
本ディレクトリで管理しています。

---

## このディレクトリにあるもの

| ファイル | 説明 |
|---|---|
| `hoscm_collaboration_program_rev1.0.pdf` | プログラム仕様書 Rev.1.0（PDF・正本） |
| `hoscm_collaboration_program_rev1.0.md`  | 同 Markdown版（編集元） |
| `CHANGELOG.md` | Rev更新履歴 |

---

## hoscm コラボレーションプログラムとは

freeBox に対するサードパーティとの協業を、**透明かつ予見可能な参加条件**で
提供するための制度です。

初版（Rev.1.0）の対象は **freeBox**。今後、hoscm が扱う他の製品・サービスへ
順次拡張予定です。

### 5つのコラボレーションパターン

| 区分 | 内容 | hoscm の関与 | 費用 |
|---|---|---|---|
| 提供元単独実装 | 公開仕様に基づき、参加者が単独で実装 | なし | 無料 |
| 技術情報提供 | 個別の技術情報を提供し、参加者が実装 | 情報提供 | 有償サポート |
| 技術支援 | 設計・実装支援を提供し、参加者が実装 | 設計・実装支援 | 有償技術支援 |
| 共同開発 | 双方向の機能強化を伴う開発 | 双方向 | 個別見積 |
| 受託開発 | 個別仕様の hoscm による実装 | hoscm が実装 | 業務委託 |

詳細は仕様書 PDF をご参照ください。

---

## 主要な方針

- **オープン仕様と GPLv3**：freeBox プロジェクトのコードは原則 GPLv3
- **NDA は原則不要**：オープン仕様が基本（hsBox 等の非公開情報は例外）
- **参加費は無料**：個別の有償支援は別建て
- **公開ステータスは独立した軸**：`public` / `restricted` / `private`
- **利用者ファースト・運用コスト最小化**：審査範囲はステータスに応じて段階化

---

## 参加方法

参加申請は既設の Web フォームよりお受けします。

- **コラボレーション申請フォーム**：<https://hoscm.com/hsbox/hoscm-collabo/>

---

## ダウンロード

- **PDF（GitHub raw）**：[hoscm_collaboration_program_rev1.0.pdf](./hoscm_collaboration_program_rev1.0.pdf)
- **PDF（jsDelivr CDN）**：<https://cdn.jsdelivr.net/gh/hoscm/freebox@main/docs/collaboration/hoscm_collaboration_program_rev1.0.pdf>

---

## 関連リンク

- freeBox リポジトリ：<https://github.com/hoscm/freebox/>
- freeBox Loader v1.0.2 リリース：<https://github.com/hoscm/freebox/releases/tag/v1.0.2>
- 公開告知記事（hoscm.com）：（公開後に追記）

---

## ライセンスについて

本ドキュメント自体は、freeBox プロジェクトの一部として **GPLv3** の下で配布されます。
本仕様書を参照して実装される参加者プラグインについても、原則として GPLv3 採用を推奨します。
