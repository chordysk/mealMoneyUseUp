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

# ---------------------------------------------------------
# 商品CSVの読み込み設定
# ---------------------------------------------------------
# A. GitHubリポジトリ内のCSVを読む場合
#    Streamlit Cloudでは、GitHub上のCSVを更新したあと「再デプロイ」されないと
#    アプリ内のローカルファイル data/priceList.csv は古いままになることがあります。
PRODUCT_CSV_PATH = Path("data/priceList.csv")

# B. GitHubのRaw URLから直接CSVを読む場合
#    CSV更新をアプリに反映しやすくしたい場合はこちらがおすすめです。
#    例:
#    PRODUCT_CSV_URL = "https://raw.githubusercontent.com/ユーザー名/リポジトリ名/main/data/priceList.csv"
#    使わない場合は空文字のままでOKです。
PRODUCT_CSV_URL = "https://github.com/chordysk/mealMoneyUseUp/raw/refs/heads/main/data/priceList.csv"

# CSVキャッシュの有効時間 秒。
# 0にするとキャッシュなし。300なら5分ごとに再取得。
CSV_CACHE_TTL_SECONDS = 300

# ショートカット金額ボタン
TARGET_SHORTCUTS = [700, 1400, 2100]
DEFAULT_TARGET_AMOUNT = 700

# カテゴリ偏り防止条件
MIN_ITEMS_FOR_CATEGORY_CHECK = 2
MIN_DISTINCT_CATEGORIES = 2
MAX_CATEGORY_SHARE = 0.70

# 探索の上限。商品数が増えても重くなりすぎないようにするための設定
MAX_COMBOS_PER_SUM = 200

# CSV列名候補
NAME_COLUMNS = ["商品名", "品名", "商品", "name"]
PRICE_COLUMNS = ["価格", "価格（税込み）", "価格(税込み)", "税込価格", "税込み価格", "値段", "price"]
CATEGORY_COLUMNS = ["カテゴリ", "カテゴリー", "分類", "category"]


# =========================================================
# CSV読み込み・整形
# =========================================================
def read_csv_with_encodings(source):
    """ローカルPathまたはURLから、日本語CSVを読み込む。"""
    encodings = ["utf-8-sig", "utf-8", "cp932", "shift_jis"]
    last_error = None

    for enc in encodings:
        try:
            return pd.read_csv(source, encoding=enc)
        except Exception as e:
            last_error = e

    raise ValueError(f"CSVを読み込めませんでした。UTF-8またはShift-JISで保存してください。詳細: {last_error}")


# ttl=0はStreamlitのバージョンによって挙動差が出る可能性があるため、
# キャッシュなしの場合は別関数で読む。
@st.cache_data(ttl=CSV_CACHE_TTL_SECONDS)
def read_csv_cached(source: str, source_type: str):
    """商品CSVをキャッシュ付きで読み込む。source_typeはキャッシュキーを明確にするために使う。"""
    if source_type == "url":
        return read_csv_with_encodings(source)

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(
            f"商品CSVが見つかりません: {path}\n"
            "GitHubリポジトリ内に data/priceList.csv を置いてください。"
        )
    return read_csv_with_encodings(path)


def read_products_source():
    """設定に応じて、Raw URLまたはリポジトリ内CSVから読み込む。"""
    if PRODUCT_CSV_URL.strip():
        return read_csv_cached(PRODUCT_CSV_URL.strip(), "url"), PRODUCT_CSV_URL.strip()

    return read_csv_cached(str(PRODUCT_CSV_PATH), "local"), str(PRODUCT_CSV_PATH)


