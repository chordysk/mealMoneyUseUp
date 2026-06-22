# 購買ぴったり使い切りアプリ

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

## 商品データ

商品データは `data/priceList.csv` に固定配置します。
アプリ起動時にこのCSVを自動で読み込みます。

対応している列名例:

```csv
商品名,価格（税込み）,カテゴリ
あなたのお茶,128,飲料
綾鷹,133,飲料
```

## 条件

- 同じ商品は最大2点まで
- 余りは10円以内
- オーバーは5円まで
- 2点以上なら原則2カテゴリ以上
- 1カテゴリの割合が70%を超える組み合わせは除外
