# PDFas 実装進捗

## 完了したタスク

### ✅ Task 1: Project Setup (完了)
- pyproject.toml作成（uv/.venv管理）
- ディレクトリ構造作成
- 基本的なmain.py実装
- コミット済み

### ✅ Task 2: PDF Utilities (完了)
- src/utils/pdf_utils.py実装
- tests/test_pdf_utils.py作成
- 全テスト通過
- コミット済み

### ✅ Task 3: Undo Manager (完了)
- src/models/undo_manager.py実装
- tests/test_undo_manager.py作成
- 全テスト通過
- コミット済み

### ✅ Task 4: Folder Watcher (完了)
- src/controllers/folder_watcher.py実装
- tests/test_folder_watcher.py作成
- 全テスト通過 (2/2)
- コミット済み

### ✅ Task 5: PDF Card Widget (完了)
- src/views/pdf_card.py実装
- tests/test_pdf_card.py作成
- 全テスト通過
- コミット済み

### ✅ Task 6: Main Window Basic Layout (完了)
- src/views/main_window.py実装
- src/main.pyを更新
- アプリケーション起動確認
- コミット済み

### ✅ Task 7: Page Edit Window (完了)
- src/views/page_edit_window.py実装
- PageThumbnailクラス実装
- PageEditWindowクラス実装
- アプリケーション起動確認
- コミット済み

### ✅ Task 8: カード並び替え（メインウィンドウ内D&D）(完了)
- src/views/pdf_card.pyにドラッグ機能追加
- src/views/main_window.pyにドロップ受け入れ機能追加
- tests/test_pdf_card_dnd.py作成
- tests/test_main_window_dnd.py作成
- コミット済み

### ✅ Task 9: カードのマージ（カードにカードをドロップ）(完了)
- src/views/pdf_card.pyにドロップ受け入れ機能追加
- src/views/main_window.pyにマージ処理追加
- tests/test_card_merge.py作成
- コミット済み

### ✅ Task 10: 外部ファイルドロップ (完了)
- Task 8で既に実装済み（_handle_external_file_drop）
- tests/test_external_drop.py作成
- コミット済み

### ✅ Task 11: ページの並び替え（編集ウィンドウ内D&D）(完了)
- src/views/page_edit_window.pyにドラッグ機能追加
- PageThumbnailにドラッグ処理実装
- PageEditWindowにドロップ処理実装
- tests/test_page_thumbnail_dnd.py作成
- コミット済み

### ✅ Task 12: ページの抽出（ページをメインウィンドウにドロップ）(完了)
- src/views/main_window.pyにページドロップ処理追加
- _handle_page_extractionメソッド実装
- tests/test_page_extraction.py作成
- コミット済み

## Phase 3: Drag and Drop Implementation (完了)

**完了日:** 2026-01-24

**実装された機能:**
1. カード並び替え（メインウィンドウ内D&D）
2. カードのマージ（カードにカードをドロップ）
3. 外部ファイルドロップ（エクスプローラーからのインポート）
4. ページの並び替え（編集ウィンドウ内D&D）
5. ページの抽出（ページをメインウィンドウにドロップ）

**実装されたMIMEタイプ:**
- `PDFCARD_MIME_TYPE = "application/x-pdfas-card"`
- `PAGETHUMBNAIL_MIME_TYPE = "application/x-pdfas-page"`

**テスト結果:**
```
21 passed in 1.76s
全テスト通過 ✅
```

**追加修正:**
- テストフィクスチャのtempfile処理を修正（test_pdf_card.py, test_pdf_card_dnd.py, test_page_thumbnail_dnd.py）
- WindowsでのPyMuPDFファイルアクセス問題に対応

**コミット:**
- `e2ec00f` test: add external file drop tests
- `b872232` feat: add page reordering via drag and drop
- `c0f5056` feat: add page extraction by dropping page to main window
- `0b06d9f` fix: correct tempfile handling in test fixtures

## 進行中のタスク

なし

## 未着手のタスク

なし (Phase 1-3 完了)

---

**プロジェクトステータス:** Phase 1-3 完了 🎉

**総テスト数:** 21
**通過率:** 100%

最終更新: 2026-01-24
