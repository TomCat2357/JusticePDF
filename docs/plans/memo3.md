# メイン画面
(.venv) PS C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas> set PDFAS_DEBUG=1
(.venv) PS C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas> python -m src.main
Traceback (most recent call last):
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 362, in _on_card_clicked
    self._clear_selection()
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 309, in _clear_selection
    card.set_selected(False)
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\pdf_card.py", line 113, in set_selected
    self._update_style()
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\pdf_card.py", line 88, in _update_style
    self.setStyleSheet("PDFCard { background-color: white; border: 1px solid #ccc; }")
RuntimeError: wrapped C/C++ object of type PDFCard has been deleted
状態などのデバッグがでない。
pdf1,pdf2,pdf3とあり、pdf1をpdf2に重ねた時、pdf1の場所は(empty)となるべきだがならない。
また、
重ねた後pdf2,pdf3となるが、pdf2をpdf3に重ねようとすると上のエラーがでる