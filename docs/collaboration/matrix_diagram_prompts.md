# コラボパターン マトリクス図 画像生成プロンプト

**用途**：WordPress記事「hoscm コラボレーションプログラム公開のお知らせ」内の本文挿入画像
**配置**：「5つのコラボレーションパターン」セクションの下、または「プラグイン公開ステータスは独立した軸」の上
**推奨アスペクト比**：4:3 または 16:9（記事内に収まりやすいワイド形）
**推奨サイズ**：1600×1200px（4:3）または 1600×900px（16:9）

---

## 図の設計（共通理解）

### 軸の定義
- **X軸（横軸）**：hoscm の関与度  
  左 = 低（情報のみ）、右 = 高（hoscm 主体実装）
- **Y軸（縦軸）**：参加者の費用負担  
  下 = 無料、上 = 有償・カスタム見積

### 5パターンの配置

| パターン | X位置 | Y位置 | 費用ラベル |
|---|---|---|---|
| ① 提供元単独実装 | 左下（hoscm関与なし・無料） | 最下 | 無料 |
| ② 技術情報提供 | 左中（情報提供） | 中下 | 有償サポート |
| ③ 技術支援 | 中（設計・実装支援） | 中上 | 有償技術支援 |
| ④ 共同開発 | 右中（双方向開発） | 上 | 個別見積 |
| ⑤ 受託開発 | 右上（hoscm主体実装） | 最上 | 業務委託 |

### 視覚的特徴
- 5つの円またはカードで各パターンを表現
- パターン間に薄い接続線または番号順の動線を入れて「段階的に深まる協業」を示唆
- 軸ラベル、軸の矢印は控えめに
- 「→」矢印は X軸右向き、Y軸上向き

---

## プロンプトA：日本語テキスト込み版（高難度、当たれば一発）

### A-① 日本語版

```
freeBoxコラボレーションプログラムにおける「5つのコラボパターン」を、
2軸マトリクス図として可視化したフォーマルなインフォグラフィック画像。

【マトリクスの軸設計】
- X軸（横軸）：「hoscmの関与度」　左：低 → 右：高、矢印で方向を示す
- Y軸（縦軸）：「参加者の費用負担」　下：無料 → 上：有償・カスタム、矢印で方向を示す
- 軸の交差点に薄いグリッドまたは方眼を入れる（控えめに）

【5つのプロット要素（円または角丸の正方形カード）】
左下から右上へ、ほぼ対角線状に配置：
1. 「提供元単独実装」（左下／無料）── 一番小さい円
2. 「技術情報提供」（左中央／有償サポート）
3. 「技術支援」（中央／有償技術支援）
4. 「共同開発」（右上／個別見積）
5. 「受託開発」（最右上／業務委託）── 一番濃い色

【各プロット要素の表記】
- 番号バッジ（①〜⑤、サークル状）
- パターン名（日本語、太字、明瞭に）
- 費用ラベル（小さなタグ風、各円の下または横）

【視覚的演出】
- 5つの円の間に薄い破線または点線で順序の流れを示唆（直線的でなく、ゆるやかに）
- 円の色は左下が淡く、右上に向かって徐々に濃くなるグラデーション
  （関与度・費用負担の段階を示唆）

【デザイン】
- 配色：ベース＝ウォームオフホワイト（#f5f3ee）、
        円とテキスト＝濃紺（#1a2b4a）からスレートグレーのグラデーション、
        アクセント＝くすんだティール（軸線・矢印用）
- ミニマル、フォーマル、企業ドキュメント風
- 影は最小限、過度な装飾なし
- 日本語フォントは美しいゴシック体（明瞭に）

【出力】
- アスペクト比 4:3 または 16:9
- 解像度 1600px幅以上
- 文字は鮮明に、にじみのないクリアな出力
- 派手なグラデーション、ネオン、3D効果は避ける
```

### A-② 英語版（GPT-Image / Midjourney向け）

```
A formal 2-axis matrix diagram visualizing "5 collaboration patterns" 
of an open-source plugin program, designed as a technical documentation infographic.

Matrix axes:
- X-axis (horizontal): "hoscm involvement" — Low (left) to High (right), with arrow
- Y-axis (vertical): "Participant cost" — Free (bottom) to Custom/Paid (top), with arrow
- Subtle grid lines at the axis intersections

Plot 5 elements (circles or rounded squares), positioned roughly along a diagonal 
from bottom-left to top-right:
1. 「提供元単独実装」(bottom-left, free) — smallest, lightest
2. 「技術情報提供」(left-middle, paid support)
3. 「技術支援」(middle, paid technical support)
4. 「共同開発」(upper-right, custom estimate)
5. 「受託開発」(top-right, contract development) — largest, darkest

Each plot element labeled with:
- A numbered circular badge (1 through 5)
- Pattern name in Japanese (bold, clear)
- Cost label (small pill-shaped tag below or beside)

Visual flow:
- A subtle dashed or dotted line connecting the 5 elements in order, 
  suggesting progression
- Color gradation from light/cool (bottom-left) to deep/warm (top-right)

Design:
- Palette: warm off-white background (#f5f3ee),
  deep navy (#1a2b4a) to slate gray gradient for plots and text,
  muted teal accent for axes and arrows
- Minimal, formal, enterprise documentation aesthetic
- No heavy shadows, no 3D effects, no neon
- CRITICAL: Japanese text must render perfectly — typography precision is essential

Output:
- Aspect ratio 4:3 or 16:9
- Resolution 1600px wide or higher
- Crisp, clean, no blurring on Japanese characters
```

---

## プロンプトB：文字なしの土台デザイン版（推奨・確実）

