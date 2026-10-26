# -*- coding: utf-8 -*-
"""
Интегральная метрика браузерных страниц — статистика разметок (v5).
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
st.title("Интегральная метрика браузерных страниц — статистика разметок")

BASE_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
               "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080",
               "#9a6324", "#800000", "#808000", "#000075", "#a9a9a9"]

VERDICT_COLORS = {"Да": "#3cb44b", "Нет": "#e6194b", "Не уверен": "#f58231"}


# ===================== УТИЛИТЫ =====================

def is_dark_theme():
    """Определяет тему интерфейса Streamlit."""
    try:
        return st.get_option("theme.base") == "dark"
    except Exception:
        return False


def apply_theme(ax, title):
    """Заглушка: графики в стандартном (светлом) оформлении matplotlib."""
    ax.set_title(title)


def colored_bar(series, colors, title, small=False):
    """Столбчатая диаграмма с индивидуальным цветом на категорию."""
    fig, ax = plt.subplots(figsize=(2.5, 1.25) if small else (3.5, 2))
    counts = series.value_counts()
    cols = [colors.get(c, "#808080") for c in counts.index]
    ax.bar(range(len(counts)), counts.values, color=cols)
    ax.set_xticks(range(len(counts)))
    ax.set_xticklabels(counts.index, rotation=35, ha="right", fontsize=8)
    apply_theme(ax, title)
    st.pyplot(fig)
    plt.close(fig)
    
def severity_bar(series, title="Распределение по грубости", small=True):
    """Компактная диаграмма распределения грубости (3–6), цифры без поворота."""
    fig, ax = plt.subplots(figsize=(2.5, 1.25) if small else (3.5, 2))
    counts = series.value_counts().sort_index()
    # Равномерные цвета для уровней грубости: чем грубее, тем «горячее»
    sev_colors = {3: "#f58231", 4: "#e6a84b", 5: "#e6644b", 6: "#e6194b"}
    cols = [sev_colors.get(s, "#808080") for s in counts.index]
    ax.bar(range(len(counts)), counts.values, color=cols)
    ax.set_xticks(range(len(counts)))
    ax.set_xticklabels(counts.index, fontsize=10)  # без rotation — цифры горизонтальны
    ax.set_xlabel("Грубость", fontsize=9)
    apply_theme(ax, title)
    st.pyplot(fig)
    plt.close(fig)

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


def overlap_min_area(b1, b2):
    """Пересечение двух прямоугольников, нормированное на меньшую площадь.
    Устойчиво к ситуации «один выделил слово, другой — предложение вокруг него»."""
    x1, y1, w1, h1 = b1
    x2, y2, w2, h2 = b2
    if w1 <= 0 or h1 <= 0 or w2 <= 0 or h2 <= 0:
        return 0.0
    ix = max(0.0, min(x1 + w1, x2 + w2) - max(x1, x2))
    iy = max(0.0, min(y1 + h1, y2 + h2) - max(y1, y2))
    inter = ix * iy
    smaller = min(w1 * h1, w2 * h2)
    return inter / smaller if smaller > 0 else 0.0


def page_name(link):
    """Извлекает короткое название страницы из ссылки на html.
    Берёт последний сегмент пути без расширения, напр.
    https://.../pages/checkout.html -> checkout"""
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
    """Разбивает текст выделения на нормализованные слова (нижний регистр)."""
    if not isinstance(text, str):
        return []
    return WORD_RE.findall(text.lower())


def words_similar(w1, w2):
    """Похожи ли два слова (учёт падежных форм и опечаток):
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
    и в каждом тексте не более (1 + 2 * span_width) слов, т.е. вокруг
    ключевого слова допускается до span_width добавочных слов слева и справа."""
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
        # Приводим дату к «наивной» (без таймзоны) — иначе Excel не экспортируется
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


# ===================== ИНТЕРФЕЙС =====================

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

# --- Фильтры ---
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

# Полные ссылки на выбранные страницы
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

# ===================== ОБЩАЯ СТАТИСТИКА =====================
st.subheader("Общая статистика")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Всего выделений", len(f))
k2.metric("Разметчиков", f["author"].nunique())
k3.metric("Страниц", f["page"].nunique())
k4.metric("Ср. выделений на разметчика", round(len(f) / max(f["author"].nunique(), 1), 1))

# Строка 1: тип ошибки + грубость + инструменты (все — маленькие двухколоночные)
r1 = st.columns(3)
with r1[0]:
    colored_bar(f["error_type"], CMAP, "По типу ошибки")
