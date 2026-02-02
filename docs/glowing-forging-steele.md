# PDFas D&D仕様準拠 修正プラン

## 概要
`docs/requirements.md`の仕様に基づき、現在のD&D実装を修正する。

---

## 差分分析

### 1. 統合順序（Critical）
| 項目 | 仕様 | 現在の実装 |
|------|------|------------|
| メイン→メイン Overlay | `[from_files] + [to_file]` (fromが先頭) | `[to_file] + [from_files]` (toが先頭) ❌ |
| 個別→メイン Overlay | `[from_pages] + [to_pages]` (fromが先頭) | 末尾に追加 ❌ |

### 2. Overlay vs Insert判定
| 項目 | 仕様 | 現在の実装 |
|------|------|------------|
| PDFCARD_MIME_TYPE | 中央70%=Overlay / 端30%=Insert | 常にInsert ❌ |
| PAGETHUMBNAIL_MIME_TYPE | 中央=Overlay / 端=Insert | ✅ 実装済み |

### 3. 視覚フィードバック
| 項目 | 仕様 | 現在の実装 |
|------|------|------------|
| ドラッグバッジ | 「N items」表示 | なし ❌ |
| Overlayハイライト | "＋"マーク | 緑背景のみ |

### 4. ペイロード形式（Optional）
| 項目 | 仕様 | 現在の実装 |
|------|------|------------|
| 形式 | JSON `{kind, files/pages}` | パス文字列 `path1|path2` |

※ペイロード形式は動作に影響しないため優先度低

---

## 修正タスク

### Task 1: 統合順序の修正
**ファイル**: `src/views/main_window.py`

#### 1.1 `_on_card_merge` メソッド (line 340-393)
```python
# 現在（toが先頭）
source_paths = [target_path] + [c.pdf_path for c in source_cards]

# 修正後（fromが先頭）
source_paths = [c.pdf_path for c in source_cards] + [target_path]
```

#### 1.2 `_handle_page_extraction` メソッド (line 886-972)
```python
# 現在（末尾に挿入）
insert_at = get_page_count(target_card.pdf_path)
insert_pages(target_card.pdf_path, tmp_path, [insert_at] * len(page_nums))

# 修正後（先頭に挿入）
insert_pages(target_card.pdf_path, tmp_path, [0] * len(page_nums))
```

---

### Task 2: PDFCARD_MIME_TYPEのOverlay判定追加
**ファイル**: `src/views/main_window.py`

#### 2.1 `dragMoveEvent` メソッド (line 674-699)
PDFCARD_MIME_TYPEドロップ時もOverlay/Insert判定を追加：
```python
if target_card and event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
    # 自己ドロップ除外チェック
    source_paths = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8').split('|')
    if target_card.pdf_path not in source_paths:
        card_rect = target_card.geometry()
        edge_margin = card_rect.width() * 0.15  # 70%中央 = 15%端
        if drop_pos.x() > card_rect.left() + edge_margin and drop_pos.x() < card_rect.right() - edge_margin:
            # Overlay mode
            self._hide_drop_indicator()
            target_card.setStyleSheet("PDFCard { background-color: #90EE90; border: 2px solid #228B22; }")
            self._drop_indicator_index = -2
            return
```

#### 2.2 `dropEvent` メソッド (line 749-770)
Overlay時はマージ処理、Insert時はリオーダー処理を分岐：
```python
if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
    source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
    drop_pos = self._container.mapFrom(self, event.position().toPoint())

    if self._drop_indicator_index == -2:  # Overlay mode
        target_card = self._get_card_at_pos(drop_pos)
        if target_card:
            self._on_card_merge(target_card, source_path)
    else:  # Insert mode
        self._handle_card_drop(source_path, drop_pos)
    event.acceptProposedAction()
```

---

### Task 3: 視覚フィードバック改善
**ファイル**: `src/views/pdf_card.py`, `src/views/page_edit_window.py`

#### 3.1 ドラッグバッジ追加
`PDFCard/mouseMoveEvent` (line 137-170)
```python
# ドラッグピクスマップにバッジ追加
if len(selected_paths) > 1:
    painter = QPainter(pixmap)
    painter.setPen(Qt.GlobalColor.white)
    painter.setBrush(QColor(0, 120, 215))
    painter.drawEllipse(pixmap.width() - 20, 0, 20, 20)
    painter.drawText(pixmap.width() - 15, 15, str(len(selected_paths)))
    painter.end()
```

`PageThumbnail/mouseMoveEvent` (line 93-123) にも同様に追加

---

### Task 4: Overlayハイライトの"＋"マーク（Optional）
**ファイル**: `src/views/pdf_card.py`

PDFCardにオーバーレイ表示用のラベルを追加するか、スタイルシートで疑似要素を使用

---

## 修正対象ファイル一覧
1. `src/views/main_window.py` - Task 1, 2
2. `src/views/pdf_card.py` - Task 3, 4
3. `src/views/page_edit_window.py` - Task 3

---

## 検証手順

### 機能テスト
1. **メイン→メイン Overlay**: 複数のPDFカードを選択し、別のカード中央にドロップ → fromファイルが先頭に来ていることを確認
2. **メイン→メイン Insert**: カードを端にドロップ → 挿入ラインが表示され、リオーダーのみ
3. **個別→メイン Overlay**: ページを選択しカード中央にドロップ → ページが先頭に挿入
4. **視覚フィードバック**: 複数選択ドラッグ時にバッジが表示される

### コマンド
```bash
# 単体テスト
pytest tests/

# GUIスモークテスト
python -m src.main
```

---

## 優先度
1. **High**: Task 1 (統合順序) - 仕様違反
2. **High**: Task 2 (Overlay判定) - 仕様違反
3. **Medium**: Task 3 (ドラッグバッジ) - UX改善
4. **Low**: Task 4 ("＋"マーク) - UX改善

#1
