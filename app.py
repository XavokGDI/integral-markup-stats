# -*- coding: utf-8 -*-
"""
Интегральная метрика браузерных страниц — статистика разметок (v6).
Запуск: streamlit run app.py
Зависимости: pip install streamlit pandas openpyxl matplotlib
"""
import io
import json
import re
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

st.set_page_config(page_title="Интегральная метрика: статистика разметок", layout="wide")

# --- Компактная тёмная тема интерфейса ---
st.markdown("""
<style>
    /* Уменьшаем вертикальные отступы между блоками */
    .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 1400px; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { padding: 6px 16px; font-size: 0.95em; }
    h1 { font-size: 1.6rem; margin-bottom: 0.3rem; }
    h2, h3 { font-size: 1.05rem; margin: 0.4rem 0 0.2rem 0; }
    .stMetric { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px; padding: 6px 10px; }
    [data-testid="stMetricValue"] { font-size: 1.15rem; }
    [data-testid="stMetricLabel"] { font-size: 0.75rem; }
    /* Компактные таблицы */
    [data-testid="stDataFrame"] { font-size: 0.8rem; }
    .stDownloadButton > button { font-size: 0.85rem; padding: 4px 14px; }
    .stCaption, [data-testid="stCaptionContainer"] { font-size: 0.75rem; }
    /* Меньше отступов у графиков */
</style>
""", unsafe_allow_html=True)

st.title("Интегральная метрика браузерных страниц — статистика разметок")

BASE_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
               "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080",
               "#9a6324", "#800000", "#808000", "#000075", "#a9a9a9"]

VERDICT_COLORS = {"Да": "#3cb44b", "Нет": "#e6194b", "Не уверен": "#f58231"}
ERROR_TYPE_COLORS = {"rendering": "#f58231", "translation": "#4363d8"}


# ===================== УТИЛИТЫ =====================

def is_dark_theme():
    """Определяет тему интерфейса Streamlit."""
    try:
        return st.get_option("theme.base") == "dark"
    except Exception:
        return False


def apply_theme(ax, title):
    """Оформление осей matplotlib в цвет темы интерфейса."""
    ax.set_title(title, fontsize=10)
    if is_dark_theme():
        text_color = "#e0e0e0"
        ax.set_facecolor("#262730")
        ax.tick_params(colors=text_color, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#555555")
    else:
        ax.tick_params(labelsize=8)


import plotly.express as px
import plotly.graph_objects as go


def colored_bar(series, colors, title, small=False):
    """Интерактивная столбчатая диаграмма с индивидуальным цветом на категорию."""
    counts = series.value_counts()
    cols = [colors.get(c, "#808080") for c in counts.index]
    fig = go.Figure(go.Bar(
        x=list(counts.index),
        y=counts.values,
        marker_color=cols,
        text=counts.values,            # цифры прямо на колонках
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>Количество: %{y}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13)),
        margin=dict(l=10, r=10, t=35, b=10),   # без пустых полей
        height=280 if small else 340,
        xaxis_tickangle=-30,
        bargap=0.35,
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False})  # скрыть тулбар Plotly


