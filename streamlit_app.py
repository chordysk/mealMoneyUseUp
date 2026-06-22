import streamlit as st
import pandas as pd
from pathlib import Path
from collections import Counter

# =========================================================
# 条件設定：ここを変えるだけで条件を調整できます
# =========================================================
MAX_PER_ITEM = 2              # 同じ商品は最大2点まで
UNDER_ALLOWANCE = 10          # 余りは10円以内
OVER_ALLOWANCE = 5            # オーバーは5円まで
TOP_RESULTS = 20              # 表示する候補数

# GitHubリポジトリ内の商品CSVの場所
# 例: repo/data/priceList.csv に置く場合
PRODUCT_CSV_PATH = Path("data/priceList.csv")

# カテゴリ偏り防止条件
MIN_ITEMS_FOR_CATEGORY_CHECK = 2   # 合計2点以上ならカテゴリチェック
MIN_DISTINCT_CATEGORIES = 2        # 原則2カテゴリ以上
MAX_CATEGORY_SHARE = 0.70          # 1カテゴリが全体の70%を超えたら除外

# 探索の上限。商品数が増えても重くなりすぎないようにするための設定
MAX_COMBOS_PER_SUM = 200

# CSV列名候補
NAME_COLUMNS = ["商品名", "品名", "商品", "name"]
PRICE_COLUMNS = ["価格", "価格（税込み）", "価格(税込み)", "税込価格", "税込み価格", "値段", "price"]
CATEGORY_COLUMNS = ["カテゴリ", "カテゴリー", "分類", "category"]


# =========================================================
# CSV読み込み・整形
# =========================================================
@st.cache_data
def read_csv_from_repo(csv_path: str):
    """GitHubリポジトリ内に置いた固定CSVを読み込む。"""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"商品CSVが見つかりません: {path}\n"
            "GitHubリポジトリ内に data/priceList.csv を置いてください。"
        )

    encodings = ["utf-8-sig", "utf-8", "cp932", "shift_jis"]
    last_error = None
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:
            last_error = e

    raise ValueError(f"CSVを読み込めませんでした。UTF-8またはShift-JISで保存してください。詳細: {last_error}")


def pick_column(df, candidates):
    """候補リストから実際に存在する列名を1つ選ぶ。"""
    for col in candidates:
        if col in df.columns:
            return col
    return None


def normalize_products(df):
    """商品名・価格・カテゴリの列名を統一し、価格を数値化する。"""
    name_col = pick_column(df, NAME_COLUMNS)
    price_col = pick_column(df, PRICE_COLUMNS)
    category_col = pick_column(df, CATEGORY_COLUMNS)

    missing = []
    if name_col is None:
        missing.append("商品名")
    if price_col is None:
        missing.append("価格")
    if category_col is None:
        missing.append("カテゴリ")
    if missing:
        raise ValueError(
            "CSVに必要な列が見つかりません: " + ", ".join(missing) +
            "\n列名は例として『商品名, 価格（税込み）, カテゴリ』にしてください。"
        )

    products = df[[name_col, price_col, category_col]].copy()
    products.columns = ["商品名", "価格", "カテゴリ"]

    products["価格"] = (
        products["価格"]
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("円", "", regex=False)
        .str.strip()
    )
    products["価格"] = pd.to_numeric(products["価格"], errors="coerce")

    products["商品名"] = products["商品名"].astype(str).str.strip()
    products["カテゴリ"] = products["カテゴリ"].astype(str).str.strip()

    products = products.dropna(subset=["商品名", "価格", "カテゴリ"])
    products = products[products["商品名"] != ""]
    products = products[products["カテゴリ"] != ""]
    products = products[products["価格"] > 0]
    products["価格"] = products["価格"].astype(int)

    return products.reset_index(drop=True)


# =========================================================
# カテゴリ偏り判定
# =========================================================
def is_category_balanced(combo, products):
    """同じカテゴリばかりの組み合わせを除外する。"""
    total_items = sum(combo.values())
    if total_items < MIN_ITEMS_FOR_CATEGORY_CHECK:
        return True

    category_counter = Counter()
    for idx, qty in combo.items():
        category_counter[products.loc[idx, "カテゴリ"]] += qty

    if len(category_counter) < MIN_DISTINCT_CATEGORIES:
        return False

    max_share = max(category_counter.values()) / total_items
    if max_share > MAX_CATEGORY_SHARE:
        return False

    return True