with r1[1]:
    severity_bar(f["severity"])
with r1[2]:
    colored_bar(f["tool"], {"Текст": "#4363d8", "Прямоугольник": "#f58231"},
                "Инструменты выделения")

# Строка 2: категории — во всю ширину (их много, подписи длинные)
colored_bar(f["category"], CMAP, "По категориям")

# Таблица «категория × инструмент» рядом с категориями не нужна отдельно —
# при желании оставьте её ниже:
tool_cat = f.groupby(["category", "tool"]).size().unstack(fill_value=0)
st.dataframe(tool_cat, use_container_width=True)

st.subheader("Плотность разметки по разметчикам")
per_author = (f.groupby("author")
                .agg(выделений=("ann_id", "count"),
                     страниц=("page", "nunique"),
                     с_комментарием=("comment", lambda s: (s.fillna("") != "").sum()))
                .sort_values("выделений", ascending=False))
st.dataframe(per_author)

st.subheader("Статистика по страницам")
per_page = (f.groupby(["название_страницы", "page"])
              .agg(выделений=("ann_id", "count"),
                   разметчиков=("author", "nunique"),
                   ср_грубость=("severity", "mean"))
              .reset_index()
              .sort_values("выделений", ascending=False))
st.dataframe(per_page, use_container_width=True)

# ===================== СОГЛАСОВАННОСТЬ РЕДАКТОРОВ =====================
st.header("Согласованность редакторов")
st.caption("Сопоставляются только выделения, сделанные инструментом «Текст» "
           "(выделения «Прямоугольником» игнорируются, т.к. не содержат "
           "однозначного текста). Два выделения разных редакторов на одной "
           "странице считаются одним и тем же местом, если их тексты содержат "
           "похожее ключевое слово (с учётом падежных форм) и различаются "
           "не более чем на заданную ширину спана — количество добавочных "
           "слов слева и/или справа от ключевого слова.")

span_width = st.slider("Ширина спана (добавочных слов вокруг ключевого слова)",
                       1, 5, 2)

# Только текстовые выделения с непустым текстом
ft = f[(f["tool"] == "Текст") & (f["text"].fillna("") != "")].reset_index(drop=True)

if ft.empty:
    st.info("В выбранной выборке нет текстовых выделений — "
            "анализ согласованности невозможен.")
    clusters = pd.DataFrame()
    multi = pd.DataFrame()
else:
    # Union-Find для группировки совпадающих выделений
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
        st.markdown("**Расхождение редакторов в совпавших местах:**")
        dis_cat = multi["совпала_категория"].value_counts().rename(
            {True: "категория совпала", False: "категория разошлась"})
        dis_sev = multi["совпала_грубость"].value_counts().rename(
            {True: "грубость совпала", False: "грубость разошлась"})
        d1, d2 = st.columns(2)
        with d1:
            st.bar_chart(dis_cat)
        with d2:
            st.bar_chart(dis_sev)

        st.subheader("Совпавшие места (≥2 редакторов)")
        st.dataframe(multi.sort_values("редакторов", ascending=False),
                     use_container_width=True)

        st.subheader("Уникальные текстовые выделения (только один редактор)")
        uniq_ann = ft[ft["cluster"].isin(solo.index)]
        st.dataframe(uniq_ann[["название_страницы", "author", "error_type",
                               "category", "severity", "text"]],
                     use_container_width=True)
    else:
        st.info("Совпадений между редакторами при данной ширине спана не найдено. "
                "Попробуйте увеличить ширину спана.")

# ===================== ДИНАМИКА КАЧЕСТВА =====================
st.header("Динамика качества разметок")
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
            fig, ax = plt.subplots(figsize=(3.5, 2))
            bottom = None
            for c in ["Да", "Нет", "Не уверен"]:
                ax.bar(by_day.index.astype(str), by_day[c], bottom=bottom,
                       color=VERDICT_COLORS[c], label=c)
                bottom = by_day[c] if bottom is None else bottom + by_day[c]
            apply_theme(ax, "Динамика вердиктов по дням")
            ax.legend()
            ax.tick_params(axis="x", rotation=45)
            st.pyplot(fig)
            plt.close(fig)
        else:
            st.info("В файле нет корректной колонки date.")

    st.subheader("Вердикты по разметчикам")
    by_worker = a.groupby("worker")["pass"].value_counts().unstack(fill_value=0)
    for c in ["Да", "Нет", "Не уверен"]:
        if c not in by_worker.columns:
            by_worker[c] = 0
    by_worker["доля_Да"] = (by_worker["Да"] /
                             by_worker[["Да", "Нет", "Не уверен"]].sum(axis=1)).round(2)
    st.dataframe(by_worker.sort_values("доля_Да", ascending=False))

    st.subheader("Проверки с комментариями")
    st.dataframe(a[["worker", "date", "pass", "comment"]], use_container_width=True)

