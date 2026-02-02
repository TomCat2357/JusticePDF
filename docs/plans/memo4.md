# メインウインドウについて
メインウインドウの何もない所をクリックしたら、選択を解除してほしい。
SplitについてもUndo,Redo対応にしてほしい。
並び替えは、並び替えボタンを押した時だけにして、自動並び替えは行わないでほしい（今も行っていないかもですが）。
今、PDFの最後にemptyを常時作っているが、これをなくしてください。
NEWPDFボタンを押したら空のPDFを作る代わりに、PDFの最後にemptyを作ってください。
白紙の１ページだけのPDFを作るボタンを新設してください。
emptyはemptyに重ねたら消えますが、empty以外に重ねたら、無効としてもとのところに戻してください。
最後にemptyが複数並んだら、１個に縮退させるようになっていますが、それはやめてください。
名前で並び替えについてNameだけだとわけわかりません。DATEも同様。
並び替えボタンを押したらemptyは全部消してください。

# 個別ウインドウについて
ページをメインウインドウにD&Dしたら、そこにそのページの（複数選択していたら複数ページの）PDFが新設され、もとの個別ウインドウからは移動であれば消して、コピー（Ctrlキーを押しながらという意味）であればそのままでお願いします。エラーがでて先ほど止まったので、そこも直してください。
(.venv) PS C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas> python -m src.main
Traceback (most recent call last):
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 853, in dropEvent
    self._handle_page_extraction(data)
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\views\main_window.py", line 1054, in _handle_page_extraction
    extract_pages(pdf_path, str(new_path), [page_num])
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\src\utils\pdf_utils.py", line 83, in extract_pages
    output_doc.save(output_path)
  File "C:\Users\gk3t-\OneDrive - 又村 友幸\working\PDFas\.venv\Lib\site-packages\pymupdf\__init__.py", line 6503, in save
    raise ValueError("cannot save with zero pages")
ValueError: cannot save with zero pages

# 全体
debugはargumentで指定させてください。python -m src.main --log-level DEBUGみたいな感じかな。