import streamlit as st
import pandas as pd
import io
import math
from collections import Counter

# =========================================================
# 条件設定：ここを変えるだけで再計算条件を変更できる
# =========================================================

MAX_PER_ITEM = 2              # 同じ商品は最大2点まで
UNDER_ALLOWANCE = 10          # 余りは10円以内
OVER_ALLOWANCE = 5            # オーバーは5円まで
TOP_RESULTS = 10              # 表示する候補数

# カテゴリ偏り防止条件
MIN_ITEMS_FOR_CATEGORY_CHECK = 2   # 合計2点以上ならカテゴリチェック
MIN_DISTINCT_CATEGORIES = 2        # 2カテゴリ以上を推奨
MAX_CATEGORY_SHARE = 0.70          # 1カテゴリが全体の70%を超えたら除外

# 計算量を抑えるため、各合計金額ごとに保持する組み合わせ数
MAX_COMBOS_PER_SUM = 80

# =========================================================
# CSV読み込み
# =========================================================

def read_csv_auto_encoding(uploaded_file):
    """
    UTF-8, Shift-JIS系のCSVを自動的に読み込む
    """
    data = uploaded_file.getvalue()
    encodings = ["utf-8-sig", "utf-8", "cp932", "shift_jis"]

    for enc in encodings:
        try:
            return pd.read_csv(io.BytesIO(data), encoding=enc)
        except Exception:
            pass

    raise ValueError("CSVの文字コードを読み取れませんでした。UTF-8またはShift-JISで保存してください。")


def normalize_columns(df):
    """
    必要列の確認と整形
    """
    required_columns = ["商品名", "価格", "カテゴリ"]

    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"CSVに必要な列がありません: {missing}")

    df = df[required_columns].copy()

    # 価格を数値化
    df["価格"] = (
        df["価格"]
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("円", "", regex=False)
        .str.strip()
    )
    df["価格"] = pd.to_numeric(df["価格"], errors="coerce")

    # 不正データ除外
    df = df.dropna(subset=["商品名", "価格", "カテゴリ"])
    df = df[df["価格"] > 0]

    df["価格"] = df["価格"].astype(int)
    df["商品名"] = df["商品名"].astype(str)
    df["カテゴリ"] = df["カテゴリ"].astype(str)

    return df.reset_index(drop=True)


# =========================================================
# カテゴリ条件
# =========================================================

def is_category_balanced(combo, products):
    """
    combo: {商品index: 個数}
    products: 商品DataFrame
    """
    total_items = sum(combo.values())

    if total_items < MIN_ITEMS_FOR_CATEGORY_CHECK:
        return True

    category_counter = Counter()

    for idx, qty in combo.items():
        category = products.loc[idx, "カテゴリ"]
        category_counter[category] += qty

    distinct_categories = len(category_counter)

    if distinct_categories < MIN_DISTINCT_CATEGORIES:
        return False

    max_category_count = max(category_counter.values())
    max_share = max_category_count / total_items

    if max_share > MAX_CATEGORY_SHARE:
        return False

    return True


# =========================================================
# 組み合わせ探索
# =========================================================

def find_combinations(products, target_amount):
    """
    目標金額に近い商品の組み合わせを探す
    """
    min_total = target_amount - UNDER_ALLOWANCE
    max_total = target_amount + OVER_ALLOWANCE

    if min_total < 0:
        min_total = 0

    # dp[合計金額] = [combo, combo, ...]
    # comboは {商品index: 個数}
    dp = {0: [dict()]}

    for idx, row in products.iterrows():
        price = int(row["価格"])
        new_dp = dict(dp)

        for current_sum, combos in dp.items():
            for combo in combos:
                for qty in range(1, MAX_PER_ITEM + 1):
                    new_sum = current_sum + price * qty

                    if new_sum > max_total:
                        continue

                    new_combo = combo.copy()
                    new_combo[idx] = qty

                    if new_sum not in new_dp:
                        new_dp[new_sum] = []

                    new_dp[new_sum].append(new_combo)

                    # 組み合わせ数が増えすぎないよう制限
                    if len(new_dp[new_sum]) > MAX_COMBOS_PER_SUM:
                        new_dp[new_sum] = new_dp[new_sum][:MAX_COMBOS_PER_SUM]

        dp = new_dp

    results = []

    for total, combos in dp.items():
        if min_total <= total <= max_total:
            for combo in combos:
                if not combo:
                    continue

                if is_category_balanced(combo, products):
                    diff = total - target_amount
                    total_items = sum(combo.values())

                    results.append({
                        "total": total,
                        "diff": diff,
                        "abs_diff": abs(diff),
                        "total_items": total_items,
                        "combo": combo
                    })

    # 近い順、次にオーバーしないもの優先、次に品数が少ない順
    results = sorted(
        results,
        key=lambda x: (
            x["abs_diff"],
            1 if x["diff"] > 0 else 0,
            x["total_items"]
        )
    )

    return results[:TOP_RESULTS]


