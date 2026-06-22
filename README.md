# 購買ぴったり使い切りアプリ

## GitHubのCSV更新が反映されない場合

原因は主に2つあります。

1. `@st.cache_data` によりCSV読み込み結果がキャッシュされている
2. Streamlit Cloudでリポジトリ内ファイル `data/priceList.csv` を読んでいる場合、GitHubのCSVを更新してもアプリ側の実行環境が古いファイルを持っていることがある

## おすすめ設定

CSVを頻繁に更新する場合は、`app.py` の `PRODUCT_CSV_URL` にGitHub Raw URLを設定してください。

```python
PRODUCT_CSV_URL = "https://raw.githubusercontent.com/ユーザー名/リポジトリ名/main/data/priceList.csv"
```

CSVをURLから読む設定にすると、アプリの再デプロイなしでも更新を反映しやすくなります。
ただし、アプリ側では `CSV_CACHE_TTL_SECONDS = 300` としているので、最大5分程度遅れる場合があります。
すぐ反映したい場合は、サイドバーの「商品CSVを再読み込み」ボタンを押してください。

## リポジトリ構成

```text
repo/
├─ app.py
├─ requirements.txt
└─ data/
   └─ priceList.csv
```

## 起動方法

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 条件

- 同じ商品は最大2点まで
- 余りは10円以内
- オーバーは5円まで
- 2点以上なら原則2カテゴリ以上
- 1カテゴリの割合が70%を超える組み合わせは除外
- 700円、1400円、2100円のショートカットボタンあり
- 探索前に商品リストをランダム化
