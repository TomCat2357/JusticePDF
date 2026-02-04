# D&D重ね合わせ機能の修正プラン

## 問題の概要
メイン画面でのドラッグ＆ドロップによるファイル重ね合わせ（マージ）が動作しなくなった。

## 原因
`src/views/main_window.py` の `dropEvent` メソッド（1290行目付近）で、`_drop_indicator_index` の値を使う前に `_hide_drop_indicator()` を呼んでいるため、値が `-1` にリセットされてしまう。

**問題のコード（1296行目）：**
```python
def dropEvent(self, event) -> None:
    ...
    self._hide_drop_indicator()  # ここで _drop_indicator_index = -1 にリセット
    ...
    if self._drop_indicator_index == -2:  # マージモード判定 - 常にFalse
```

## 修正方針
`dropEvent` の最初で `_drop_indicator_index` の値を保存し、その後のマージモード判定に保存した値を使用する。

## 修正内容

### 対象ファイル
- `src/views/main_window.py`

### 変更内容
`dropEvent` メソッド内で：
1. `_hide_drop_indicator()` を呼ぶ**前**に `_drop_indicator_index` の値を保存
2. マージモード判定に保存した値を使用

**修正後のコード（概要）：**
```python
def dropEvent(self, event) -> None:
    ...
    # 値を保存してからリセット
    drop_mode = self._drop_indicator_index
    self._hide_drop_indicator()
    ...
    if drop_mode == -2:  # マージモード判定
        # 重ね合わせ処理
```

## 速度改善の維持
この修正は速度に影響しない。既存の速度改善（loadpage改善など）はそのまま維持される。

## 検証方法
1. アプリを起動: `python -m src.main --log-level DEBUG`
2. PDFカードをドラッグして別のカードの中央部分にドロップ
3. ログで `_drop_indicator_index=-2` が表示され、マージが実行されることを確認
4. Undoでマージが元に戻ることを確認
