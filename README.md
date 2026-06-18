# JusticePDF

**JusticePDF** は、PDF ファイルを「カード」として並べ、ドラッグ＆ドロップで直感的に **結合 / 並べ替え / ページ抽出 / 回転** などを行うデスクトップアプリです。

- GUI: **PyQt6**
- PDF 処理: **PyMuPDF (fitz)**
- 監視: **watchdog**（作業フォルダ内の PDF の追加/削除/更新を自動反映）
- Undo/Redo: **最大 100 ステップ**（`UndoManager(max_size=100)`）

> 備考: ソース内には過去の名称（PDFas / JustinPDF）に由来する文字列が一部残っていますが、アプリ表示名は **JusticePDF** に統一されています（`src/main.py`）。

---

## 画面構成

- **メイン画面**: PDF をカード表示（サムネイル、ページ数、ファイル名）。
- **個別画面（ページ編集）**: 1 PDF 内のページサムネイルを一覧表示し、並べ替え・削除・回転・挿入・抽出を行う。
- **拡大表示（ズームビュー）**: ページサムネイルをダブルクリックすると、そのページを通常サイズで表示し、スライダー/ボタン/ホイールでズーム可能。

---

## 作業フォルダ

アプリは起動時に作業フォルダを作成/利用します。

- 既定: `~/Documents/PDFs`（OS のホーム配下）
- フォルダ内の PDF を監視しており、追加/削除/更新がメイン画面に自動反映されます。
- ツールバーの「設定」ボタンから保存先を変更できます。相対パスはホームフォルダ基準で解決し（例「PDFs」→ `~/PDFs`）、存在しないフォルダは自動作成します。変更は開いているメイン画面に即時反映されます（既存ファイルは移動しません）。

> 既定パスは `src/utils/app_settings.py` の `default_pdfs_dir()` で定義され、設定値は QSettings に保存されます。

---

## 設定

ツールバーの「設定」ボタンから次の項目を変更できます。

- **PDFsフォルダの場所**: 上記「作業フォルダ」の保存先。
- **重ねたときのしおり**: ドラッグ＆ドロップでファイルを重ねた（マージした）ときに、ファイル名でしおりを作るかどうか。オンにすると重ねた各ファイルの先頭にファイル名のしおりを付け、そのファイルが元々持つしおりはその下にぶら下げます。**既定はオフ（しおりを作らない）** です。

---

## 基本操作

### 選択

- クリック: 単一選択
- **Ctrl + クリック**: 追加/解除（複数選択）
- **Shift + クリック**: 範囲選択
- 何もない場所をドラッグ: **ラバーバンド選択**（矩形選択）

### キーボードショートカット

- **Undo**: `Ctrl + Z`
- **Redo**: `Ctrl + Y`
- **削除**: `Delete`
- **名前変更**: `F2`（メイン画面で 1 件選択時）
- **全選択**: `Ctrl + A`
- **注釈オブジェクトの移動**: ズームビューで注釈を選択中、`↑ / ↓ / ← / →`（矢印キー）で移動。`Alt`（または `Shift`）を押しながらだと細かく移動。

---

## ドラッグ＆ドロップ仕様

### 1) メイン画面内（カード同士）

| 操作 | 結果 | 補足 |
|---|---|---|
| カードをカード間へドロップ | **カードの並べ替え（Move）** | 手動順（`manual`）に切り替え、Undo 対応 |
| **Ctrl** を押しながらカードをカード間へドロップ | **ファイル複製（Copy）**して挿入 | `*_copy_N.pdf` を作成し、Undo 対応 |
| カードを別カードの中央付近へドロップ | **結合（Move Merge）** | 対象 PDF を上書きし、元 PDF はゴミ箱へ（`send2trash`） |
| **Ctrl** を押しながらカードを別カードの中央付近へドロップ | **結合（Copy Merge）** | 元 PDF は残しつつ、対象 PDF を上書き（Undo 対応） |

中央付近へのドロップは、ドラッグ中に対象カードが **緑** にハイライトされます。

### 2) 個別画面 → メイン画面（ページ抽出）

個別画面のページ（複数選択可）を、メイン画面へドロップできます。

| ドロップ先 | 結果 | Ctrl 押下 |
|---|---|---|
| カード上 | その PDF の **先頭にページを挿入** | **Copy**: 元 PDF は保持 / **Move**: 元 PDF からページ削除 |
| カード間（または空き領域） | 抽出ページで **新しい PDF を作成**し、そこに保存してカード追加 | Copy/Move の扱いは同上 |

- 新規作成のファイル名: `*_pages_N.pdf`（作業フォルダへ保存）
- Move で「元 PDF の全ページが無くなる」場合は、元 PDF はゴミ箱へ移動され、個別画面は閉じます。

### 3) メイン画面 → 個別画面（PDF 丸ごと挿入）

