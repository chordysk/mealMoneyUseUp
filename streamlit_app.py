import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from time import time_ns
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import streamlit as st

# =========================================================
# 条件設定
# =========================================================
MAX_PER_ITEM = 2
UNDER_ALLOWANCE = 10
OVER_ALLOWANCE = 5
TOP_RESULTS = 20
MAX_PURCHASE_HISTORY = 5

PRODUCT_CSV_PATH = Path("data/priceList.csv")
PRODUCT_CSV_URL = "https://github.com/chordysk/mealMoneyUseUp/raw/refs/heads/main/data/priceList.csv"
CSV_CACHE_TTL_SECONDS = 300
PURCHASE_HISTORY_PATH = Path("data/purchase_history.json")

TARGET_SHORTCUTS = [700, 1400, 2100]
DEFAULT_TARGET_AMOUNT = 700

MIN_ITEMS_FOR_CATEGORY_CHECK = 2
MIN_DISTINCT_CATEGORIES = 2
MAX_CATEGORY_SHARE = 0.70
MAX_COMBOS_PER_SUM = 200

NAME_COLUMNS = ["商品名", "品名", "商品", "name"]
PRICE_COLUMNS = ["価格", "価格（税込み）", "価格(税込み)", "税込価格", "税込み価格", "値段", "price"]
CATEGORY_COLUMNS = ["カテゴリ", "カテゴリー", "分類", "category"]
CALORIE_COLUMNS = ["カロリー", "熱量", "calorie", "calories", "kcal"]
DEFAULT_TARGET_CALORIES = 800


# =========================================================
# CSV読み込み・整形
# =========================================================
def read_csv_with_encodings(source):
    encodings = ["utf-8-sig", "utf-8", "cp932", "shift_jis"]
    last_error = None
    for enc in encodings:
        try:
            return pd.read_csv(source, encoding=enc)
        except Exception as e:
            last_error = e
    raise ValueError(f"CSVを読み込めませんでした。詳細: {last_error}")


@st.cache_data(ttl=CSV_CACHE_TTL_SECONDS)
def read_csv_cached(source: str, source_type: str):
    if source_type == "url":
        return read_csv_with_encodings(source)

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"商品CSVが見つかりません: {path}")
    return read_csv_with_encodings(path)


def add_cache_buster(url: str) -> str:
    """GitHub/CDNの古い応答を避けるため、毎回異なるクエリをURLに付ける。"""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["refresh"] = str(time_ns())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def read_products_source():
    """
    ページ実行のたびにGitHub上のCSVを再取得する。
    列チェックより前に最新CSVを読み込むため、追加直後の「カロリー」列も反映される。
    """
    if PRODUCT_CSV_URL.strip():
        original_url = PRODUCT_CSV_URL.strip()
        live_url = add_cache_buster(original_url)
        # URL読み込みにはst.cache_dataを使わず、毎回ネットワークから取得する。
        return read_csv_with_encodings(live_url), original_url

    # URL未設定時のみ、リポジトリ内のローカルCSVを使用する。
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
    calorie_col = pick_column(df, CALORIE_COLUMNS)

    missing = []
    if name_col is None:
        missing.append("商品名")
    if price_col is None:
        missing.append("価格")
    if category_col is None:
        missing.append("カテゴリ")
    if calorie_col is None:
        missing.append("カロリー")
    if missing:
        raise ValueError("CSVに必要な列がありません: " + ", ".join(missing))

    products = df[[name_col, price_col, category_col, calorie_col]].copy()
    products.columns = ["商品名", "価格", "カテゴリ", "カロリー"]
    products["価格"] = (
        products["価格"].astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("円", "", regex=False)
        .str.strip()
    )
    products["価格"] = pd.to_numeric(products["価格"], errors="coerce")
    products["カロリー"] = (
        products["カロリー"].astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("kcal", "", case=False, regex=False)
        .str.replace("キロカロリー", "", regex=False)
        .str.strip()
    )
    products["カロリー"] = pd.to_numeric(products["カロリー"], errors="coerce")
    products["商品名"] = products["商品名"].astype(str).str.strip()
    products["カテゴリ"] = products["カテゴリ"].astype(str).str.strip()
    products = products.dropna(subset=["商品名", "価格", "カテゴリ", "カロリー"])
    products = products[
        (products["商品名"] != "")
        & (products["カテゴリ"] != "")
        & (products["価格"] > 0)
        & (products["カロリー"] >= 0)
    ]
    products["価格"] = products["価格"].astype(int)
    products["カロリー"] = products["カロリー"].astype(float)
    return products.drop_duplicates(subset=["商品名"], keep="first").reset_index(drop=True)