def pick_column(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    return None


def normalize_products(df):
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
    total_items = sum(combo.values())
    if total_items < MIN_ITEMS_FOR_CATEGORY_CHECK:
        return True

    category_counter = Counter()
    for idx, qty in combo.items():
        category_counter[products.loc[idx, "カテゴリ"]] += qty

    if len(category_counter) < MIN_DISTINCT_CATEGORIES:
        return False

    max_share = max(category_counter.values()) / total_items
    return max_share <= MAX_CATEGORY_SHARE


def shuffle_products_for_search(products):
    """探索前に商品リストの順番をランダム化する。"""
    return products.sample(frac=1).reset_index(drop=True)


# =========================================================
# 組み合わせ探索
# =========================================================
def find_combinations(products, target_amount):
    products = shuffle_products_for_search(products)
    min_total = max(0, target_amount - UNDER_ALLOWANCE)
    max_total = target_amount + OVER_ALLOWANCE
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
                if not combo or not is_category_balanced(combo, products):
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
st.caption("固定CSVから、指定金額に近い組み合わせを探します。")

if "target_amount" not in st.session_state:
    st.session_state.target_amount = DEFAULT_TARGET_AMOUNT

with st.sidebar:
    st.header("検索条件")

    # st.write("ショートカット")
    shortcut_cols = st.columns(len(TARGET_SHORTCUTS))
    for col, amount in zip(shortcut_cols, TARGET_SHORTCUTS):
        if col.button(f"{amount}円", use_container_width=True):
            st.session_state.target_amount = amount

    target_amount = st.number_input(
        "使いたい金額 n 円",
        min_value=1,
        step=10,
        key="target_amount"
    )

    st.divider()
    st.write("固定条件")
    st.write(f"- 同じ商品は最大 **{MAX_PER_ITEM}点**")
    st.write(f"- 余りは **{UNDER_ALLOWANCE}円以内**")
    st.write(f"- オーバーは **{OVER_ALLOWANCE}円まで**")
    st.write(f"- 表示候補数は最大 **{TOP_RESULTS}件**")
    st.write(f"- 2点以上なら原則 **{MIN_DISTINCT_CATEGORIES}カテゴリ以上**")
    st.write(f"- 1カテゴリの割合は **{int(MAX_CATEGORY_SHARE * 100)}%以下**")
    st.write("- 探索前に商品リストを **ランダム化**")


    st.divider()
    st.write("データ更新")
    if st.button("商品CSVを再読み込み", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.caption(f"CSVキャッシュ有効時間: {CSV_CACHE_TTL_SECONDS}秒")

try:
    raw_df, source_label = read_products_source()
    products = normalize_products(raw_df)

    st.subheader("商品リスト")
    st.caption(f"読み込み元: `{source_label}`")
    if PRODUCT_CSV_URL.strip():
        st.info("GitHub Raw URLからCSVを読んでいます。CSV更新後、最大でキャッシュ有効時間ぶん反映が遅れる場合があります。すぐ反映したい場合はサイドバーの『商品CSVを再読み込み』を押してください。")
    else:
        st.warning("リポジトリ内CSVを読んでいます。Streamlit Cloudでは、GitHub上のCSV更新後にアプリの再デプロイが必要になる場合があります。CSVだけ頻繁に更新するなら PRODUCT_CSV_URL にGitHub Raw URLを設定してください。")

    st.dataframe(products, use_container_width=True, hide_index=True)

    col1, col2, col3 = st.columns(3)
    col1.metric("商品数", len(products))
    col2.metric("カテゴリ数", products["カテゴリ"].nunique())
    col3.metric("価格範囲", f"{products['価格'].min()}〜{products['価格'].max()}円")

    if st.button("組み合わせを探す", type="primary"):
        search_products = shuffle_products_for_search(products)
        results = find_combinations(search_products, int(target_amount))

        st.subheader("検索結果")
        st.caption("※ 探索前に商品順をランダム化しているため、同じ金額でも再検索すると候補が変わる場合があります。")

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
                    detail_df = combo_to_dataframe(r["combo"], search_products)
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
    st.info("`data/priceList.csv` があるか、または `PRODUCT_CSV_URL` が正しいか確認してください。")