# ===================== АНАЛИЗ КОММЕНТАРИЕВ =====================
st.header("Анализ комментариев разметчиков")

# Подготовка: непустые комментарии
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

# --- Комментарии по категориям и типам ошибок ---
st.subheader("Комментарии по категориям и типам ошибок")
cat_comm = (fc.groupby("category")
              .agg(выделений=("ann_id", "count"),
                   с_комментарием=("has_comment", "sum"),
                   доля_с_комментарием=("has_comment", "mean"),
                   ср_длина_комментария=("comment_len", "mean"))
              .round(2)
              .sort_values("доля_с_комментарием", ascending=False))
st.dataframe(cat_comm, use_container_width=True)

et_comm = (fc.groupby("error_type")
             .agg(выделений=("ann_id", "count"),
                  с_комментарием=("has_comment", "sum"),
                  доля_с_комментарием=("has_comment", "mean"))
             .round(2))
st.dataframe(et_comm)

# --- Рейтинг разметчиков ---
st.subheader("Рейтинг разметчиков по комментариям")
author_comm = (fc.groupby("author")
                 .agg(выделений=("ann_id", "count"),
                      с_комментарием=("has_comment", "sum"),
                      доля_с_комментарием=("has_comment", "mean"),
                      ср_символов=("comment_len", "mean"),
                      ср_слов=("comment_words", "mean"),
                      символов_всего=("comment_len", "sum"))
                 .round(2))

# Сводный индекс: доля комментируемых × средняя длина (развёрнутость)
# Нормируем обе величины в диапазон 0–1 по максимуму, индекс = их произведение.
if len(author_comm):
    norm_share = author_comm["доля_с_комментарием"] / author_comm["доля_с_комментарием"].max()
    norm_len = author_comm["ср_символов"] / author_comm["ср_символов"].max()
    author_comm["индекс_комментируемости"] = (norm_share * norm_len).round(3)
    author_comm = author_comm.sort_values("индекс_комментируемости", ascending=False)

st.caption("Индекс комментируемости — сводный показатель: доля выделений с "
           "комментарием × развёрнутость комментариев (средняя длина), обе "
           "величины нормированы по максимуму в выборке. Чем выше, тем активнее "
           "и подробнее разметчик комментирует.")
st.dataframe(author_comm, use_container_width=True)

# --- Диаграммы ---
r1c, r2c = st.columns(2)
with r1c:
    colored_bar(fc.loc[fc["has_comment"], "category"], CMAP,
                "Комментарии по категориям")
with r2c:
    colored_bar(fc.loc[fc["has_comment"], "error_type"], CMAP,
                "Комментарии по типам ошибок")

# --- Само содержимое комментариев ---
st.subheader("Все комментарии (таблица)")
comm_table = fc[fc["has_comment"]][["название_страницы", "author", "error_type",
                                    "category", "severity", "comment",
                                    "comment_len", "comment_words"]]
comm_table = comm_table.sort_values(["author", "comment_len"], ascending=[True, False])
st.dataframe(comm_table, use_container_width=True)

# --- Короткие комментарии (потенциально малоинформативные) ---
short_thr = st.slider("Порог «короткого» комментария (символов)", 5, 100, 20, 5)
short_comm = comm_table[comm_table["comment_len"] < short_thr]
st.markdown(f"**Короткие комментарии (< {short_thr} символов): "
            f"{len(short_comm)} шт.**")
if len(short_comm):
    st.dataframe(short_comm[["author", "category", "comment"]],
                 use_container_width=True)

# ===================== ОТЧЁТЫ =====================
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
st.download_button("📊 Excel-отчёт", buf.getvalue(), "markup_report.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
st.download_button("📄 CSV (выделения)",
                   f.drop(columns=["box"]).to_csv(index=False).encode("utf-8"),
                   "annotations.csv", "text/csv")
                   
st.markdown("""
<style>
    .stPlotlyChart, [data-testid="stPyplotGlobal"] { zoom: 0.6; }
    .element-container img { zoom: 0.6; }
</style>
""", unsafe_allow_html=True)
