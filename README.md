# PDFas

ビジュアルPDFマージ/スプリットアプリケーション

## 概要

PDFasは、ドラッグ&ドロップ操作でPDFファイルの結合・分割・ページ編集ができるデスクトップアプリケーションです。

## 機能

- PDFカードの並び替え（ドラッグ&ドロップ）
- 複数PDFのマージ（カードをカードにドロップ）
- 外部ファイルのインポート（エクスプローラーからドロップ）
- ページの並び替え（編集ウィンドウ内D&D）
- ページの抽出（ページをメインウィンドウにドロップ）

## 動作環境

- Python 3.10以上
- Windows / macOS / Linux

## インストール

```bash
# uvを使用する場合（推奨）
uv sync

# pipを使用する場合
pip install -e .
```

## 使い方

```bash
python -m src.main
```

## 依存ライブラリ

- PyQt6 >= 6.6.0
- PyMuPDF >= 1.23.0
- watchdog >= 3.0.0
- send2trash >= 1.8.0

## 開発

### DEBUGモード

詳細なデバッグログを有効にするには、環境変数 `PDFAS_DEBUG=1` を設定します。

**Windows:**
```cmd
set PDFAS_DEBUG=1
python -m src.main
```

**Linux/Mac:**
```bash
PDFAS_DEBUG=1 python -m src.main
```

デバッグログには以下の情報が表示されます：
- カードの追加/削除（ファイル名付き）
- ドラッグ&ドロップ操作（ソース/ターゲット位置）
- プレースホルダー管理操作
- 各操作後の状態（PDFカード数、プレースホルダー数、レイアウト）
- 選択の変更

ログはタイムスタンプと行番号付きでコンソールに出力されます。

### テストの実行

```bash
pytest
```

### プロジェクト構造

```
PDFas/
├── src/
│   ├── main.py              # アプリケーションエントリーポイント
│   ├── models/
│   │   └── undo_manager.py  # Undo/Redo管理
│   ├── views/
│   │   ├── main_window.py   # メインウィンドウ
│   │   ├── pdf_card.py      # PDFカードウィジェット
│   │   └── page_edit_window.py  # ページ編集ウィンドウ
│   ├── controllers/
│   │   └── folder_watcher.py    # フォルダ監視
│   └── utils/
│       └── pdf_utils.py     # PDFユーティリティ関数
├── tests/                   # テストファイル
├── docs/                    # ドキュメント
└── pyproject.toml          # プロジェクト設定
```

## ライセンス

MIT License