メイン画面のカードを個別画面へドロップすると、その PDF の **全ページを挿入**できます。

- ドロップ位置に応じて挿入位置が決まります（ページサムネイルの間にインジケータが表示されます）。

### 4) 個別画面内（ページの並べ替え/挿入）

| 操作 | 結果 |
|---|---|
| ページをページ間へドロップ | 同一 PDF 内でページ並べ替え（Undo 対応） |
| 別 PDF 由来のページをドロップ | 指定位置へページ挿入（Undo 対応） |

---

## 起動方法

### uv（推奨）

```bash
# 仮想環境
uv venv .venv --python 3.14

# 依存関係インストール
uv sync

# 起動
uv run python -m src.main

# ログを詳しく
uv run python -m src.main --log-level DEBUG
```

### pip / venv

```bash
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (cmd.exe)
.venv\Scripts\activate.bat
# macOS / Linux
# source .venv/bin/activate

pip install -U pip
pip install -e .

python -m src.main
```

---

## 既定のアプリとして設定する（Windows）

PDF などをダブルクリックしたときに JusticePDF で開く「**既定のアプリ（通常使うアプリ）**」
として登録できます。**管理者権限は不要（HKCU のみ）**で、Python 同梱のポータブルビルド
（`python\pythonw.exe` 同梱）にも対応します。

```powershell
# （任意・推奨）専用ランチャー JusticePDF.exe をビルド
#   一覧での表示名が「Python」ではなく「JusticePDF」になり、アイコンも付きます。
#   Windows 同梱の .NET の C# コンパイラを使うため、追加インストール・管理者権限は不要。
powershell -ExecutionPolicy Bypass -File tools\build_launcher_exe.ps1

# JusticePDF を「プログラムから開く」候補として登録
#   JusticePDF.exe があれば自動で利用し、無ければ pythonw.exe + ランチャーで代替。
powershell -ExecutionPolicy Bypass -File tools\set_default_app.ps1

# 解除
powershell -ExecutionPolicy Bypass -File tools\unset_default_app.ps1
```

> `JusticePDF.exe` は実行時にアプリ直下の `.venv` / 同梱 `python\` から `pythonw.exe` を
> 探して起動するため、Python 同梱のポータブル配布でもそのまま動きます（ビルド成果物の
> ため Git 管理外）。アイコンを付けたい場合は `-Icon app.ico` を両スクリプトに渡すか、
> `tools\justicepdf.ico` を置けば自動採用されます。

登録後、**実際に既定へ確定する操作はユーザー自身が行う必要があります**（Windows 10/11 は
拡張子の既定ハンドラ `UserChoice` をハッシュで保護しており、スクリプトから無言で強制設定
できないため）。

1. PDF を右クリック →「プログラムから開く」→「別のプログラムを選択」
2. **JusticePDF** を選ぶ
3. 「常にこのアプリを使って .pdf ファイルを開く」にチェック →「OK」
4. 拡張子ごとに繰り返すか、設定 →「既定のアプリ」からまとめて設定

> 対象拡張子は `src/utils/constants.py` の `IMPORT_EXTS`（PDF / Office / 画像）を参照します。
> 右クリックの「JusticePDFで開く」メニューだけ追加したい場合は
> `tools\install_context_menu.ps1` を使ってください（こちらは関連付けを変更しません）。

---

## プロジェクト構成

```
.
├─ pyproject.toml
├─ uv.lock
└─ src/
   ├─ main.py                  # エントリポイント
   ├─ controllers/
   │  └─ folder_watcher.py      # 作業フォルダ監視
   ├─ models/
   │  └─ undo_manager.py        # Undo/Redo（最大100）
   ├─ utils/
   │  └─ pdf_utils.py           # PyMuPDF を用いた PDF 操作
   └─ views/
      ├─ main_window.py         # メイン画面（カード一覧）
      ├─ page_edit_window.py    # 個別画面（ページ編集/ズーム）
      └─ pdf_card.py            # カード UI
```

---

## 開発メモ

### ログ

起動時に `--log-level` を指定できます。

- `INFO`（デフォルト）
- `DEBUG` / `WARNING` / `ERROR`

### ロック（編集中カードの保護）

メイン画面で PDF をダブルクリックして個別画面を開くと、そのカードは **ロック状態**（グレーアウト）になり、
ドラッグ元/結合先として選べないように制限されます。

---

## ライセンス

このリポジトリには現時点で LICENSE ファイルが含まれていません。
公開・配布方針に応じて、適切なライセンスを追加してください。

---

## Contributing

改善提案・バグ報告・PR 歓迎です。

- D&D 仕様や Undo/Redo の期待挙動がある場合は、再現手順（操作の順番）を添えてください。
- UI の改善は、変更前/変更後のスクリーンショットがあるとレビューが進みやすいです。
