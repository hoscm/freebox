# freeBox Loader — Getting Started

**対象読者:** freeBox Loader を hsBox に初めてインストールするユーザー  
**ドキュメントバージョン:** v1.0.0  

---

## 1. 概要

freeBox Loader は hsBox 上で動作する Plugin 管理サーバーです。
Web UI（Manager）から Plugin モジュールを取得・インストール・管理できます。

インストール後は以下の URL で Manager UI にアクセスできます：

```
http://<hsBox のIPアドレス>/freebox/manager/
```

---

## 2. 前提条件

| 項目 | 要件 |
|------|------|
| hsBox バージョン | 1.3.1.1 以上 |
| インターネット接続 | 必要（GitHub からモジュールを取得するため） |
| 操作端末 | hsBox に SSH できる環境 |

---

## 3. インストール手順

### 3-1. freebox-base.hbx を入手する

[GitHub Releases](https://github.com/hoscm/freebox/releases/latest) から `freebox-base.hbx` をダウンロードします。

### 3-2. hsBox にアップロードする

ダウンロードした `freebox-base.hbx` を hsBox の任意のディレクトリにコピーします。

```bash
scp freebox-base.hbx root@<hsBox のIPアドレス>:/tmp/
```

### 3-3. hsBox にインストールする

hsBox に SSH してインストールを実行します。

```bash
ssh root@<hsBox のIPアドレス>
cd /tmp
# freebox-base.hbx を ztmp に展開して run.sh を実行します
mkdir -p /home/hsbox/ztmp
cp freebox-base.hbx /home/hsbox/ztmp/
cd /home/hsbox/ztmp
unzip -o freebox-base.hbx
bash run.sh
```

インストールが正常に完了すると以下のメッセージが表示されます：

```
[freeBox Loader] インストール完了
[freeBox Loader] Manager UI: http://<hsBox の IP>/freebox/manager/
```

### 3-4. インストールを確認する

ブラウザで Manager UI を開きます：

```
http://<hsBox のIPアドレス>/freebox/manager/
```

StatusBar に **running** と表示されていれば正常に起動しています。

---

## 4. Plugin モジュールをインストールする

### 4-1. Manager UI を開く

```
http://<hsBox のIPアドレス>/freebox/manager/
```

### 4-2. インデックスを更新する

画面上部の **[Refresh Index]** ボタンをクリックしてモジュール一覧を取得します。

インターネット接続が正常であれば、利用可能な Plugin モジュールがカード形式で表示されます。

### 4-3. モジュールをインストールする

インストールしたいモジュールの **[Deploy]** ボタンをクリックします。

| モジュールの種類 | Deploy ボタンの動作 |
|---------------|------------------|
| `public` | すぐにインストール開始 |
| `restricted` | 確認ダイアログが表示される |
| `private` | `.hbx` ファイルのアップロードが必要（§5 参照） |

### 4-4. hsBox を再起動する

インストール完了後は hsBox を再起動してモジュールを有効化します。

Manager UI の **[Restart now]** ボタンを使用するか、hsBox の管理画面から再起動してください。

---

## 5. ローカル .hbx ファイルをインストールする（private モジュール）

`private` モジュールや手動ビルドした `.hbx` をインストールする場合は Upload 機能を使用します。

1. Manager UI の **[Upload]** ボタンをクリックします
2. ファイル選択ダイアログで `.hbx` ファイルを選択します
3. アップロードと展開が完了したら hsBox を再起動します

---

## 6. モジュールを削除する

1. Manager UI でインストール済みモジュールのカードを表示します
2. **[Remove]** ボタンをクリックして削除します
3. 削除後は hsBox を再起動します

---

## 7. 設定を変更する

Manager UI の **[Settings]** タブから Loader の設定を変更できます。

| 設定項目 | 内容 |
|---------|------|
| Index URL | モジュール一覧の取得先 URL |
| NAS Mount Point | NAS のマウントポイント（Plugin が参照する場合） |

設定を保存した後は、一部の項目が有効になるまで hsBox の再起動が必要です。

---

## 8. アンインストール

freeBox Loader をアンインストールする場合は hsBox に SSH して以下を実行します。

```bash
# サービスを停止・無効化する
systemctl stop freebox
systemctl disable freebox

# ファイルを削除する
rm -rf /home/hsbox/freebox
rm -f /etc/systemd/system/freebox.service
rm -f /etc/apache2/conf-enabled/freebox.conf
rm -f /home/hsbox/www/freebox/index.php

# Apache2 を再起動する
systemctl reload apache2
systemctl daemon-reload
```

---

## 9. よくある質問

**Q: Manager UI にアクセスできない**  
A: `http://<IP>/freebox/manager/` の URL を確認してください。`/freebox/` のスラッシュを含めて正確に入力してください。hsBox 上で `systemctl status freebox` を実行してサービスが動作していることを確認してください。

**Q: [Refresh Index] を押してもモジュールが表示されない**  
A: インターネット接続を確認してください。接続が確認できる場合は、StatusBar のエラー表示を確認してください。

**Q: Deploy 後にモジュールが動作しない**  
A: Deploy 後は必ず hsBox を再起動してください（v1 の制限事項）。

**Q: NAS の StatusBar が disconnected になる**  
A: NAS が hsbox ユーザーの書き込み権限でマウントされているか確認してください。マウントオプションに `uid`・`gid`・`file_mode` を適切に設定する必要があります。

---

## 10. 次のステップ

| ドキュメント | 内容 |
|------------|------|
| `docs/specification.md` | freeBox の構成・アーキテクチャの詳細 |
| `docs/module_dev_guide.md` | 独自 Plugin モジュールの開発方法 |
| `docs/hbx_build_tool_guide.md` | `.hbx` パッケージのビルド方法 |

---

*本ドキュメントは freeBox Loader v1.0.0 に基づきます。*