def combo_to_dataframe(combo, products):
    rows = []

    for idx, qty in combo.items():
        name = products.loc[idx, "商品名"]
        price = int(products.loc[idx, "価格"])
        category = products.loc[idx, "カテゴリ"]

        rows.append({
            "商品名": name,
            "カテゴリ": category,
            "単価": price,
            "個数": qty,
            "小計": price * qty
        })

    return pd.DataFrame(rows)


# =========================================================
# Streamlit画面
# =========================================================

st.set_page_config(
    page_title="購買ぴったり使い切りアプリ",
    page_icon="🛒",
    layout="wide"
)

st.title("🛒 購買ぴったり使い切りアプリ")

st.write(
    "指定した金額にできるだけ近くなるように、購買の商品リストから組み合わせを探します。"
)

with st.sidebar:
    st.header("条件")

    target_amount = st.number_input(
        "使いたい金額 n 円",
        min_value=1,
        value=500,
        step=10
    )

    st.write("現在の条件")
    st.write(f"- 同じ商品は最大 **{MAX_PER_ITEM}点** まで")
    st.write(f"- 余りは **{UNDER_ALLOWANCE}円以内**")
    st.write(f"- オーバーは **{OVER_ALLOWANCE}円まで**")
    st.write(f"- 表示候補数: **{TOP_RESULTS}件**")
    st.write(f"- 1カテゴリの最大割合: **{int(MAX_CATEGORY_SHARE * 100)}%以下**")

uploaded_file = st.file_uploader(
    "商品リストCSVをアップロードしてください",
    type=["csv"]
)

if uploaded_file is not None:
    try:
        products = read_csv_auto_encoding(uploaded_file)
        products = normalize_columns(products)

        st.subheader("読み込んだ商品リスト")
        st.dataframe(products, use_container_width=True)

        if st.button("組み合わせを探す"):
            results = find_combinations(products, target_amount)

            st.subheader("検索結果")

            if not results:
                st.warning(
                    "条件に合う組み合わせが見つかりませんでした。"
                    "カテゴリ条件をゆるめるか、商品数を増やしてみてください。"
                )
            else:
                for i, result in enumerate(results, start=1):
                    total = result["total"]
                    diff = result["diff"]

                    if diff < 0:
                        diff_text = f"{abs(diff)}円余り"
                    elif diff > 0:
                        diff_text = f"{diff}円オーバー"
                    else:
                        diff_text = "ぴったり"

                    with st.expander(
                        f"候補{i}: 合計 {total}円 / {diff_text}",
                        expanded=(i == 1)
                    ):
                        result_df = combo_to_dataframe(result["combo"], products)
                        st.dataframe(result_df, use_container_width=True)

                        category_summary = (
                            result_df
                            .groupby("カテゴリ")["個数"]
                            .sum()
                            .reset_index()
                            .rename(columns={"個数": "カテゴリ別点数"})
                        )

                        st.write("カテゴリ構成")
                        st.dataframe(category_summary, use_container_width=True)

    except Exception as e:
        st.error(f"エラーが発生しました: {e}")

else:
    st.info("まず商品リストCSVをアップロードしてください。")