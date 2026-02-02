# メイン画面
PDFをスタックしたらエラー
Traceback (most recent call last):
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 389, in _on_file_removed
    self._remove_card(path)
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 224, in _remove_card
    if card.pdf_path == pdf_path:
       ^^^^^^^^^^^^^
AttributeError: 'PlaceholderCard' object has no attribute 'pdf_path'

PDFを他のPDFの間に置いたら末のEmptyがなぜか複数に増える。
というかPDFの移動とか途中に置いたりとか、Emptyのところに動かしたりした時とか全体的にエラーがでたり想定外の挙動が見られるので、もう一度コードを再確認して必要な修正を行ってください。

# 個別PDF画面
メイン画面にページをD&Dしたらエラー
Traceback (most recent call last):
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 382, in _on_file_added
    if card.pdf_path == path:
       ^^^^^^^^^^^^^
AttributeError: 'PlaceholderCard' object has no attribute 'pdf_path'