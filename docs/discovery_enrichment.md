# 書誌補完と関連本 v2（2026-09-28）

## 範囲と成果物

UIは別作業。今回の変更は書誌補完、索引生成、関連本の事前計算、ローカル/AWS APIの読み出しと検証。本番配信は未実施。週替わりおすすめの選定と初期HTMLは変更しない。

元のbook_metadataは空だったが、book_covers.raw_jsonには書誌取得時の応答が残っていた。ネットワーク再取得せず、ISBNチェックサムとISBN-10/13の同値性を確認して再利用した。元DBは読み取り専用で開き、コピーに追加する。既存のmatched書誌は上書きしない。

4,202冊の入力から3,970冊へ書誌を取り込み、紹介文3,380冊、ページ数1,468冊、書名の読み2,266冊、著者の読み1,960冊を得た。ISBN未確認160件、利用可能フィールドなし53件は取り込まない。ISBN一致は提供元の書誌自体の正確性を保証するものではない。

## UI作業への引き継ぎ

生成済みのファイル（git管理外）:

- `outputs/discovery-enriched/library.db`: 書誌・フィルタ・関連本を含むDB。
- `outputs/discovery-enriched/search-index.json`: 同じDBから作った候補索引。
- `outputs/discovery-enriched/related-evaluation.json`: 分類別30冊の旧/新関連本比較。

UIファイルと`frontend/public/search-index.json`は、この作業では書き換えていない。UI側で採用する際は新しい索引をpublicへコピーし、索引キャッシュを再取得する。候補索引は既存version 1形式を維持し、booksの各要素へ任意フィールド`title_reading`・`authors_reading`を追加した。漢字書名のかな検索を有効にするには、UI側の候補照合にこれらも含める必要がある。読み未取得時はフィールドを省略する。

coverageは`{books:4202,page_count:1468,level:31}`。索引675,307 bytes、gzip 191,408 bytes。ページ数・レベルの不明本は該当条件の検索から除外される。レベル31冊はすべてbeginnerで、現段階の中級・上級フィルタには該当本がない。レベルは分野内の対象読者の目安であり、前提知識ゼロを保証しない。

検索APIの契約は従来どおり:

- `/api/books/search?topic=python&max_pages=300`
- `/api/books/search?level=beginner`
- `/api/books/11/related`: 従来のbooks配列とreasons。新しい理由`同じテーマの本`が増える。

## 分類と根拠

テーマは19ルール中17種に該当本がある。書名・提供元カテゴリを優先し、紹介文だけの場合は同じテーマへの複数回言及を必要とする。book_topics.evidenceに判定元を保存する。これはルールによる推定で、網羅的な分類・人による承認ではない。ブルータリズムとアニメーション制作は今回0件。未所蔵の証明にはならない。

レベルは紹介文の明示的な対象読者表現だけから判定し、根拠の一文・提供元・source_idをbook_discovery.evidenceに保存する。対象が複数レベルにまたがる、他書との比較、否定、章だけの説明などは不明に戻す。書名の「入門」やページ数から難易度を推測しない。

book_metadata_provenanceには提供元URL、ISBN、キャッシュ取得日時、元フィールド名と読みを保持する。提供元URLがないopenBDではISBN/source_idとフィールド位置が根拠となる。

ページ数の参照仕様: [Google Books Volume](https://developers.google.com/books/docs/v1/reference/volumes)、ONIXの[Content page count (11)](https://ns.editeur.org/onix/en/23/11)と[Pages (03)](https://ns.editeur.org/onix/en/24/03)。ONIXの他のExtent種別や冊子サイズをページ数として扱わない。

## 関連本の選び方

Janome 0.5.0で日本語を単語に分け、書名・紹介文・カテゴリのTF-IDFベクトルを正規化して比較する。[Janome公式資料](https://janome.mocobeta.dev/ja/)。外部モデルやLLMの呼び出しはない。Janomeは事前生成時だけ使い、API/Lambdaで形態素解析しない。

- 著者・出版社名を内容ベクトルに混ぜず、紹介文末尾の発行元表記も除く。
- テーマ一致・分類の近さを補助信号とし、別分野へ広げる場合は複数語と高い内容類似度を求める。
- 自分自身、同じ正規化書名、同じISBN、候補内の重複を除く。同一著者は最大2冊とし、既選択本との類似度にも軽い減点を付ける。
- 同じ書名を一律除外するため、同名の別作品や巻違いも除外される。この方式では取りこぼしを許容する。
- 保存するstrategyは`content-diverse-v2`。scoreは多様性調整後の表示順位を保存するための順位点であり、類似度そのものではない。評価JSONにはcosineを別途保持する。
- book_recommendation_runsで各本の計算完了を記録。新方式が0件でも旧方式で埋めない。未計算の本・旧DBだけはbm25-keyword-v1を利用する。

「深める」「次に読む」「別視点」といった学習上の関係は、この計算だけでは断定しない。テーマと語彙の関連を改善した段階であり、意味埋め込みやLLMによる意味理解を実装したとは扱わない。

## 再生成

```sh
uv run --frozen python scripts/import_cached_metadata.py \
  --db aws/functions/go_api/library.db \
  --output-db outputs/discovery-enriched/metadata.db
uv run --frozen python scripts/build_discovery_index.py \
  --db outputs/discovery-enriched/metadata.db \
  --output-db outputs/discovery-enriched/library.db \
  --output-index outputs/discovery-enriched/search-index.json
uv run --frozen python scripts/build_related_recommendations.py \
  --db outputs/discovery-enriched/library.db \
  --report outputs/discovery-enriched/related-evaluation.json
```

稼働中のDBを再生成先にしない。別パスへ生成してからAPIを切り替える。AWS prepare_assetsにも同じ順序を組み込んだが、今回は実行・配信していない。

## 検証と残り

Python関連31件、ローカル/AWSの両Goモジュールの全テスト成功。ISBN誤照合、ISBN-10/13同値、HTML除去、ページ数の異常値、元DB不変、未確認書誌の除外、レベルの比較・否定・範囲表現、推薦重複、著者偏り、単語の部分一致、v2の0件/旧DBのfallbackを検証した。

残るデータ課題は未収録書誌、読みの欠損、細かいテーマの網羅性、中級/上級の根拠と人による分類確認。関連本は30冊の分類別比較と特定の誤推薦の回帰確認を行うが、利用者による適合性評価スコアではない。新しいAI検索・要約は別工程。

## ローカルでの確認結果

APIは`http://127.0.0.1:18089`、稼働DBは`/tmp/booksearch-discovery-enriched/library-final.db`へ切り替え済み。frontend開発サーバーはそのまま。UI用の静的索引は別作業で取り込む必要がある。

最終生成は3,406冊に15,905件の関連候補。各本最大6冊、自分自身/同じ書名/ISBNの除外、同一著者2冊までを全件確認した。分類別30冊の比較で「自然言語処理→自然言語処理/音声言語処理」「AutoCAD→AutoCAD製図」「温かいテクノロジー→ロボット開発」などを確認。一方、十分な根拠がない796冊は空になる。これは旧方式より適合率が何％向上したという評価ではない。

最初の候補で出た「バイオリン→バイオプラ」「ホテルビジネス→発行元が同じ別業界の本」は、単語分割と発行元表記の除去で解消。回帰テストに追加した。テーマの英単語に日本語の助詞が続くケースも修正し、Python183冊・JavaScript25冊・SQL18冊を収録する。

実APIでページ上限、初級31冊、関連本6冊、根拠なし0冊を確認。新方式の0件に旧方式が混ざらないことも確認した。
