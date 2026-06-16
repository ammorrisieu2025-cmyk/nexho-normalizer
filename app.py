import streamlit as st
import pandas as pd
import re
import io

st.set_page_config(page_title="Nexho Product Normalizer", page_icon="🍺", layout="centered")

BRAND_ALIASES = {
    "m*": "Mahou", "mao": "Mahou", "maho": "Mahou", "mahou": "Mahou", "mh": "Mahou",
    "sm": "San Miguel",
    "alh": "Alhambra", "alhamb": "Alhambra",
    "cocacola": "Coca-Cola", "coca cola": "Coca-Cola", "coca-cola": "Coca-Cola", "cca": "Coca-Cola",
    "sch": "Schweppes", "schwepps": "Schweppes",
    "7up": "7Up", "7-up": "7Up",
    "a.muñoz": "Aceites Muñoz",
    "solan": "Solan de Cabras",
    "j.w.": "Johnnie Walker",
    "jack daniel.": "Jack Daniels",
    "tanquer.": "Tanqueray",
}

FRACTION_TO_CL = {"1/1": "100 cl", "1/2": "50 cl", "1/3": "33 cl", "1/4": "25 cl", "1/5": "20 cl"}
WORD_FRACTIONS = {"tercio": "33 cl", "quinto": "20 cl", "medio": "50 cl"}

FORMAT_PATTERNS = [
    (r'\bVIDRIO\b|\bVID\b', "Vidrio"),
    (r'\bRET(?:ORN(?:ABLE)?)?\.?\b', "Vidrio"),
    (r'\bN\.?R\.?\b|\bNO\s*RET(?:ORNABLE)?\b', None),
    (r'\bLATA\b|\bLATAS\b|\bLAT\b', "Lata"),
    (r'\bPET\b', "PET"),
    (r'\bBRIK\b|\bBRK\b', "Brik"),
    (r'\bGARRAFA\b|\bGARR\b', "Garrafa"),
    (r'\bBIDON\b', "Bidon"),
    (r'\bBARRIL\b|\bKEG\b', "Barril"),
    (r'\bMONODOSIS\b|\bMONOD\b', "Monodosis"),
    (r'\bSACHET\b|\bSOBRES?\b', "Sobres"),
    (r'\bTARRINA\b', "Tarrina"),
    (r'\bBAG\s*IN\s*BOX\b', "Bag in Box"),
]

NOISE_PATTERNS = [
    r'\bCAJA\s*\d*U?\b', r'\b\d+\s*UDS?\b', r'\bX\s*\d+\b',
    r'\bUNI\.?\b', r'\bBOT\.?\b', r'^\*+', r'\*+$', r'^-+\s*',
    r'\bPACK\b', r'\bBANDEJA\b',
]

def fix_encoding(text):
    text = re.sub(r'MU[¡¦­?]OZ', 'MUÑOZ', text, flags=re.IGNORECASE)
    text = re.sub(r'A[¡¦­?]O', 'AÑO', text, flags=re.IGNORECASE)
    return text

def _fmt_num(n):
    if n == int(n):
        return str(int(n))
    return f"{n:.2f}".rstrip("0").rstrip(".").replace(".", ",")

def _fmt_cl_or_l(cl):
    return f"{_fmt_num(cl/100)} l" if cl >= 100 else f"{_fmt_num(cl)} cl"

def _fmt_kg(kg):
    return f"{_fmt_num(kg)} kg"

