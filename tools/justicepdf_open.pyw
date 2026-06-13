r"""Explorer 右クリック「JusticePDFで開く」用ランチャ。

レジストリのコマンドは任意の作業ディレクトリでこのファイルを ``pythonw.exe``
（コンソール窓なし）から起動する。アプリは ``-m src.main`` 相当でプロジェクト
ルートを基準に import する前提なので、ここで cwd と ``sys.path`` をルートに固定
してから ``src.main.main()`` を呼ぶ。

レジストリのコマンド例:
    "<root>\.venv\Scripts\pythonw.exe" "<root>\tools\justicepdf_open.pyw" "%1"
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.main import main

if __name__ == "__main__":
    main()
