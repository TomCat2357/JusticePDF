# メイン画面
remove emptyを行ったが、PDF並びの末のemptyが消えない
remove emptyボタンを押したが、そのDEBUGログがでない
PDFをctrl押しながらEmptyにD&Dしたがコピーされないで単に移動される
emptyをPDFの間にD＆Dしたが、PDFの間にemptyが挿入されない
remove emptyボタンを押したが、そのDEBUGログがでない
PDFを重ねたあと、UNDOをしたら以下のエラーがでた
21:03:19 [DEBUG] src.views.main_window:264 - [remove_card] Cards: 5, PDFs: 2, Placeholders: 3, Selected: 0 | Layout: [PDF(New_1.pdf), PDF(New_1_pages_1.pdf), Empty, Empty, Empty]
Traceback (most recent call last):
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 469, in _on_undo
    self._undo_manager.undo()
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\models\undo_manager.py", line 37, in undo
    action.undo_func()
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 956, in undo_reorder
    self._refresh_grid()
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 298, in _refresh_grid
    self._grid_layout.addWidget(card, row, col)
RuntimeError: wrapped C/C++ object of type PlaceholderCard has been deleted

# 個別PDF画面
個別PDF画面から違う個別PDF画面にD&Dで移動やコピーができない。