# =========================================================
# 組み合わせ探索
# =========================================================
def find_combinations(products, target_amount):
    """目標金額に近い商品の組み合わせを探す。"""
    min_total = max(0, target_amount - UNDER_ALLOWANCE)
    max_total = target_amount + OVER_ALLOWANCE

    # dp[合計金額] = [ {商品index: 個数}, ... ]
    dp = {0: [dict()]}

    for idx, row in products.iterrows():
        price = int(row["価格"])
        new_dp = {s: combos[:] for s, combos in dp.items()}

        for current_sum, combos in dp.items():
            for combo in combos:
                for qty in range(1, MAX_PER_ITEM + 1):
                    new_sum = current_sum + price * qty
                    if new_sum > max_total:
                        continue

                    new_combo = combo.copy()
                    new_combo[idx] = qty
                    new_dp.setdefault(new_sum, []).append(new_combo)

                    if len(new_dp[new_sum]) > MAX_COMBOS_PER_SUM:
                        new_dp[new_sum] = new_dp[new_sum][:MAX_COMBOS_PER_SUM]

        dp = new_dp

    results = []
    for total, combos in dp.items():
        if min_total <= total <= max_total:
            for combo in combos:
                if not combo:
                    continue
                if not is_category_balanced(combo, products):
                    continue

                diff = total - target_amount
                total_items = sum(combo.values())
                category_count = len({products.loc[idx, "カテゴリ"] for idx in combo})

                results.append({
                    "合計金額": total,
                    "差額": diff,
                    "絶対差額": abs(diff),
                    "総点数": total_items,
                    "カテゴリ数": category_count,
                    "combo": combo,
                })

    # ぴったりに近い順 → 余り優先 → カテゴリ数が多い順 → 点数が少ない順
    results.sort(key=lambda r: (r["絶対差額"], 1 if r["差額"] > 0 else 0, -r["カテゴリ数"], r["総点数"]))
    return results[:TOP_RESULTS]


def combo_to_dataframe(combo, products):
    rows = []
    for idx, qty in combo.items():
        price = int(products.loc[idx, "価格"])
        rows.append({
            "商品名": products.loc[idx, "商品名"],
            "カテゴリ": products.loc[idx, "カテゴリ"],
            "単価": price,
            "個数": qty,
            "小計": price * qty,
        })
    return pd.DataFrame(rows).sort_values(["カテゴリ", "商品名"]).reset_index(drop=True)


def diff_label(diff):
    if diff < 0:
        return f"{abs(diff)}円余り"
    if diff > 0:
        return f"{diff}円オーバー"
    return "ぴったり"


# =========================================================
# Streamlit UI
# =========================================================
st.set_page_config(
    page_title="購買ぴったり使い切りアプリ",
    page_icon="🛒",
    layout="wide"
)

st.title("🛒 購買ぴったり使い切りアプリ")
st.caption("GitHubリポジトリ内の固定CSVから、指定金額に近い組み合わせを探します。")

with st.sidebar:
    st.header("検索条件")
    target_amount = st.number_input("使いたい金額 n 円", min_value=1, value=500, step=10)

    st.divider()
    st.write("固定条件")
    st.write(f"- 同じ商品は最大 **{MAX_PER_ITEM}点**")
    st.write(f"- 余りは **{UNDER_ALLOWANCE}円以内**")
    st.write(f"- オーバーは **{OVER_ALLOWANCE}円まで**")
    st.write(f"- 表示候補数は最大 **{TOP_RESULTS}件**")
    st.write(f"- 2点以上なら原則 **{MIN_DISTINCT_CATEGORIES}カテゴリ以上**")
    st.write(f"- 1カテゴリの割合は **{int(MAX_CATEGORY_SHARE * 100)}%以下**")

try:
    raw_df = read_csv_from_repo(str(PRODUCT_CSV_PATH))
    products = normalize_products(raw_df)

    st.subheader("商品リスト")
    st.caption(f"読み込み元: `{PRODUCT_CSV_PATH}`")
    st.dataframe(products, use_container_width=True, hide_index=True)

    col1, col2, col3 = st.columns(3)
    col1.metric("商品数", len(products))
    col2.metric("カテゴリ数", products["カテゴリ"].nunique())
    col3.metric("価格範囲", f"{products['価格'].min()}〜{products['価格'].max()}円")

    if st.button("組み合わせを探す", type="primary"):
        results = find_combinations(products, int(target_amount))

        st.subheader("検索結果")
        if not results:
            st.warning(
                "条件に合う組み合わせが見つかりませんでした。\n\n"
                "目標金額を変える、商品リストを増やす、またはカテゴリ条件を少しゆるめてください。"
            )
        else:
            summary_rows = []
            for i, r in enumerate(results, start=1):
                summary_rows.append({
                    "候補": i,
                    "合計金額": r["合計金額"],
                    "差額": diff_label(r["差額"]),
                    "総点数": r["総点数"],
                    "カテゴリ数": r["カテゴリ数"],
                })
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

            for i, r in enumerate(results, start=1):
                with st.expander(f"候補{i}: 合計 {r['合計金額']}円 / {diff_label(r['差額'])}", expanded=(i == 1)):
                    detail_df = combo_to_dataframe(r["combo"], products)
                    st.dataframe(detail_df, use_container_width=True, hide_index=True)

                    category_summary = (
                        detail_df.groupby("カテゴリ", as_index=False)["個数"]
                        .sum()
                        .rename(columns={"個数": "カテゴリ別点数"})
                    )
                    st.write("カテゴリ構成")
                    st.dataframe(category_summary, use_container_width=True, hide_index=True)

except Exception as e:
    st.error(f"エラーが発生しました: {e}")
    st.info("GitHubリポジトリ内に `data/priceList.csv` があるか確認してください。")