def normalize_volume(text):
    t = text.upper()
    for word, val in WORD_FRACTIONS.items():
        if re.search(rf'\b{word.upper()}\b', t):
            return val, word
    m = re.search(r'\b([1-5]/[1-5])\b', t)
    if m and m.group(1) in FRACTION_TO_CL:
        return FRACTION_TO_CL[m.group(1)], m.group(0)
    m = re.search(r'(\d+[.,]?\d*)\s*ML\b', t)
    if m:
        return _fmt_cl_or_l(float(m.group(1).replace(",","."))/10), m.group(0)
    m = re.search(r'(\d+[.,]?\d*)\s*CL\b', t)
    if m:
        return _fmt_cl_or_l(float(m.group(1).replace(",","."))), m.group(0)
    m = re.search(r'(\d+[.,]?\d*)\s*L(?:ITROS?)?\b', t)
    if m:
        return _fmt_cl_or_l(float(m.group(1).replace(",","."))*100), m.group(0)
    m = re.search(r'(\d+[.,]?\d*)\s*K(?:G)?\b', t)
    if m:
        return _fmt_kg(float(m.group(1).replace(",","."))), m.group(0)
    m = re.search(r'(\d+[.,]?\d*)\s*G(?:R)?\b', t)
    if m:
        gr = float(m.group(1).replace(",","."))
        return (_fmt_kg(gr/1000) if gr >= 1000 else f"{_fmt_num(gr)} gr"), m.group(0)
    return None, None

def detect_format(text):
    t = text.upper()
    for pattern, label in FORMAT_PATTERNS:
        if re.search(pattern, t):
            return label
    return None

def detect_brand(text, brand_list):
    t = text.upper()
    for alias, canonical in BRAND_ALIASES.items():
        if re.search(rf'\b{re.escape(alias.upper())}\b', t):
            return canonical
    for brand in brand_list:
        if re.search(rf'\b{re.escape(str(brand).upper())}\b', t):
            return str(brand)
    return None

