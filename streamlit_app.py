# app.py
import os, re, json, requests
import pandas as pd
import altair as alt  # 차트는 안 쓰지만 유지 가능
import streamlit as st
from datetime import datetime
from requests.adapters import HTTPAdapter, Retry

st.set_page_config(page_title="Duty-free Promo Planner", layout="wide")

# -----------------------------
# Config  (수정 금지)
# -----------------------------
DEFAULT_WEBHOOK = os.environ.get(
    "N8N_WEBHOOK_URL",
    "https://hyunji6.app.n8n.cloud/webhook-test/8d05730a-0bc0-48d0-b580-c268a1b753ce"
)

# -----------------------------
# Light styling 💅
# -----------------------------
st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
.section-title{
  font-weight: 800; font-size: 1.15rem; letter-spacing: .2px;
  display:inline-block; padding: .35rem .7rem; border-radius: 999px;
  background: linear-gradient(90deg, #eef3ff, #edf9ff);
  border: 1px solid #e6efff; margin-bottom: .6rem;
}
.card { border-radius: 16px; padding: 14px 16px; margin-bottom: 10px;
  border: 1px solid rgba(0,0,0,.06); background: #ffffffaa; box-shadow: 0 2px 10px rgba(0,0,0,.04);}
.glass { background: linear-gradient(180deg, rgba(255,255,255,.85), rgba(255,255,255,.65)); backdrop-filter: blur(4px); }
.hr {height:1px; background:linear-gradient(90deg,#00000012,transparent); margin: 16px 0;}
.kicker {font-size:.84rem; color:#6c7aa0; font-weight:600; letter-spacing:.3px;}
button[kind="secondary"] {border-radius: 12px !important;}
.pills {display:flex; flex-wrap:wrap; gap:.4rem; margin:.25rem 0 .6rem 0;}
.pill {
  display:inline-block; padding:.24rem .55rem; border-radius:999px; font-weight:800;
  font-size:.82rem; border:1px solid #e8eefc; color:#334155;
  background: linear-gradient(180deg,#f5faff,#f0f8ff);
}
.pill:nth-child(4n+1){ background:linear-gradient(180deg,#fef9f5,#fff4ea); border-color:#fde7cf; }
.pill:nth-child(4n+2){ background:linear-gradient(180deg,#f5fff7,#ecffef); border-color:#d9f7de; }
.pill:nth-child(4n+3){ background:linear-gradient(180deg,#fef6ff,#f9ecff); border-color:#f0d9ff; }
.smallnote {font-size:.85rem; color:#64748b;}
.theme-badge {
  display:inline-block; padding:.4rem .9rem; border-radius:999px; font-weight:800;
  font-size:.9rem; border:1px solid #d0ebff; color:#0b7285; background:#e7f5ff;
  margin-right:.4rem; margin-bottom:.4rem;
}
</style>
""", unsafe_allow_html=True)

# -----------------------------
# Utils
# -----------------------------
def extract_yyyy_mm(text: str, default_ym: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return default_ym
    yyyy, mm = default_ym.split("-")
    m1 = re.search(r"(20\d{2})[-/.]?(0[1-9]|1[0-2])", text)
    m2 = re.search(r"(0?[1-9]|1[0-2])\s*월", text)
    y_only = re.search(r"(20\d{2})", text)
    if m1: yyyy, mm = m1.group(1), m1.group(2)
    elif m2: mm = f"{int(m2.group(1)):02d}"
    if y_only: yyyy = y_only.group(1)
    return f"{yyyy}-{mm}"

def _as_dict(obj):
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        s = obj.strip()
        try:
            return _as_dict(json.loads(s))
        except Exception:
            m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.I)
            if m:
                try:
                    return _as_dict(json.loads(m.group(1).strip()))
                except Exception:
                    pass
            return {}
    if isinstance(obj, list):
        out = {}
        for it in obj:
            d = it.get("json", it) if isinstance(it, dict) else {}
            for k in [
                "reply","search_data","calendar","promotions",
                "search_data_raw","calendar_raw","catalog_raw",
                "recommended_products_by_region","restock_alerts"
            ]:
                if k in d and k not in out:
                    out[k] = d[k]
            if isinstance(d.get("ats"), dict):
                out["ats"] = d["ats"]
            if "promotions_by_region" in d:
                out["promotions_by_region"] = d["promotions_by_region"]
        return out
    return {}

def normalize_payload(obj):
    d = _as_dict(obj)
    ats = d.get("ats") or {}
    if isinstance(ats, str):
        try:
            ats = json.loads(ats)
        except Exception:
            ats = {}
    regions = ats.get("regions")
    if isinstance(regions, dict):
        regions = [{"region": k, **(v if isinstance(v, dict) else {})} for k, v in regions.items()]
    if not isinstance(regions, list):
        regions = []

    calendar = d.get("calendar_raw")
    if not isinstance(calendar, list):
        calendar = d.get("calendar") if isinstance(d.get("calendar"), list) else []
    search_data = d.get("search_data_raw")
    if not isinstance(search_data, list):
        search_data = d.get("search_data") if isinstance(d.get("search_data"), list) else []

    catalog = d.get("catalog_raw") if isinstance(d.get("catalog_raw"), list) else []
    rec_by_region = d.get("recommended_products_by_region") if isinstance(d.get("recommended_products_by_region"), list) else []
    restock_alerts = d.get("restock_alerts") if isinstance(d.get("restock_alerts"), list) else []

    return {
        "reply": d.get("reply") or "",
        "search_data": search_data,
        "calendar": calendar,
        "catalog_raw": catalog,
        "recommended_products_by_region": rec_by_region,
        "restock_alerts": restock_alerts,
        "promotions": [],
        "promotions_by_region": d.get("promotions_by_region") if isinstance(d.get("promotions_by_region"), list) else [],
        "ats": {"month": (ats.get("month") or ""), "regions": regions},
        "_raw": d,
    }

def flag(region: str) -> str:
    return {"KR":"🎎","CN":"🐉","JP":"🎌","SEA":"🌴"}.get(region, "🏳️")

def _as_list(x):
    if x is None:
        return []
    if isinstance(x, (list, tuple, set)):
        return [str(i).strip() for i in x if str(i).strip()]
    parts = re.split(r"[•·;\n]|,\s*", str(x))
    return [p.strip() for p in parts if p and p.strip()]

def render_hashtag_pills(tags):
    tags = [t.strip() for t in (tags or []) if str(t).strip()]
    if not tags:
        return
    def _fmt(t): return t if str(t).startswith("#") else "#"+str(t)
    html = '<div class="pills">' + "".join([f'<span class="pill">{_fmt(t)}</span>' for t in tags]) + "</div>"
    st.markdown(html, unsafe_allow_html=True)

def skeleton_holidays(region="KR"):
    with st.container(border=True):
        st.markdown(f"**{flag(region)} {region}**")
        st.write("• —\n• —\n• —")

def skeleton_search_topN(n=10):
    df = pd.DataFrame({"keyword": [f"검색어 {i}" for i in range(n, 0, -1)], "rank": list(range(1,n+1)), "search_volume":[0]*n})
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("데이터 로딩 전 미리보기")

# -----------------------------
# n8n caller
# -----------------------------
def call_n8n(webhook: str, month_ym: str):
    if not webhook:
        raise RuntimeError("Webhook URL이 설정되지 않았습니다.")
    payload = {
        "content": f"{month_ym} 프로모션 추천",
        "month": month_ym,
        "year": month_ym.split("-")[0],
        "chat_history": []
    }
    sess = requests.Session()
    retry = Retry(
        total=2, connect=2, read=2, backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=["POST"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter); sess.mount("http://", adapter)

    r = sess.post(webhook, json=payload, timeout=(5, 120))
    r.raise_for_status()
    try:
        parsed = r.json()
    except Exception:
        parsed = r.text
    return normalize_payload(parsed)

@st.cache_data(ttl=600, show_spinner=False)
def fetch_cached(webhook: str, month_ym: str):
    return call_n8n(webhook, month_ym)

# -----------------------------
# State
# -----------------------------
if "data" not in st.session_state:
    st.session_state.data = None
if "ym" not in st.session_state:                   # 실제 서버에서 받은 월(표시용/헤더용)
    st.session_state.ym = None
if "selected_ym" not in st.session_state:         # 사용자가 입력한 목표 월
    st.session_state.selected_ym = datetime.today().strftime("%Y-%m")
if "region" not in st.session_state:
    st.session_state.region = "KR"
if "last_input_ym" not in st.session_state:
    st.session_state.last_input_ym = None
if "sb_open" not in st.session_state:
    st.session_state.sb_open = True  # 사이드바 기본 열림

# -----------------------------
# Sidebar toggle 버튼 (헤더 우측)
# -----------------------------
header_l, header_c, header_r = st.columns([1, 6, 1])
with header_l:
    st.markdown("## 📊 프로모션 자동 기획 대시보드")
with header_r:
    if st.button("🧰 필터 토글", use_container_width=True):
        st.session_state.sb_open = not st.session_state.sb_open

# 사이드바 열림/닫힘 CSS
if st.session_state.sb_open:
    st.markdown("""
    <style>
    [data-testid="stSidebar"]{
        width: 320px;
        min-width: 320px;
        transition: transform 300ms ease-in-out, margin-left 300ms ease-in-out;
    }
    </style>
    """, unsafe_allow_html=True)
else:
    st.markdown("""
    <style>
    [data-testid="stSidebar"]{
        transform: translateX(-360px);
        margin-left: -360px;
        width: 0 !important;
        min-width: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        transition: transform 300ms ease-in-out, margin-left 300ms ease-in-out;
    }
    .block-container {padding-left: 1rem; padding-right: 1rem;}
    </style>
    """, unsafe_allow_html=True)

# -----------------------------
# Sidebar (필터/동작)
# -----------------------------
def render_sidebar():
    with st.sidebar:
        st.markdown('<span class="section-title">⚙️ 분석 옵션</span>', unsafe_allow_html=True)

        # ✅ 월 입력 → 즉시 session_state.selected_ym 동기화
        default_ym = datetime.today().strftime("%Y-%m")
        month_input = st.text_input("월 선택", value=st.session_state.selected_ym, placeholder="예) 2025-12 또는 12월")
        st.session_state.selected_ym = extract_yyyy_mm(month_input or "", default_ym)

        # 국가 선택
        st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
        st.markdown('<span class="kicker">국가 선택</span>', unsafe_allow_html=True)
        region_map = {"KR": f"{flag('KR')} KR", "JP": f"{flag('JP')} JP", "CN": f"{flag('CN')} CN", "SEA": f"{flag('SEA')} SEA"}
        choice = st.radio(" ", list(region_map.keys()), index=list(region_map.keys()).index(st.session_state.region),
                          format_func=lambda k: region_map[k], horizontal=False, label_visibility="collapsed")
        st.session_state.region = choice
        st.caption(f"현재 선택: {flag(st.session_state.region)} {st.session_state.region}")

        st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
        # 수동 갱신 버튼
        if st.button("데이터 불러오기", use_container_width=True):
            try:
                with st.status("데이터 불러오는 중...", expanded=False) as s:
                    res = fetch_cached(DEFAULT_WEBHOOK, st.session_state.selected_ym)
                    st.session_state.data = res
                    st.session_state.ym = st.session_state.selected_ym
                    st.session_state.last_input_ym = st.session_state.selected_ym
                    s.update(label="완료", state="complete")
            except requests.exceptions.ReadTimeout:
                st.error("요청이 시간 초과되었습니다. 다시 시도해주세요.")
            except Exception as e:
                st.error(f"데이터 수집 실패: {e}")

        # ✅ 입력 월 변경 자동 재요청
        if st.session_state.last_input_ym != st.session_state.selected_ym:
            try:
                res = fetch_cached(DEFAULT_WEBHOOK, st.session_state.selected_ym)
                st.session_state.data = res
                st.session_state.ym = st.session_state.selected_ym
                st.session_state.last_input_ym = st.session_state.selected_ym
                st.caption("자동 새로고침: 입력 월 변경 감지")
            except Exception:
                pass

# 사이드바가 열려 있을 때만 렌더(값은 session_state로 유지)
if st.session_state.sb_open:
    render_sidebar()

# -----------------------------
# Main (본문)
# -----------------------------
ym = st.session_state.ym or st.session_state.selected_ym  # ✅ 헤더는 선택 월 우선
data = st.session_state.data or {}
rg = st.session_state.region

# 헤더
st.markdown(f'<div class="kicker">대상 월</div><h3 style="margin-top:.2rem;">{ym}</h3>', unsafe_allow_html=True)
st.markdown(f'<div class="kicker">국가</div><h4 style="margin-top:.2rem;">{flag(rg)} {rg}</h4>', unsafe_allow_html=True)
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

# ============ 1행(3열): NOW TREND | 연휴상황 | 인기검색어 Top10 ============
col_now, col_holiday, col_search = st.columns([2, 1, 1], gap="large")

ats = data.get("ats") or {}
regions = ats.get("regions") or []
def pick_region_info(code):
    for r in regions:
        if r.get("region") == code:
            return r
    return {"region": code}
info = pick_region_info(rg)

# 1-1) NOW TREND
with col_now:
    st.markdown('<span class="section-title">🌏 NOW TREND</span>', unsafe_allow_html=True)
    hs = (info.get("hashtags") or {})

    macro_txt   = info.get("macro_issue")
    shop_txt    = info.get("shopping_trend")
    cons_txt    = info.get("consumer_behavior")
    travel_txt  = info.get("travel_leisure")
    brand_txt   = info.get("brand_highlight")
    promo_txt   = info.get("promotion_implication")

    st.write("**🌤️ 주요 이슈**")
    render_hashtag_pills(hs.get("macro_issue"))
    items = _as_list(macro_txt); st.write("\n".join([f"- {it}" for it in items]) if items else (f"- {macro_txt}" if (macro_txt or "").strip() else ""))

    st.write("**🛍️ 쇼핑 트렌드**")
    render_hashtag_pills(hs.get("shopping_trend"))
    items = _as_list(shop_txt); st.write("\n".join([f"- {it}" for it in items]) if items else (f"- {shop_txt}" if (shop_txt or "").strip() else ""))

    st.write("**👥 소비자 행동**")
    render_hashtag_pills(hs.get("consumer_behavior"))
    items = _as_list(cons_txt); st.write("\n".join([f"- {it}" for it in items]) if items else (f"- {cons_txt}" if (cons_txt or "").strip() else ""))

    st.write("**✈️ 여행·레저**")
    render_hashtag_pills(hs.get("travel_leisure"))
    items = _as_list(travel_txt); st.write("\n".join([f"- {it}" for it in items]) if items else (f"- {travel_txt}" if (travel_txt or "").strip() else ""))

    st.write("**🏷️ 카테고리/브랜드**")
    render_hashtag_pills(hs.get("brand_highlight"))
    items = _as_list(brand_txt); st.write("\n".join([f"- {it}" for it in items]) if items else (f"- {brand_txt}" if (brand_txt or "").strip() else ""))

    st.write("**🎯 프로모션 시사점**")
    render_hashtag_pills(hs.get("promotion_implication"))
    items = _as_list(promo_txt); st.write("\n".join([f"- {it}" for it in items]) if items else (f"- {promo_txt}" if (promo_txt or "").strip() else ""))

# 1-2) 연휴상황
with col_holiday:
    st.markdown('<span class="section-title">🗓️ 연휴 상황</span>', unsafe_allow_html=True)
    cal = data.get("calendar") or []
    names = []
    if cal:
        cal_df = pd.DataFrame(cal)
        try:
            month_int = int(ym.split("-")[1])
        except Exception:
            month_int = None

        if "date" in cal_df.columns and pd.api.types.is_numeric_dtype(cal_df["date"]):
            tmp = cal_df[cal_df["date"].astype("Int64") == month_int].copy() if month_int else cal_df.head(0)
        else:
            cal_df["date_dt"] = pd.to_datetime(cal_df["date"], errors="coerce")
            cal_df["mm"] = cal_df["date_dt"].dt.month
            tmp = cal_df[cal_df["mm"] == month_int].copy() if month_int else cal_df.head(0)

        if "country" in tmp.columns and "name" in tmp.columns:
            names = tmp[tmp["country"] == rg]["name"].dropna().astype(str).unique().tolist()

    if not names:
        skeleton_holidays(rg)
    else:
        with st.container(border=True):
            st.markdown(f"**{flag(rg)} {rg}**")
            st.write("• " + "\n• ".join(names))

# 1-3) 인기검색어 Top10
with col_search:
    st.markdown('<span class="section-title">🔎 인기검색어 Top 10</span>', unsafe_allow_html=True)
    def get_search_topN_df(data_dict, ym_str, topn=10):
        df = pd.DataFrame(data_dict.get("search_data") or [])
        if df.empty:
            return df
        df = df.rename(columns={c: str(c).strip().lower() for c in df.columns})
        if "month" not in df.columns: df["month"] = None
        if "rank" not in df.columns: df["rank"] = None
        if "keyword" not in df.columns:
            key_col = next((c for c in df.columns if "key" in c), None)
            df["keyword"] = df.get(key_col, "")
        if "search_volume" in df.columns:
            df["search_value"] = pd.to_numeric(df["search_volume"], errors="coerce").fillna(0)
        else:
            df["search_value"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)

        df["rank"] = pd.to_numeric(df["rank"], errors="coerce").fillna(999).astype(int)

        def _mm_any(x):
            if x is None: return None
            if isinstance(x, (int, float)):
                mm = int(x);  return mm if 1 <= mm <= 12 else None
            t = str(x).strip()
            if not t: return None
            m = re.search(r"20\d{2}[-/\.]?(\d{1,2})", t)
            if m:
                mm = int(m.group(1));  return mm if 1 <= mm <= 12 else None
            m2 = re.fullmatch(r"(\d{1,2})", t)
            if m2:
                mm = int(m2.group(1));  return mm if 1 <= mm <= 12 else None
            m3 = re.search(r"(\d{1,2})\s*월", t)
            if m3:
                mm = int(m3.group(1));  return mm if 1 <= mm <= 12 else None
            return None

        try:
            req_mm = int(str(ym_str).split("-")[1])
        except Exception:
            req_mm = None

        if req_mm is not None:
            df = df[df["month"].apply(lambda x: _mm_any(x) == req_mm)]
        if df.empty:
            return df

        if (df["rank"] < 999).any():
            df = df.sort_values(["rank", "search_value"], ascending=[True, False])
        else:
            df = df.sort_values("search_value", ascending=False)

        return df.head(topn)[["keyword","rank","search_value"]]

    s_df = get_search_topN_df(data, ym, topn=10)
    if s_df.empty:
        skeleton_search_topN(10)
    else:
        st.dataframe(
            s_df.rename(columns={"keyword":"keyword","rank":"rank","search_value":"search_value"}),
            use_container_width=True, hide_index=True
        )

st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

# =========================
# 2행: 🛒 프로모션 컨셉&상품추천 (테마 4개 + 테마별 5개)
# =========================
st.markdown('<span class="section-title">🛒 프로모션 컨셉&상품추천</span>', unsafe_allow_html=True)
region_blocks = data.get("promotions_by_region") or []
region_promo = next((b for b in region_blocks if b.get("region") == st.session_state.region), None)
promo_items = (region_promo or {}).get("items") or []

def get_rec_items(data_dict, region_code):
    blocks = data_dict.get("recommended_products_by_region") or []
    for b in blocks:
        if b.get("region") == region_code:
            return b.get("items") or []
    return []
rec_items_all = get_rec_items(data, st.session_state.region)

def score_total(it):
    sc = it.get("scores") or {}
    v = sc.get("final", sc.get("total"))
    try:
        return float(v)
    except Exception:
        return None
rec_sorted_all = sorted(rec_items_all, key=lambda x: (score_total(x) or -1), reverse=True)

seen_theme = set()
themes = []
for it in promo_items:
    th = (it.get("theme") or "").strip()
    if not th or th in seen_theme:
        continue
    themes.append(it)
    seen_theme.add(th)
    if len(themes) >= 4:
        break

if not themes:
    st.caption("프로모션 컨셉 추천 데이터가 없습니다.")
else:
    st.markdown("".join([f'<span class="theme-badge">{(t.get("theme") or "테마")}</span>' for t in themes]), unsafe_allow_html=True)

    used_skus = set()
    for t in themes:
        theme_txt = (t.get("theme") or "").lower()
        prod_keywords = [str(x).lower() for x in (t.get("products") or []) if str(x).strip()]

        tokens = []
        if theme_txt:
            tokens += re.split(r"[\s,\/\|·•]+", theme_txt)
        tokens += prod_keywords
        tokens = [w.strip() for w in tokens if w.strip()]
        tokens = list(dict.fromkeys(tokens))

        def _match_score(item):
            txt = f"{str(item.get('name','')).lower()} {str(item.get('category','')).lower()}"
            hit = 0
            for w in tokens:
                if w and w in txt:
                    hit += 1
            base = score_total(item) or 0
            return (hit, base)

        pool = [x for x in rec_sorted_all if x.get("sku") not in used_skus]
        matched = sorted(pool, key=lambda x: _match_score(x), reverse=True)
        top5 = matched[:5] if matched else rec_sorted_all[:5]
        for it in top5:
            if it.get("sku"):
                used_skus.add(it["sku"])

        rows = []
        for it in top5:
            rows.append({
                "상품명": it.get("name"),
                "카테고리": it.get("category"),
                "재고": it.get("stock"),
                "score_total": score_total(it),
                "suggested_mechanic": it.get("suggested_mechanic"),
            })
        df_view = pd.DataFrame(rows)
        if "score_total" in df_view.columns:
            df_view["score_total"] = pd.to_numeric(df_view["score_total"], errors="coerce")
            df_view = df_view.sort_values("score_total", ascending=False)

        st.markdown(f"**• {t.get('theme','테마')}**")
        st.dataframe(df_view.reset_index(drop=True), use_container_width=True, hide_index=True)

# Raw JSON
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
with st.expander("🔎 Raw JSON"):
    st.code(json.dumps(data or {}, ensure_ascii=False, indent=2))
