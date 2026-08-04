# フォルダごとの並び順の永続化 — 設計案

## 0. 現状（調査結果）

**結論: 並び順を保存する仕組みは存在しない。「リセットされる」のは実装通りの挙動（未実装）。**

| 項目 | 現状 |
| --- | --- |
| 並び順の実体 | `MainWindow._cards` / `_folder_cards`（Python リストの要素順）`src/views/main_window.py:76-77` |
| 表示順 | `list(_folder_cards) + list(_cards)`（フォルダが先、PDF が後）`main_window.py:771` |
| ソートモード | `_sort_order = "manual"` / `_sort_ascending = True`。初期値は常に `"manual"`。`main_window.py:81-82` |
| ソート処理 | `_sort_cards()` `main_window.py:781-797`。`"manual"` の分岐は無く、リスト順をそのまま使う |
| 手動並び替え | D&D で `_cards.remove()` → `insert()`、その後 `_sort_order = "manual"`。`main_window_dragdrop.py:384-394, 471-474, 504, 749-754` |
| 起動時のロード | `_load_existing_files()` `main_window.py:505-510`。サブフォルダは **名前順に強制ソート**、PDF は `os.listdir()` の順（＝OS 依存で不定） |
| 永続化レイヤ | `QSettings` は利用可（`main.py:36-39` で org/app 名設定済み）だが、使用箇所は FreeText フォントサイズと印刷設定のみ。ウィンドウ状態も並び順も未保存 |
| フォルダ単位のメタデータ機構 | 無し（新規に作る必要あり） |
| ウィンドウとフォルダの関係 | サブフォルダを開くと **別 MainWindow** が生成される（`main_window.py:1086`）。＝1 ウィンドウ = 1 フォルダ |

つまり、再起動のたびに `os.listdir()` の順で作り直されるため、手動で並べた順序は必ず失われる。

---

## 1. 設計方針（決めるべき 5 点）

### 1-1. キー（どの単位で保存するか）

**フォルダの正規化済み絶対パス** を単位とする。1 ウィンドウ = 1 フォルダなので `MainWindow._work_dir` がそのままキーになる。

```python
def folder_key(path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))
```

- Windows なので `normcase`（小文字化 + `/`→`\`）で大文字小文字・区切り文字の揺れを吸収する。
- UNC パス（`\\server\share\...`）はそのまま文字列キーとして扱う。マップされたドライブ（`Z:\...`）と UNC は別キーになるが、実害は「順序が復元されない」だけなので許容する。
- 保存ファイル内では、キー衝突・可読性の観点から `{key: {...}}` の素直な JSON マップとする（後述の通り QSettings は使わないので、`/` がグループ区切りとして解釈される問題は起きない）。

### 1-2. 保存先 — **中央ストア（JSON ファイル 1 個）を採用**

`%APPDATA%\JusticePDF\JusticePDF\folder_order.json` に、全フォルダ分をまとめて保存する。
（`main.py:36-39` で organizationName / applicationName が両方 `"JusticePDF"` のため、`QStandardPaths.AppDataLocation` は 2 階層になる。実装ではこのパスを定数として明示し、テストでは差し替え可能にする。）

サイドカー方式（各フォルダに `.justicepdf_order.json` を置く）と比較した理由:

| | 中央ストア（採用） | サイドカー（不採用） |
| --- | --- | --- |
| フォルダの移動・リネーム | エントリが失効（自然順にフォールバック） | 追従できる ◎ |
| ユーザーのフォルダを汚さない | ◎ | × |
| 読み取り専用・権限のないフォルダ | 影響なし ◎ | 保存失敗 × |
| 書き込みの原子性 | 1 ファイルを atomic replace で完結 ◎ | フォルダごとに個別 |
| リセット・調査 | ファイル 1 個を消せばよい ◎ | 各所に散る × |
| エクスポート・共有時の混入 | 影響なし ◎ | 経路によっては同梱される恐れ |

**決め手**: このアプリは外部フォルダを開くとき `~/Documents/PDFs` 配下に **コピーしてから** 開く（`main_window.py:187-190` `open_external_folder`）。つまりキーの大半は固定のローカルルート配下に収まり、サイドカー方式の唯一の利点である「フォルダ移動への追従」がほとんど効かない。一方で、ユーザーのフォルダにアプリ固有ファイルを撒くコストは常に発生する。よって中央ストアが有利。

> 検証済みの補足: `FolderWatcher` は `.pdf` のみをイベント化し（`folder_watcher.py:15-16, 90-95`）、`get_subfolders()` はディレクトリのみを返すため、サイドカーを置いても**監視イベントやカード表示には現れない**。この点はサイドカー不採用の理由にはならない。

`QSettings` ではなく独立 JSON にする理由: Windows では `QSettings` は既定でレジストリに書き込むため、フォルダ数に比例して増える可変長データの置き場として不適。また純粋関数としてテストしやすい。

**ファイル形式:**

```json
{
  "version": 1,
  "folders": {
    "c:\\users\\foo\\documents\\pdfs": {
      "manual_files": ["b.pdf", "a.pdf", "c.pdf"],
      "manual_subfolders": ["2024", "2023"],
      "sort_order": "manual",
      "sort_ascending": true,
      "updated_at": "2026-08-04T10:00:00"
    }
  }
}
```

- ファイル名は **basename のみ**（パス全体を持つとフォルダ移動時に完全に無効化されるため）。
- `manual_files` と `manual_subfolders` を分けて持つ（表示も「フォルダが先、PDF が後」で分かれているため）。
- **`manual_*` は「手動順」専用のフィールド**。`sort_order` が `"name"` / `"date"` のときは更新しない（§1-4 / §1-5 参照）。名前順・日付順は再計算できるので保存する必要がなく、手動順だけが失われると復元不能なため。

### 1-3. 突き合わせ方針（ディスク側が変わっていた場合）— 最重要

起動時、`os.listdir()` の結果（`disk`）と保存済みリスト（`saved`）を次のルールで合成する。

```
1. saved にあり disk にもある  → saved の順で先頭から並べる
2. disk にあり saved に無い    → 末尾に、名前の自然順で追加
3. saved にあり disk に無い    → 捨てる（次回保存時にストアからも消える）
```

- **アプリ外で追加されたファイルは末尾**。これはセッション中の挙動（新規ファイルはカード末尾に追加される）と一致するので、ユーザーから見て一貫している。
- **アプリ外でのリネームは「削除 + 追加」扱い**になり、そのファイルだけ末尾に移動する。セッション中のリネームは `FolderWatcher` の moved イベントでカードが更新されるため位置は保たれる。この非対称は仕様として受け入れる。
- 純粋関数として切り出し、単体テストの主対象にする。

```python
def merge_order(disk_names: list[str], saved_names: list[str]) -> list[str]:
    disk_set = set(disk_names)
    saved_set = set(saved_names)
    kept = [n for n in saved_names if n in disk_set]
    added = sorted((n for n in disk_names if n not in saved_set), key=str.lower)
    return kept + added