def remove_noise(text):
    t = text
    for pattern in NOISE_PATTERNS:
        t = re.sub(pattern, ' ', t, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', t).strip()

def normalize_one(raw_name, brand_list):
    avisos = []
    if not raw_name or not str(raw_name).strip():
        return {"normalized": None, "confianza": 0.0, "requiere_revision_humana": True, "avisos": ["Descripción vacía"]}
    text = fix_encoding(str(raw_name))
    if text != str(raw_name):
        avisos.append("Encoding corregido")
    brand = detect_brand(text, brand_list)
    if not brand:
        avisos.append("Marca no detectada")
    volume, vol_match = normalize_volume(text)
    if not volume:
        avisos.append("Volumen no detectado")
    fmt = detect_format(text)
    if not fmt:
        avisos.append("Formato no claro")
    remainder = text
    if vol_match:
        remainder = re.sub(re.escape(vol_match), '', remainder, flags=re.IGNORECASE)
    for pattern, _ in FORMAT_PATTERNS:
        remainder = re.sub(pattern, '', remainder, flags=re.IGNORECASE)
    remainder = remove_noise(remainder)
    if brand:
        remainder = re.sub(rf'\b{re.escape(brand.upper())}\b', remainder.upper(), '', flags=re.IGNORECASE)
    remainder = re.sub(r'\s+', ' ', remainder).strip()
    variety = remainder.title() if remainder else ""
    parts = []
    if brand: parts.append(brand)
    if variety: parts.append(variety)
    if fmt: parts.append(fmt)
    if volume: parts.append(volume)
    if not parts:
        return {"normalized": None, "confianza": 0.0, "requiere_revision_humana": True, "avisos": avisos + ["No se pudo extraer ningún atributo"]}
    normalized = " ".join(parts)
    found = sum([bool(brand), bool(volume), bool(fmt)])
    confianza = {3: 0.85, 2: 0.65, 1: 0.40}.get(found, 0.15)
    return {"normalized": normalized, "confianza": confianza, "requiere_revision_humana": confianza < 0.70, "avisos": avisos}

def load_brands_from_df(df):
    col = df.columns[0]
    brands = df[col].dropna().astype(str).str.strip().tolist()
    brands = [b for b in brands if b and not b.isdigit()]
    brands.sort(key=len, reverse=True)
    return brands

def detect_name_col(df):
    candidates = ["des_product_name","product_name","nombre_sin_normalizar","nombre_producto","descripcion","description","name","producto"]
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in cols_lower:
            return cols_lower[cand]
    str_cols = df.select_dtypes(include="object").columns
    avg_len = {c: df[c].dropna().astype(str).str.len().mean() for c in str_cols}
    return max(avg_len, key=avg_len.get)

def to_excel_bytes(df_main, df_review):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_main.to_excel(writer, index=False, sheet_name="Normalized Catalog")
        df_review.to_excel(writer, index=False, sheet_name="Needs Review")
    return buf.getvalue()

# ── UI ──────────────────────────────────────────────────────────────────────

st.title("🍺 Nexho Product Normalizer")
st.markdown("**Mahou San Miguel** · Transform messy distributor product descriptions into clean, structured catalog entries.")
st.divider()

st.subheader("Step 1 — Upload brand master list")
brands_file = st.file_uploader("Upload brands.xlsx", type=["xlsx","xls"], key="brands")
brand_list = []
if brands_file:
    brands_df = pd.read_excel(brands_file, dtype=str)
    brand_list = load_brands_from_df(brands_df)
    st.success(f"✓ {len(brand_list)} brands loaded")

st.divider()

st.subheader("Step 2 — Upload your product catalog")
st.caption("Excel or CSV — any format. The tool auto-detects your product name column.")
catalog_file = st.file_uploader("Upload catalog file", type=["xlsx","xls","csv"], key="catalog")
dist_id = st.text_input("Your distributor ID (optional)", placeholder="e.g. 22")

st.divider()

st.subheader("Step 3 — Normalize")

if st.button("▶ Run normalization", type="primary", disabled=not (brands_file and catalog_file)):
    df = pd.read_csv(catalog_file, dtype=str) if catalog_file.name.endswith(".csv") else pd.read_excel(catalog_file, dtype=str)
    name_col = detect_name_col(df)
    id_col = next((c for c in df.columns if c.lower() == "id_distributor"), None)
    names = df[name_col].fillna("").astype(str).str.strip().tolist()

    progress = st.progress(0, text="Normalizing products...")
    results = []
    for i, name in enumerate(names):
        results.append(normalize_one(name, brand_list))
        if i % 100 == 0:
            progress.progress(min(i/len(names), 1.0), text=f"Processing {i+1}/{len(names)}...")
    progress.progress(1.0, text="Done!")

    out_rows = []
    for i, (name, result) in enumerate(zip(names, results)):
        row = {}
        if id_col:
            row["id_distributor"] = df[id_col].iloc[i]
        elif dist_id:
            row["id_distributor"] = dist_id
        row["des_product_name"] = name
        row["des_product_description_normalized"] = result["normalized"] or ""
        row["confianza"] = result["confianza"]
        row["requiere_revision_humana"] = result["requiere_revision_humana"]
        row["avisos"] = "; ".join(result["avisos"]) if result["avisos"] else ""
        out_rows.append(row)

    out_df = pd.DataFrame(out_rows)
    review_df = out_df[out_df["requiere_revision_humana"] == True].copy()
    total = len(out_df)
    clean = (~out_df["requiere_revision_humana"]).sum()
    needs_review = out_df["requiere_revision_humana"].sum()
    avg_conf = out_df["confianza"].mean()

    st.divider()
    st.subheader("Results")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total products", total)
    col2.metric("Fully normalized", f"{clean} ({clean/total*100:.0f}%)")
    col3.metric("Needs review", f"{needs_review} ({needs_review/total*100:.0f}%)")
    col4.metric("Avg confidence", f"{avg_conf:.2f}")

    st.dataframe(out_df.head(50), use_container_width=True)

    excel_bytes = to_excel_bytes(out_df, review_df)
    st.download_button(
        label="⬇ Download Excel output",
        data=excel_bytes,
        file_name=f"nexho_normalized_{dist_id or 'output'}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    st.caption("Output contains two sheets: **Normalized Catalog** (all products) and **Needs Review** (flagged rows only).")

elif not brands_file or not catalog_file:
    st.info("Upload both files above to enable normalization.")

st.divider()
st.caption("Built by IE Innovation LAB · Mahou San Miguel Nexho Project · 2026")