def severity_bar(series, title="Распределение по грубости", small=True):
    """Интерактивная диаграмма распределения грубости (3–6)."""
    counts = series.value_counts().sort_index()
    sev_colors = {3: "#f58231", 4: "#e6a84b", 5: "#e6644b", 6: "#e6194b"}
    cols = [sev_colors.get(s, "#808080") for s in counts.index]
    fig = go.Figure(go.Bar(
        x=[str(s) for s in counts.index],
        y=counts.values,
        marker_color=cols,
        text=counts.values,
        textposition="outside",
        hovertemplate="Грубость <b>%{x}</b><br>Количество: %{y}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13)),
        margin=dict(l=10, r=10, t=35, b=10),
        height=280 if small else 340,
        xaxis_title="Грубость",
        bargap=0.4,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def get_box(ann):
    """Извлекает прямоугольник выделения (x, y, w, h) из разметки."""
    rr = ann.get("rect_relative") or ann.get("rect")
    if rr is None:
        return None
    if isinstance(rr, list) and len(rr) >= 4:
        return float(rr[0]), float(rr[1]), float(rr[2]), float(rr[3])
    if isinstance(rr, dict):
        try:
            if "width" in rr:
                return (float(rr["x"]), float(rr["y"]),
                        float(rr["width"]), float(rr["height"]))
            if "right" in rr:
                return (float(rr["left"]), float(rr["top"]),
                        float(rr["right"]) - float(rr["left"]),
                        float(rr["bottom"]) - float(rr["top"]))
        except (TypeError, ValueError, KeyError):
            return None
    return None


def page_name(link):
    """Извлекает короткое название страницы из ссылки на html."""
    if not isinstance(link, str) or not link:
        return ""
    m = re.search(r"([^/]+?)(?:\.html?)?(?:[?#].*)?$", link.rstrip("/"))
    return m.group(1) if m else link[-30:]


def short_label(link, used):
    """Короткая подпись для выпадающего списка: хвост ссылки."""
    tail = link[-45:] if isinstance(link, str) else str(link)
    label = "…" + tail
    n = 2
    while label in used:
        label = f"…{tail} ({n})"
        n += 1
    used.add(label)
    return label


WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def norm_words(text):
    """Разбивает текст на нормализованные слова (нижний регистр)."""
    if not isinstance(text, str):
        return []
    return WORD_RE.findall(text.lower())


def words_similar(w1, w2):
    """Похожи ли два слова (падежные формы, опечатки):
    общий префикс >= 4 символов ИЛИ похожесть по difflib >= 0.8."""
    if w1 == w2:
        return True
    prefix = 0
    for a, b in zip(w1, w2):
        if a != b:
            break
        prefix += 1
    if prefix >= 4 and min(len(w1), len(w2)) >= 4:
        return True
    from difflib import SequenceMatcher
    return SequenceMatcher(None, w1, w2).ratio() >= 0.8


def texts_match(t1, t2, span_width):
    """Совпадают ли два текстовых выделения: есть похожее ключевое слово,
    и в каждом тексте не более (1 + 2 * span_width) слов."""
    w1, w2 = norm_words(t1), norm_words(t2)
    if not w1 or not w2:
        return False
    max_words = 1 + 2 * span_width
    if len(w1) > max_words or len(w2) > max_words:
        return False
    return any(words_similar(a, b) for a in w1 for b in w2)


# ===================== ЗАГРУЗКА ДАННЫХ =====================

@st.cache_data
def load_tsv(file) -> pd.DataFrame:
    """Парсит TSV с разметками. Страница берётся из INPUT:link_for_markup."""
    df = pd.read_csv(file, sep="\t", dtype=str)
    records = []
    for _, row in df.iterrows():
        raw = row.get("OUTPUT:result_json")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        page = row.get("INPUT:link_for_markup") or row.get("INPUT:name") or ""
        for ann in data.get("annotations", []):
            anchor = ann.get("anchor") or {}
            box = get_box(ann)
            records.append({
                "page": page,
                "название_страницы": page_name(page),
                "author": (ann.get("author") or "").strip(),
                "error_type": ann.get("error_type", ""),
                "category": ann.get("category", ""),
                "severity": ann.get("severity"),
                "obviousness": ann.get("obviousness", ""),
                "comment": ann.get("comment", ""),
                "text": ann.get("text", ""),
                "tool": "Текст" if anchor.get("type") == "text" else "Прямоугольник",
                "created_at": ann.get("created_at", ""),
                "ann_id": ann.get("id", ""),
                "box": box,
            })
    out = pd.DataFrame(records)
    if not out.empty:
        out["created_at"] = pd.to_datetime(out["created_at"], errors="coerce",
                                           format="mixed", utc=True)
        out["created_at"] = out["created_at"].dt.tz_localize(None)
    return out


@st.cache_data
def load_assessment(file) -> pd.DataFrame:
    """Загружает xlsx с результатами ручных проверок."""
    df = pd.read_excel(file)
    df.columns = [str(c).strip().lower() for c in df.columns]
    df["worker"] = df.get("worker", pd.Series(dtype=str)).astype(str).str.strip()
    df["pass"] = df.get("pass", pd.Series(dtype=str)).astype(str).str.strip()
    df["comment"] = df.get("comment", pd.Series(dtype=str)).fillna("")
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


def color_map(categories):
    return {c: BASE_COLORS[i % len(BASE_COLORS)] for i, c in enumerate(categories)}


# ===================== ЗАГРУЗКА И ФИЛЬТРЫ =====================

col1, col2 = st.columns(2)
with col1:
    uploaded = st.file_uploader("TSV с разметками", type=["tsv", "txt"])
with col2:
    assess_file = st.file_uploader("XLSX с результатами ручных проверок (необязательно)",
                                   type=["xlsx"])

if uploaded is None:
    st.info("Загрузите TSV-файл с разметками, чтобы увидеть статистику.")
    st.stop()

df = load_tsv(uploaded)
if df.empty:
    st.warning("Не удалось извлечь выделения. Проверьте столбец OUTPUT:result_json.")
    st.stop()

assessment = load_assessment(assess_file) if assess_file is not None else None

all_pages = sorted(df["page"].unique())
used = set()
label_to_page = {short_label(p, used): p for p in all_pages}

c1, c2, c3, c4 = st.columns(4)
with c1:
    sel_labels = st.multiselect("Страница", list(label_to_page.keys()))
    pages = [label_to_page[l] for l in sel_labels]
with c2:
    authors = st.multiselect("Разметчик", sorted(df["author"].unique()))
with c3:
    etypes = st.multiselect("Тип ошибки", sorted(df["error_type"].unique()))
with c4:
    cats = st.multiselect("Категория", sorted(df["category"].unique()))

if pages:
    with st.expander(f"Выбранные страницы ({len(pages)}) — полные ссылки"):
        for p in pages:
            st.markdown(f"· `{p}`")

f = df.copy()
if pages: f = f[f["page"].isin(pages)]
if authors: f = f[f["author"].isin(authors)]
if etypes: f = f[f["error_type"].isin(etypes)]
if cats: f = f[f["category"].isin(cats)]

CMAP = color_map(sorted(df["category"].unique()))

# Дополняем палитру типов ошибок для неизвестных значений
_known = set(ERROR_TYPE_COLORS)
for i, et in enumerate(sorted(df["error_type"].unique())):
    if et and et not in _known:
        ERROR_TYPE_COLORS[et] = BASE_COLORS[i % len(BASE_COLORS)]

# ===================== ВКЛАДКИ =====================

tab1, tab2, tab3, tab4 = st.tabs(["📊 Общая статистика",
                                  "🤝 Согласованность",
                                  "📈 Динамика качества",
                                  "💬 Комментарии"])

# -------------------- ВКЛАДКА 1: ОБЩАЯ СТАТИСТИКА --------------------
with tab1:
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Всего выделений", len(f))
    k2.metric("Разметчиков", f["author"].nunique())
    k3.metric("Страниц", f["page"].nunique())
    k4.metric("Ср. выделений на разметчика",
              round(len(f) / max(f["author"].nunique(), 1), 1))

    r1 = st.columns(3)
    with r1[0]:
        colored_bar(f["error_type"], ERROR_TYPE_COLORS, "По типу ошибки")
    with r1[1]:
        severity_bar(f["severity"])
    with r1[2]:
        colored_bar(f["tool"], {"Текст": "#4363d8", "Прямоугольник": "#f58231"},
                    "Инструменты выделения")

    colored_bar(f["category"], CMAP, "По категориям")

    per_author = (f.groupby("author")
                    .agg(выделений=("ann_id", "count"),
                         страниц=("page", "nunique"),
                         с_комментарием=("comment", lambda s: (s.fillna("") != "").sum()))
                    .sort_values("выделений", ascending=False))
    st.markdown("**Плотность разметки по разметчикам**")
    st.dataframe(per_author, use_container_width=True, height=250)

    per_page = (f.groupby(["название_страницы", "page"])
                  .agg(выделений=("ann_id", "count"),
                       разметчиков=("author", "nunique"),
                       ср_грубость=("severity", "mean"))
                  .reset_index()
                  .sort_values("выделений", ascending=False))
    st.markdown("**Статистика по страницам**")
    st.dataframe(per_page, use_container_width=True, height=250)

    with st.expander("Категория × инструмент выделения"):
        tool_cat = f.groupby(["category", "tool"]).size().unstack(fill_value=0)
        st.dataframe(tool_cat, use_container_width=True)

# -------------------- ВКЛАДКА 2: СОГЛАСОВАННОСТЬ --------------------
with tab2:
    st.caption("Сопоставляются только выделения, сделанные инструментом «Текст» "
               "(выделения «Прямоугольником» игнорируются, т.к. не содержат "
               "однозначного текста). Два выделения разных редакторов на одной "
               "странице считаются одним и тем же местом, если их тексты содержат "
               "похожее ключевое слово (с учётом падежных форм) и различаются "
               "не более чем на заданную ширину спана — количество добавочных "
               "слов слева и/или справа от ключевого слова.")

    span_width = st.slider("Ширина спана (добавочных слов вокруг ключевого слова)",
                           1, 5, 2)

    ft = f[(f["tool"] == "Текст") & (f["text"].fillna("") != "")].reset_index(drop=True)

    if ft.empty:
        st.info("В выбранной выборке нет текстовых выделений — "
                "анализ согласованности невозможен.")
        clusters = pd.DataFrame()
        multi = pd.DataFrame()
    else:
        parent = {i: i for i in range(len(ft))}

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            parent[find(i)] = find(j)

        for page, grp in ft.groupby("page"):
            idxs = grp.index.tolist()
            recs = grp.to_dict("records")
            for a_i in range(len(recs)):
                for b_i in range(a_i + 1, len(recs)):
                    a, b = recs[a_i], recs[b_i]
                    if a["author"] == b["author"]:
                        continue
                    if texts_match(a["text"], b["text"], span_width):
                        union(idxs[a_i], idxs[b_i])

        ft["cluster"] = [find(i) for i in range(len(ft))]

        cluster_rows = []
        for cl, grp in ft.groupby("cluster"):
            n_authors = grp["author"].nunique()
            cluster_rows.append({
                "название_страницы": grp["название_страницы"].iloc[0],
                "редакторов": n_authors,
                "выделений": len(grp),
                "категории": "; ".join(sorted(grp["category"].unique())),
                "грубости": "; ".join(sorted(str(s) for s in grp["severity"].dropna().unique())),
                "совпала_категория": grp["category"].nunique() == 1 if n_authors > 1 else None,
                "совпала_грубость": grp["severity"].nunique() == 1 if n_authors > 1 else None,
                "тексты": " | ".join(dict.fromkeys(grp["text"]))[:160],
                "авторы": "; ".join(sorted(grp["author"].unique())),
            })
        clusters = pd.DataFrame(cluster_rows)

        multi = clusters[clusters["редакторов"] >= 2]
        solo = clusters[clusters["редакторов"] == 1]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Текстовых выделений проанализировано", len(ft))
        m2.metric("Совпавших мест (≥2 редакторов)", len(multi))
        m3.metric("Уникальных (1 редактор)", len(solo))
        if len(multi):
            agree = multi["совпала_категория"].mean()
            m4.metric("Совпадение категорий", f"{agree:.0%}")
        else:
            m4.metric("Совпадение категорий", "—")

        if len(multi):
            dis_cat = multi["совпала_категория"].value_counts().rename(
                {True: "категория совпала", False: "категория разошлась"})
            dis_sev = multi["совпала_грубость"].value_counts().rename(
                {True: "грубость совпала", False: "грубость разошлась"})
            d1, d2 = st.columns(2)
            with d1:
                st.bar_chart(dis_cat, height=200)
            with d2:
                st.bar_chart(dis_sev, height=200)

            st.markdown("**Совпавшие места (≥2 редакторов)**")
            st.dataframe(multi.sort_values("редакторов", ascending=False),
                         use_container_width=True, height=300)

            with st.expander("Уникальные текстовые выделения (только один редактор)"):
                uniq_ann = ft[ft["cluster"].isin(solo.index)]
                st.dataframe(uniq_ann[["название_страницы", "author", "error_type",
                                       "category", "severity", "text"]],
                             use_container_width=True, height=300)
        else:
            st.info("Совпадений между редакторами при данной ширине спана не найдено. "
                    "Попробуйте увеличить ширину спана.")

# -------------------- ВКЛАДКА 3: ДИНАМИКА КАЧЕСТВА --------------------
with tab3:
    if assessment is None or assessment.empty:
        st.info("Загрузите xlsx-файл с результатами ручных проверок (кнопка вверху), "
                "чтобы увидеть этот раздел.")
    else:
        a = assessment.dropna(subset=["pass"])
        a = a[a["pass"].isin(["Да", "Нет", "Не уверен"])]

        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Проверок всего", len(a))
        v2.metric("Да", (a["pass"] == "Да").sum())
        v3.metric("Нет", (a["pass"] == "Нет").sum())
        v4.metric("Не уверен", (a["pass"] == "Не уверен").sum())

        q1, q2 = st.columns(2)
        with q1:
            colored_bar(a["pass"], VERDICT_COLORS, "Распределение вердиктов")
        with q2:
            if "date" in a.columns and a["date"].notna().any():
                by_day = a.groupby([a["date"].dt.date, "pass"]).size().unstack(fill_value=0)
                for c in ["Да", "Нет", "Не уверен"]:
                    if c not in by_day.columns:
                        by_day[c] = 0
                fig = go.Figure()
                for c in ["Да", "Нет", "Не уверен"]:
                    fig.add_trace(go.Bar(
                        name=c,
                        x=[str(d) for d in by_day.index],
                        y=by_day[c],
                        marker_color=VERDICT_COLORS[c],
                        hovertemplate=f"<b>{{%x}}</b><br>{c}: %{{y}}<extra></extra>",
                    ))
                fig.update_layout(
                    barmode="stack",
                    title=dict(text="Динамика вердиктов по дням", font=dict(size=13)),
                    margin=dict(l=10, r=10, t=35, b=10),
                    height=340,
                    legend=dict(orientation="h", y=1.05),
                )
                st.plotly_chart(fig, use_container_width=True,
                                config={"displayModeBar": False})
            else:
                st.info("В файле нет корректной колонки date.")

        by_worker = a.groupby("worker")["pass"].value_counts().unstack(fill_value=0)
        for c in ["Да", "Нет", "Не уверен"]:
            if c not in by_worker.columns:
                by_worker[c] = 0
        by_worker["доля_Да"] = (by_worker["Да"] /
                                 by_worker[["Да", "Нет", "Не уверен"]].sum(axis=1)).round(2)
        st.markdown("**Вердикты по разметчикам**")
        st.dataframe(by_worker.sort_values("доля_Да", ascending=False), height=250)

        with st.expander("Проверки с комментариями"):
            st.dataframe(a[["worker", "date", "pass", "comment"]],
                         use_container_width=True, height=300)

# -------------------- ВКЛАДКА 4: КОММЕНТАРИИ --------------------
with tab4:
    fc = f.copy()
    fc["comment"] = fc["comment"].fillna("")
    fc["has_comment"] = fc["comment"] != ""
    fc["comment_len"] = fc["comment"].str.len()
    fc["comment_words"] = fc["comment"].apply(lambda s: len(norm_words(s)))

    n1, n2, n3, n4 = st.columns(4)
    n1.metric("Всего выделений", len(fc))
    n2.metric("С комментариями", int(fc["has_comment"].sum()))
    n3.metric("Без комментариев", int((~fc["has_comment"]).sum()))
    n4.metric("Доля с комментарием",
              f"{fc['has_comment'].mean():.0%}" if len(fc) else "—")

    r1c, r2c = st.columns(2)
    with r1c:
        colored_bar(fc.loc[fc["has_comment"], "category"], CMAP,
                    "Комментарии по категориям")
    with r2c:
        colored_bar(fc.loc[fc["has_comment"], "error_type"], ERROR_TYPE_COLORS,
                    "Комментарии по типам ошибок")

    cat_comm = (fc.groupby("category")
                  .agg(выделений=("ann_id", "count"),
                       с_комментарием=("has_comment", "sum"),
                       доля_с_комментарием=("has_comment", "mean"),
                       ср_длина_комментария=("comment_len", "mean"))
                  .round(2)
                  .sort_values("доля_с_комментарием", ascending=False))
    st.markdown("**Комментарии по категориям**")
    st.dataframe(cat_comm, use_container_width=True, height=250)

    et_comm = (fc.groupby("error_type")
                 .agg(выделений=("ann_id", "count"),
                      с_комментарием=("has_comment", "sum"),
                      доля_с_комментарием=("has_comment", "mean"))
                 .round(2))
    st.markdown("**Комментарии по типам ошибок**")
    st.dataframe(et_comm, height=150)

    author_comm = (fc.groupby("author")
                     .agg(выделений=("ann_id", "count"),
                          с_комментарием=("has_comment", "sum"),
                          доля_с_комментарием=("has_comment", "mean"),
                          ср_символов=("comment_len", "mean"),
                          ср_слов=("comment_words", "mean"),
                          символов_всего=("comment_len", "sum"))
                     .round(2))

    if len(author_comm):
        norm_share = author_comm["доля_с_комментарием"] / author_comm["доля_с_комментарием"].max()
        norm_len = author_comm["ср_символов"] / author_comm["ср_символов"].max()
        author_comm["индекс_комментируемости"] = (norm_share * norm_len).round(3)
        author_comm = author_comm.sort_values("индекс_комментируемости", ascending=False)

    st.markdown("**Рейтинг разметчиков по комментариям**")
    st.caption("Индекс комментируемости — доля выделений с комментарием × "
               "развёрнутость (средняя длина), нормировано по максимуму.")
    st.dataframe(author_comm, use_container_width=True, height=250)

    comm_table = fc[fc["has_comment"]][["название_страницы", "author", "error_type",
                                       "category", "severity", "comment",
                                       "comment_len", "comment_words"]]
    comm_table = comm_table.sort_values(["author", "comment_len"], ascending=[True, False])

    short_thr = st.slider("Порог «короткого» комментария (символов)", 5, 100, 20, 5)
    short_comm = comm_table[comm_table["comment_len"] < short_thr]

    e1, e2 = st.columns(2)
    with e1:
        with st.expander(f"Все комментарии ({len(comm_table)})"):
            st.dataframe(comm_table, use_container_width=True, height=300)
    with e2:
        with st.expander(f"Короткие комментарии (< {short_thr} символов) — {len(short_comm)} шт."):
            if len(short_comm):
                st.dataframe(short_comm[["author", "category", "comment"]],
                             use_container_width=True, height=300)

# ===================== ОТЧЁТЫ =====================
st.divider()
st.subheader("Скачать отчёт")
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as xl:
    f.drop(columns=["box"]).to_excel(xl, sheet_name="Выделения", index=False)
    per_author.to_excel(xl, sheet_name="По разметчикам")
    per_page.to_excel(xl, sheet_name="По страницам", index=False)
    if not clusters.empty:
        clusters.to_excel(xl, sheet_name="Согласованность (текстовая)", index=False)
    if len(multi):
        multi.to_excel(xl, sheet_name="Совпавшие кластеры", index=False)
    if assessment is not None and not assessment.empty:
        assessment.to_excel(xl, sheet_name="Проверки", index=False)
    author_comm.to_excel(xl, sheet_name="Комментарии по разметчикам")
    cat_comm.to_excel(xl, sheet_name="Комментарии по категориям")
    comm_table.to_excel(xl, sheet_name="Все комментарии", index=False)
d1, d2 = st.columns(2)
with d1:
    st.download_button("📊 Excel-отчёт", buf.getvalue(), "markup_report.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)
with d2:
    st.download_button("📄 CSV (выделения)",
                       f.drop(columns=["box"]).to_csv(index=False).encode("utf-8"),
                       "annotations.csv", "text/csv", use_container_width=True)