# =========================================================
# 購入履歴
# =========================================================
def load_purchase_history():
    """購入履歴を最大5回読み込む。読み込めない場合は空にする。"""
    if not PURCHASE_HISTORY_PATH.exists():
        return []
    try:
        with PURCHASE_HISTORY_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data[:MAX_PURCHASE_HISTORY] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_purchase_history(history):
    """一時ファイルを使って購入履歴を安全に保存する。"""
    PURCHASE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    history = history[:MAX_PURCHASE_HISTORY]
    fd, temp_path = tempfile.mkstemp(
        prefix="purchase_history_", suffix=".json", dir=str(PURCHASE_HISTORY_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, PURCHASE_HISTORY_PATH)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def record_purchase(detail_df, target_amount):
    items = detail_df[["商品名", "カテゴリ", "単価", "カロリー", "個数", "小計", "合計カロリー"]].to_dict("records")
    entry = {
        "購入日時": pd.Timestamp.now(tz="Asia/Tokyo").strftime("%Y-%m-%d %H:%M:%S"),
        "設定金額": int(target_amount),
        "合計金額": int(detail_df["小計"].sum()),
        "合計カロリー": float(detail_df["合計カロリー"].sum()),
        "商品": items,
    }
    history = [entry] + st.session_state.purchase_history
    st.session_state.purchase_history = history[:MAX_PURCHASE_HISTORY]
    try:
        save_purchase_history(st.session_state.purchase_history)
        return True, "購入履歴に保存しました。"
    except OSError as e:
        return False, f"このセッションには保存しましたが、ファイル保存に失敗しました: {e}"


def purchased_product_names(history):
    names = []
    for purchase in history:
        for item in purchase.get("商品", []):
            name = item.get("商品名")
            if name and name not in names:
                names.append(name)
    return names


# =========================================================
# 探索処理
# =========================================================
def is_detail_category_balanced(detail_df):
    total_items = int(detail_df["個数"].sum())
    if total_items < MIN_ITEMS_FOR_CATEGORY_CHECK:
        return True
    counts = detail_df.groupby("カテゴリ")["個数"].sum()
    if len(counts) < MIN_DISTINCT_CATEGORIES:
        return False
    return float(counts.max() / total_items) <= MAX_CATEGORY_SHARE


def shuffle_products_for_search(products):
    return products.sample(frac=1).reset_index(drop=True)


def combo_to_dataframe(combo, products):
    rows = []
    for idx, qty in combo.items():
        price = int(products.loc[idx, "価格"])
        calorie = float(products.loc[idx, "カロリー"])
        rows.append({
            "商品名": products.loc[idx, "商品名"],
            "カテゴリ": products.loc[idx, "カテゴリ"],
            "単価": price,
            "カロリー": calorie,
            "個数": int(qty),
            "小計": price * int(qty),
            "合計カロリー": calorie * int(qty),
        })
    if not rows:
        return pd.DataFrame(columns=["商品名", "カテゴリ", "単価", "カロリー", "個数", "小計", "合計カロリー"])
    return pd.DataFrame(rows)


def merge_required_items(combo_df, required_df):
    frames = [df for df in [combo_df, required_df] if not df.empty]
    if not frames:
        return pd.DataFrame(columns=["商品名", "カテゴリ", "単価", "カロリー", "個数", "小計", "合計カロリー"])
    merged = pd.concat(frames, ignore_index=True)
    merged = (
        merged.groupby(["商品名", "カテゴリ", "単価", "カロリー"], as_index=False)["個数"]
        .sum()
    )
    merged["小計"] = merged["単価"] * merged["個数"]
    merged["合計カロリー"] = merged["カロリー"] * merged["個数"]
    return merged.sort_values(["カテゴリ", "商品名"]).reset_index(drop=True)


def find_combinations(products, remaining_target, original_target, required_df):
    """必須商品の金額を差し引いた残額について組み合わせを探索する。"""
    min_total = max(0, remaining_target - UNDER_ALLOWANCE)
    max_total = remaining_target + OVER_ALLOWANCE
    dp = {0: [dict()]}

    for idx, row in products.iterrows():
        price = int(row["価格"])
        new_dp = {total: combos[:] for total, combos in dp.items()}
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

    required_total = int(required_df["小計"].sum()) if not required_df.empty else 0
    results = []
    for remaining_total, combos in dp.items():
        if not (min_total <= remaining_total <= max_total):
            continue
        for combo in combos:
            # 残額0円の場合は空の組み合わせも有効。必須商品だけで候補を作れる。
            if not combo and required_df.empty:
                continue
            combo_df = combo_to_dataframe(combo, products)
            detail_df = merge_required_items(combo_df, required_df)
            if not is_detail_category_balanced(detail_df):
                continue

            full_total = remaining_total + required_total
            diff = full_total - original_target
            results.append({
                "合計金額": full_total,
                "差額": diff,
                "絶対差額": abs(diff),
                "総点数": int(detail_df["個数"].sum()),
                "カテゴリ数": int(detail_df["カテゴリ"].nunique()),
                "合計カロリー": float(detail_df["合計カロリー"].sum()),
                "detail": detail_df.to_dict("records"),
            })

    results.sort(
        key=lambda r: (
            r["絶対差額"],
            1 if r["差額"] > 0 else 0,
            -r["カテゴリ数"],
            r["総点数"],
        )
    )
    return results[:TOP_RESULTS]


def diff_label(diff):
    if diff < 0:
        return f"{abs(diff)}円余り"
    if diff > 0:
        return f"{diff}円オーバー"
    return "ぴったり"


# =========================================================
# Streamlit UI
# =========================================================
st.set_page_config(page_title="購買ぴったり使い切りアプリ", page_icon="🛒", layout="wide")
st.title("🛒 購買ぴったり使い切りアプリ")
st.caption("ページ実行時にGitHubの最新CSVを取得します。必須商品・除外商品・直近5回の購入履歴にも対応しています。")

if "target_amount" not in st.session_state:
    st.session_state.target_amount = DEFAULT_TARGET_AMOUNT
if "purchase_history" not in st.session_state:
    st.session_state.purchase_history = load_purchase_history()
if "search_results" not in st.session_state:
    st.session_state.search_results = []
if "search_target" not in st.session_state:
    st.session_state.search_target = None
if "target_calories" not in st.session_state:
    st.session_state.target_calories = DEFAULT_TARGET_CALORIES

try:
    raw_df, source_label = read_products_source()
    products = normalize_products(raw_df)
    all_product_names = products["商品名"].tolist()
    history_names = purchased_product_names(st.session_state.purchase_history)

    with st.sidebar:
        st.header("検索条件")
        shortcut_cols = st.columns(len(TARGET_SHORTCUTS))
        for col, amount in zip(shortcut_cols, TARGET_SHORTCUTS):
            if col.button(f"{amount}円", use_container_width=True):
                st.session_state.target_amount = amount

        target_amount = st.number_input(
            "使いたい金額 n 円", min_value=1, step=10, key="target_amount"
        )

        st.subheader("カロリー設定")
        target_calories = st.number_input(
            "目標カロリー（kcal）",
            min_value=0,
            step=50,
            key="target_calories",
            help="『目標カロリーに近い順』で候補を並べるときに使用します。",
        )
        calorie_sort = st.selectbox(
            "候補の並び替え",
            options=["合計カロリー高い順", "合計カロリー低い順", "目標カロリーに近い順"],
            index=2,
        )

        st.divider()
        st.subheader("必須購入商品")
        required_names = st.multiselect(
            "必ず1点購入する商品",
            options=all_product_names,
            default=[],
            help="選んだ商品は1点ずつ必須にし、その金額を目標金額から先に差し引きます。",
        )

        st.subheader("除外商品")
        history_excluded = st.multiselect(
            "購入済み商品から除外",
            options=history_names,
            default=[],
            help="直近5回の購入履歴に含まれる商品だけを表示しています。",
        )
        all_excluded = st.multiselect(
            "全商品リストから除外",
            options=all_product_names,
            default=[],
        )
        excluded_names = sorted(set(history_excluded) | set(all_excluded))

        overlap = sorted(set(required_names) & set(excluded_names))
        if overlap:
            st.error("必須商品と除外商品が重複しています: " + "、".join(overlap))

        st.divider()
        st.write("データ更新")
        if st.button("商品CSVを再読み込み", use_container_width=True):
            st.cache_data.clear()
            st.session_state.search_results = []
            st.rerun()

    # 必須商品の明細を1点ずつ作る
    required_df = products[products["商品名"].isin(required_names)].copy()
    required_df = required_df.rename(columns={"価格": "単価"})
    if not required_df.empty:
        required_df["個数"] = 1
        required_df["小計"] = required_df["単価"]
        required_df["合計カロリー"] = required_df["カロリー"]
        required_df = required_df[["商品名", "カテゴリ", "単価", "カロリー", "個数", "小計", "合計カロリー"]]
    else:
        required_df = pd.DataFrame(columns=["商品名", "カテゴリ", "単価", "カロリー", "個数", "小計", "合計カロリー"])

    required_total = int(required_df["小計"].sum()) if not required_df.empty else 0
    remaining_target = int(target_amount) - required_total

    st.subheader("現在の指定")
    c1, c2, c3 = st.columns(3)
    c1.metric("使いたい金額", f"{int(target_amount)}円")
    c2.metric("必須商品の合計", f"{required_total}円")
    c3.metric("残りの探索金額", f"{remaining_target}円")

    if required_names:
        st.write("**必須購入商品**")
        st.dataframe(required_df, use_container_width=True, hide_index=True)
    if excluded_names:
        st.write("**除外中の商品:** " + "、".join(excluded_names))

    search_disabled = bool(overlap) or remaining_target < 0
    if remaining_target < 0:
        st.error("必須商品の合計が、使いたい金額を超えています。")

    if st.button("組み合わせを探す", type="primary", disabled=search_disabled):
        # 必須商品と除外商品は、探索対象から完全に外す。
        blocked_names = set(required_names) | set(excluded_names)
        search_pool = products[~products["商品名"].isin(blocked_names)].copy()
        search_pool = shuffle_products_for_search(search_pool)

        st.session_state.search_results = find_combinations(
            search_pool,
            remaining_target=remaining_target,
            original_target=int(target_amount),
            required_df=required_df,
        )
        st.session_state.search_target = int(target_amount)

    if st.session_state.search_results:
        st.subheader("購入商品候補")
        st.caption("必須商品は各候補に追加済みです。購入すると、候補全体を直近5回の履歴に保存します。")

        displayed_results = list(st.session_state.search_results)
        if calorie_sort == "合計カロリー高い順":
            displayed_results.sort(key=lambda r: r["合計カロリー"], reverse=True)
        elif calorie_sort == "合計カロリー低い順":
            displayed_results.sort(key=lambda r: r["合計カロリー"])
        else:
            displayed_results.sort(
                key=lambda r: (abs(r["合計カロリー"] - float(target_calories)), r["絶対差額"])
            )

        st.caption(f"並び替え: {calorie_sort} / 目標 {float(target_calories):g} kcal")

        for i, result in enumerate(displayed_results, start=1):
            detail_df = pd.DataFrame(result["detail"])
            with st.container(border=True):
                st.markdown(
                    f"### 候補{i}: 合計 {result['合計金額']}円 / "
                    f"{diff_label(result['差額'])} / {result['合計カロリー']:g} kcal"
                )
                st.dataframe(detail_df, use_container_width=True, hide_index=True)

                button_col, info_col = st.columns([1, 4])
                if button_col.button("購入", key=f"purchase_{i}", type="primary", use_container_width=True):
                    success, message = record_purchase(
                        detail_df,
                        st.session_state.search_target or int(target_amount),
                    )
                    if success:
                        st.success(message)
                    else:
                        st.warning(message)
                    st.session_state.search_results = []
                    st.rerun()
                info_col.caption(
                    f"{result['総点数']}点・{result['カテゴリ数']}カテゴリ・"
                    f"目標カロリーとの差 {abs(result['合計カロリー'] - float(target_calories)):g} kcal"
                )
    elif st.session_state.search_target is not None:
        st.info("検索結果がありません。条件を変更して再検索してください。")

    st.divider()
    st.subheader("直近5回の購入履歴")
    if not st.session_state.purchase_history:
        st.info("購入履歴はまだありません。候補の「購入」ボタンから登録できます。")
    else:
        for i, purchase in enumerate(st.session_state.purchase_history, start=1):
            title = (
                f"{i}. {purchase.get('購入日時', '日時不明')} / "
                f"合計 {purchase.get('合計金額', 0)}円 / {purchase.get('合計カロリー', 0):g} kcal"
            )
            with st.expander(title, expanded=(i == 1)):
                st.dataframe(pd.DataFrame(purchase.get("商品", [])), use_container_width=True, hide_index=True)

        if st.button("購入履歴をすべて削除"):
            st.session_state.purchase_history = []
            try:
                save_purchase_history([])
            except OSError:
                pass
            st.rerun()

    with st.expander("商品リストを確認"):
        st.caption(f"読み込み元: {source_label}")
        st.dataframe(products, use_container_width=True, hide_index=True)

except Exception as e:
    st.error(f"エラーが発生しました: {e}")
    st.info("商品CSVのURL、列名、文字コードを確認してください。")