```

### 1-4. ソートモードとの関係

**手動順（`manual_*`）と、ソートモード（`sort_order` / `sort_ascending`）を保存する。**

保存の規則（§1-5 と対になる、実装上の不変条件）:

> **`manual_*` フィールドを書き換えてよいのは `sort_order == "manual"` のときだけ。**
> 名前順・日付順が有効な間の保存は、`sort_order` / `sort_ascending` / `updated_at` のみを更新し、`manual_*` は**そのまま残す**。

現在の `_sort_cards()` は `_cards` を in-place で並べ替えるため、名前順を適用した時点でメモリ上の手動順は失われる。この規則が無いと、名前順を一度クリックしただけで保存済みの手動順まで名前順で上書きされ、二度と戻せなくなる。

復元時の挙動:

- `sort_order == "manual"` → `manual_*` を §1-3 のルールで合成して適用。
- `sort_order == "name"` / `"date"` → **そのソートを再適用する**（`manual_*` は読まない）。新しく増えたファイルも正しい位置に入るため、こちらのほうが期待に合う。

ソートモードを保存しないと、名前順にしていた人が再起動後に `os.listdir()` 順を見て「リセットされた」と感じるので、**モードの保存は必須**。

現状「手動順に戻す」UI が無い（D&D で暗黙的に `"manual"` に戻るだけ）ので、並び替えメニュー（`main_window_fileops.py:345-356`）に **「手動順」** 項目を追加する。選択時はストアの `manual_*` を読み直して適用する。

### 1-5. 保存タイミングと原子性

**トリガー（すべて「dirty フラグを立てて debounce」）:**

| きっかけ | 場所 |
| --- | --- |
| D&D による並び替え・コピー・外部ドロップ | `main_window_dragdrop.py:384-394, 471-474, 504, 749-754` |
| Undo / Redo で順序が戻った時 | `UndoManager` のリスナー（`_on_undo_manager_changed`） |
| ソートメニューの適用 | `_apply_sort()`（**モードのみ保存。`manual_*` は触らない** — §1-4） |
| ファイル追加・削除・リネーム後 | `_reconcile_with_disk()` / watcher イベント後 |
| ウィンドウを閉じる時（即時 flush） | `main_window.py:1502` `closeEvent` |

debounce は `QTimer.singleShot(500, ...)` 相当の単発タイマーで、連続 D&D 中に何度も書かないようにする。

**原子性と多重起動:**

- 保存は毎回 **read-modify-write**（他ウィンドウが書いた別フォルダのエントリを消さないため）→ 一時ファイルに書く → `os.replace()` で置換。
- 同一フォルダを複数ウィンドウで開いた場合は「後勝ち」。並び順という性質上これで問題ない。
- 読み込み失敗（JSON 破損・ファイル無し・権限エラー）は **すべて握りつぶして「保存済み順序なし」として扱う**。並び順の永続化が原因でアプリが起動しない事態を作らない。

**肥大化対策:** 読み込み時に、存在しないフォルダのエントリを削除。さらに上限 500 件を超えたら `updated_at` の古い順に間引く。

---

## 2. 実装計画

### Phase 1 — ストアと合成ロジック（Qt 非依存・テスト可能）

新規 `src/utils/order_store.py`:

```python
DEFAULT_STORE_PATH: Path                  # %APPDATA%\JusticePDF\folder_order.json
def folder_key(path) -> str
def merge_order(disk_names, saved_names) -> list[str]
def load_folder_order(folder, store_path=None) -> FolderOrder | None
def save_folder_order(folder, files, subfolders, sort_order, sort_ascending, store_path=None) -> None
def prune(store_path=None) -> None
```

- 全関数で `store_path` を差し替え可能にし、テストは `tmp_path` を使う。
- 新規テスト `tests/test_order_store.py`（`merge_order` の各ケース、破損 JSON、atomic 保存、prune）。

### Phase 2 — 復元（読み込み側）

- `_load_existing_files()` `main_window.py:505-510` を書き換え:
  - `load_folder_order(self._work_dir)` を読む。
  - `sort_order` / `sort_ascending` を `self._sort_order` / `self._sort_ascending` に復元。
  - `"manual"` なら `merge_order()` の結果順に `_add_folder_card` / `_add_card` を呼ぶ。
  - `"name"` / `"date"` なら従来通り追加してから `_sort_cards()` を呼ぶ。
- **507 行目のサブフォルダ強制名前順ソートを撤去**（保存済み順があるならそれを優先）。
- ツールバーのソートボタン表示を復元後の状態に同期。

### Phase 3 — 保存（書き込み側）

- `MainWindow` に `_schedule_order_save()` / `_flush_order_save()` を追加（単発 QTimer、既存の `_schedule_reconcile` と同じ流儀）。
- 1-5 の表の各トリガーから `_schedule_order_save()` を呼ぶ。
- `closeEvent` で `_flush_order_save()`。
- 新規テスト `tests/test_main_window_order_persistence.py`（D&D 後に保存される / 再構築で順序が復元される / 外部追加ファイルが末尾に来る）。

### Phase 4 — ソートメニューに「手動順」を追加

- `main_window_fileops.py:345-356` のメニューに項目追加、選択時にストアの手動順を再適用。

### Phase 5 — ドキュメント

- `CHANGELOG.md` / `docs/feature-updates.md` に追記。マニュアル（`dev/build_manual_docx.py`）にも「並び順は自動的に記憶されます」の一文を追加し、ビルドスクリプトを再実行。

---

## 3. リスクと注意点

- **`_reconcile_with_disk()`（`main_window.py:616-698`）との整合**: 差分検出で追加されるカードは末尾に入るので、1-3 の合成ルールと同じ挙動。ただし reconcile 後に保存トリガーを呼ばないと、削除されたファイルがストアに残り続ける。
- **`sorted()` の撤去による回帰**: 507 行目のサブフォルダ名前順ソートは、保存済み順が無いフォルダでは維持する必要がある（初回表示の見た目を変えないため）。
- **多重起動での競合**: read-modify-write を徹底しないと、他ウィンドウ分のエントリを消す。
- **ネットワークドライブ / UNC**: 外部フォルダは `~/Documents/PDFs` にコピーしてから開かれる（`main_window.py:187-190`）ため、キーの大半は固定のローカルパスになり、実質ほぼ発生しない。仮にキーが変わっても「順序が復元されない」だけで壊れはしない。
- **既存テストへの影響**: `MainWindow` を生成するテストが本番のストアファイルを読み書きしないよう、`conftest.py` でストアパスを `tmp_path` に向けるフィクスチャを用意する。

---

## 4. 作業量の目安

| Phase | 内容 | 規模 |
| --- | --- | --- |
| 1 | `order_store.py` + 単体テスト | 新規 ~150 行 + テスト ~120 行 |
| 2 | `_load_existing_files` の復元処理 | 既存 ~30 行改修 |
| 3 | 保存トリガー配線 + テスト | 既存 ~8 箇所 + テスト ~100 行 |
| 4 | 「手動順」メニュー | 既存 ~20 行 |
| 5 | ドキュメント・マニュアル | — |

Phase 1〜3 だけで「閉じても並び順が残る」という要望は満たせる。Phase 4 は付随的な改善。