AI生成では文字部分を空白にしておき、PowerPoint / Canva / Figma 等で日本語テキストを後から重ねる方式。  
**仕上がりが最も確実で、修正も容易。**

### B-① 日本語版

```
2軸マトリクス図のフォーマルな土台デザイン画像。
あとから手動でテキストを重ねるため、文字は一切含めない。

【構成】
- 横軸（X軸）：右向きの細い矢印、軸線
- 縦軸（Y軸）：上向きの細い矢印、軸線
- 軸の根元（左下）が原点、軸ラベル用のスペースを軸の外側に確保
- 軸間に薄いグリッド（4×4または5×5の方眼、ごく薄く）

【5つのプロット要素】
左下から右上へ、対角線状にゆるやかに配置：
- 5つの円（またはサイズの異なる円）、サイズは右上ほど大きい
- 色は左下が淡いオフホワイト寄り、右上に向かって徐々に濃紺へグラデーション
- 各円の中心は文字を入れるための余白として確保される
- 各円の下に小さな角丸の長方形（タグ）を空のまま配置（費用ラベル用）

【視覚的演出】
- 5つの円を結ぶ薄い破線または点線（順序の流れを示唆）
- 線は直線でなく、ゆるやかな曲線
- 円には控えめな影（軽くオフセット）

【デザイン】
- 背景：ウォームオフホワイト（#f5f3ee）
- 円：淡いベージュ → 濃紺（#1a2b4a）への5段階グラデーション
- 軸線・矢印：くすんだティール（控えめ）
- グリッド：極薄のグレー
- 一切のテキスト・記号・数字を含めない（後から重ねるため）
- ミニマル、フォーマル

【出力】
- アスペクト比 4:3
- 解像度 1600×1200px 以上
- PNG（背景透過なし、オフホワイト背景）
- レイアウトに余白を多めに取り、後からテキスト挿入できる空間を確保
```

### B-② 英語版

```
A formal 2-axis matrix diagram template, with NO text whatsoever
(text will be added manually afterward).

Composition:
- X-axis: thin horizontal line with right-pointing arrow at its end
- Y-axis: thin vertical line with upward arrow at its end
- Origin at bottom-left, with margin reserved outside the axes for label placement
- Subtle grid lines (4×4 or 5×5, very faint)

5 plot elements arranged roughly diagonally from bottom-left to top-right:
- 5 circles of gradually increasing size (smaller at bottom-left, larger at top-right)
- Color gradient: from pale off-white tint (bottom-left) deepening to navy (top-right)
- Empty interior of each circle (reserved for text overlay later)
- A small empty rounded-rectangle pill below each circle (for cost label)

Visual flow:
- A subtle dashed/dotted line connecting the 5 circles in order
- Gentle curve, not a straight line
- Soft, minimal drop shadows on circles

Design:
- Background: warm off-white (#f5f3ee)
- Circles: 5-step gradient from pale beige to deep navy (#1a2b4a)
- Axis lines and arrows: muted teal, restrained
- Grid: extremely faint gray
- ABSOLUTELY NO text, numbers, letters, or symbols anywhere
- Minimal, formal, enterprise documentation aesthetic

Output:
- Aspect ratio 4:3
- Resolution 1600×1200px or higher
- PNG (with off-white background, not transparent)
- Generous whitespace to allow text overlay afterward
```

### B-③ 文字を後から重ねる手順（PowerPoint / Canva の例）

1. AI生成した土台画像を PowerPoint / Canva / Keynote にインポート
2. 各円の中心に「テキストボックス」を配置：
   - フォント：游ゴシック Medium / Noto Sans JP Bold
   - 色：白（濃紺の円上）または濃紺（淡い円上）
   - サイズ：18-24pt
3. テキスト内容（左下から右上へ）：
   - ① 提供元単独実装
   - ② 技術情報提供
   - ③ 技術支援
   - ④ 共同開発
   - ⑤ 受託開発
4. 各円の下のタグに費用ラベル：
   - 無料 / 有償サポート / 有償技術支援 / 個別見積 / 業務委託
5. 軸ラベル：
   - X軸：「hoscm の関与度」（右下に「低 → 高」）
   - Y軸：「費用負担」（左上に「↑ 有償」、左下に「無料」）
6. PNG または JPEG で書き出し

---

## どちらのプロンプトを選ぶべきか

| 状況 | 推奨 |
|---|---|
| 失敗してもいい・試行錯誤OK | **A** で何回か試して当たれば速い |
| 確実に綺麗な仕上がりが欲しい | **B** + 後から文字重ね |
| 時間がない・1発で決めたい | **B** + 後から文字重ね |
| 高品質AIで再挑戦できる | **A** の英語版（A-②）が最も成功率高い |

**最終おすすめ**：時間が惜しい場合は **B** で確実に。妥協のない仕上がりを狙うなら **A英語版を ChatGPT/Geminiで4-5枚生成 → 一番文字が綺麗なものを採用**。

---

## SVG 即時生成オプション（追加提案）

私のほうで **SVGコードを直接書いてPNG化する**こともできます：

### メリット
- 文字化け **完全ゼロ**（ベクタフォント直接埋込）
- 修正が **テキスト編集だけ**で可能（パターン名や色を後から微調整できる）
- 画像生成AIのクォータを **一切消費しない**
- 仕上がりがフォーマル文書としてに耐えうる品質

### デメリット
- AI生成画像のような「装飾的な雰囲気」は出にくい
- 純粋に図解として割り切ることが必要

### 仕上がりイメージ
- ベクタ直線・円・テキストで構成された、教科書・論文・コンサル資料風の整った図
- 仕様書PDFに挟んでも違和感ないテイスト

ご希望があれば、このセッション内で完結します（5〜10分）。
