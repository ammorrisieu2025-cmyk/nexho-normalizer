import streamlit as st
import pandas as pd
import anthropic
import json
import io
import re
import time

st.set_page_config(page_title="Nexho Product Normalizer", page_icon="🍺", layout="centered")

BRAND_ALIASES = {
    "m*": "Mahou", "mao": "Mahou", "maho": "Mahou", "mh": "Mahou",
    "sm": "San Miguel", "alh": "Alhambra", "alhamb": "Alhambra",
    "cocacola": "Coca-Cola", "coca cola": "Coca-Cola", "cca": "Coca-Cola",
    "sch": "Schweppes", "schwepps": "Schweppes",
    "7up": "7Up", "7-up": "7Up",
    "solan": "Solan de Cabras",
    "j.w.": "Johnnie Walker", "jack daniel.": "Jack Daniels",
}

def load_brands_from_df(df):
    col = df.columns[0]
    brands = df[col].dropna().astype(str).str.strip().tolist()
    brands = [b for b in brands if b and not b.isdigit()]
    brands.sort(key=len, reverse=True)
    return brands

def detect_name_col(df):
    candidates = ["des_product_name","product_name","nombre_sin_normalizar",
                  "nombre_producto","descripcion","description","name","producto"]
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in cols_lower:
            return cols_lower[cand]
    str_cols = df.select_dtypes(include="object").columns
    avg_len = {c: df[c].dropna().astype(str).str.len().mean() for c in str_cols}
    return max(avg_len, key=avg_len.get)

def normalize_batch_claude(client, names, brand_list):
    brand_str = ", ".join(brand_list[:200])
    
    system = f"""You are an expert product catalog normalizer for Nexho, a Spanish hospitality B2B marketplace owned by Mahou San Miguel.

BRAND MASTER LIST (match and correct against these):
{brand_str}

NORMALIZATION RULES:
- Volume: 1/3|tercio|33cl|0.33L→"33 cl" | 1/5|quinto|20cl|200cc→"20 cl" | 3/4|75cl|0.75L→"75 cl" | 1/2|50cl→"50 cl" | 1L|1litro→"1 l" | 70cl|0.7L→"70 cl" | 30L→"30 l" | 15L→"15 l" | 350cc|35cl→"35 cl" | 1200GR→"1.2 kg" | 5K|5KG→"5 kg"
- Format: vidrio|botella|bot|RT→"Vidrio" | lata|latas|ltn→"Lata" | barril|keg→"Barril" | PET→"PET" | sobre|sobres|monodosis→"Monodosis" | brik→"Brik" | cubo→"Cubo" | barril→"Barril"
- Retornabilidad: RET|ret|retornable|RT→"Retornable" | NR|N.R.|no ret→"No retornable" | not mentioned→null
- Brand: fix abbreviations (MH→Mahou, SM→San Miguel, ALH→Alhambra, M*→Mahou, SCH→Schweppes, SOLAN→Solan de Cabras). Unknown brand→null+flag
- tipo_producto: Cerveza|Agua|Agua con Gas|Vino Blanco|Vino Tinto|Vino Rosado|Cava|Refresco|Aceite|Limpieza|Snack|Lácteo|Licor|Whisky|Gin|Ron|Vodka|Tequila|Vermut|Café|Té|Infusión|Zumo|Brandy|Papel|Vajilla|Conserva|Condimento|Sirope|Otro
- Encoding: ¡|¦|­ appearing in Spanish words → fix to Ñ (e.g. MU¡OZ→MUÑOZ)
- Strip leading -, *, < from names
- 1B|1BT|1BOTE = unit count, never volume
- Ages (12 AÑOS, 3YO) go in variedad, not volume
- Non-beverage missing brand is NORMAL — do not penalize

CONFIDENCE (calculate per product):
BEVERAGES: brand matched=+35pts | volume found=+25pts | tipo identified=+25pts | formato=+15pts → divide by 100
NON-BEVERAGES: tipo identified=+40pts | volume/weight=+35pts | variedad=+25pts → divide by 100

descripcion_normalizada: tipo + marca + variedad + volumen + formato + retornabilidad (skip nulls)
Example: "Cerveza Mahou 5 Estrellas 33 cl Vidrio Retornable"
For non-beverages without brand, still generate: "Limpieza Manual 5 l"
Only null if tipo cannot be determined at all.

Flag requiere_revision_humana=true if: confidence<0.65 OR beverage with no brand OR completely ambiguous

Return ONLY a valid JSON array, no markdown, no explanation. Each object must have exactly:
tipo_producto, marca_detectada, marca_normalizada, variedad, volumen, formato, retornabilidad, descripcion_normalizada, confianza, requiere_revision_humana, avisos (string array)"""

    user = f"Normalize these {len(names)} product descriptions. Return exactly {len(names)} JSON objects in the same order.\n\n" + "\n".join(f"{i+1}. {n}" for i, n in enumerate(names))

    fallback = {"tipo_producto": None, "marca_detectada": None, "marca_normalizada": None,
                "variedad": None, "volumen": None, "formato": None, "retornabilidad": None,
                "descripcion_normalizada": None, "confianza": 0.3,
                "requiere_revision_humana": True, "avisos": ["API error — needs review"]}

    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=system,
                messages=[{"role": "user", "content": user}]
            )
            text = response.content[0].text
            try:
                return json.loads(text)
            except:
                m = re.search(r'\[[\s\S]*\]', text)
                if m:
                    return json.loads(m.group(0))
                return [fallback.copy() for _ in names]
        except Exception as e:
            if attempt == 2:
                return [fallback.copy() for _ in names]
            time.sleep(2 * (attempt + 1))
    return [fallback.copy() for _ in names]

def to_excel_bytes(df_main, df_review):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_main.to_excel(writer, index=False, sheet_name="Normalized Catalog")
        df_review.to_excel(writer, index=False, sheet_name="Needs Review")
    return buf.getvalue()

# ── UI ──────────────────────────────────────────────────────────────────────

st.title("🍺 Nexho Product Normalizer")
st.markdown("**Mahou San Miguel** · AI-powered catalog normalization using Claude")
st.divider()

# API Key
st.subheader("Step 1 — Anthropic API Key")
api_key = st.text_input("Enter your API key", type="password", placeholder="sk-ant-...")
if api_key:
    st.success("✓ API key set")

st.divider()

# Brand master
st.subheader("Step 2 — Upload brand master list")
brands_file = st.file_uploader("Upload brands.xlsx", type=["xlsx","xls"], key="brands")
brand_list = []
if brands_file:
    brands_df = pd.read_excel(brands_file, dtype=str)
    brand_list = load_brands_from_df(brands_df)
    st.success(f"✓ {len(brand_list)} brands loaded")

st.divider()

# Catalog
st.subheader("Step 3 — Upload your product catalog")
st.caption("Excel or CSV — any format. Auto-detects your product name column.")
catalog_file = st.file_uploader("Upload catalog file", type=["xlsx","xls","csv"], key="catalog")
dist_id = st.text_input("Your distributor ID (optional)", placeholder="e.g. 22")
batch_size = st.slider("Batch size (products per API call)", min_value=5, max_value=25, value=15)

st.divider()

st.subheader("Step 4 — Normalize")

ready = api_key and brands_file and catalog_file

if st.button("▶ Run AI normalization", type="primary", disabled=not ready):
    client = anthropic.Anthropic(api_key=api_key)
    
    df = pd.read_csv(catalog_file, dtype=str) if catalog_file.name.endswith(".csv") else pd.read_excel(catalog_file, dtype=str)
    name_col = detect_name_col(df)
    id_col = next((c for c in df.columns if c.lower() == "id_distributor"), None)
    names = df[name_col].fillna("").astype(str).str.strip().tolist()

    progress = st.progress(0, text="Starting AI normalization...")
    status = st.empty()
    all_results = []

    chunks = [names[i:i+batch_size] for i in range(0, len(names), batch_size)]
    
    for ci, chunk in enumerate(chunks):
        pct = ci / len(chunks)
        progress.progress(pct, text=f"Processing batch {ci+1} of {len(chunks)} ({ci*batch_size}/{len(names)} products)...")
        results = normalize_batch_claude(client, chunk, brand_list)
        all_results.extend(results)
        if ci < len(chunks) - 1:
            time.sleep(0.3)

    progress.progress(1.0, text="Done!")

    out_rows = []
    for i, (name, result) in enumerate(zip(names, all_results)):
        row = {}
        if id_col:
            row["id_distributor"] = df[id_col].iloc[i]
        elif dist_id:
            row["id_distributor"] = dist_id
        row["des_product_name"] = name
        row["tipo_producto"] = result.get("tipo_producto") or ""
        row["marca_detectada"] = result.get("marca_detectada") or ""
        row["marca_normalizada"] = result.get("marca_normalizada") or ""
        row["variedad"] = result.get("variedad") or ""
        row["volumen"] = result.get("volumen") or ""
        row["formato"] = result.get("formato") or ""
        row["retornabilidad"] = result.get("retornabilidad") or ""
        row["des_product_description_normalized"] = result.get("descripcion_normalizada") or ""
        row["confianza"] = result.get("confianza", 0.3)
        row["requiere_revision_humana"] = result.get("requiere_revision_humana", True)
        row["avisos"] = "; ".join(result.get("avisos", [])) if result.get("avisos") else ""
        out_rows.append(row)

    out_df = pd.DataFrame(out_rows)
    review_df = out_df[out_df["requiere_revision_humana"] == True].copy()
    
    total = len(out_df)
    clean = (~out_df["requiere_revision_humana"]).sum()
    needs_review = out_df["requiere_revision_humana"].sum()
    avg_conf = out_df["confianza"].mean()
    branded = (out_df["marca_normalizada"] != "").sum()

    st.divider()
    st.subheader("Results")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total", total)
    col2.metric("Fully normalized", f"{clean} ({clean/total*100:.0f}%)")
    col3.metric("Needs review", f"{needs_review} ({needs_review/total*100:.0f}%)")
    col4.metric("Brands matched", f"{branded} ({branded/total*100:.0f}%)")

    st.dataframe(out_df[["des_product_name","des_product_description_normalized","confianza","requiere_revision_humana"]].head(50), use_container_width=True)

    excel_bytes = to_excel_bytes(out_df, review_df)
    st.download_button(
        label="⬇ Download Excel output",
        data=excel_bytes,
        file_name=f"nexho_normalized_{dist_id or 'output'}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    st.caption("Two sheets: **Normalized Catalog** (all) + **Needs Review** (flagged only)")

elif not ready:
    missing = []
    if not api_key: missing.append("API key")
    if not brands_file: missing.append("brands.xlsx")
    if not catalog_file: missing.append("catalog file")
    st.info(f"Still needed: {', '.join(missing)}")

st.divider()
st.caption("Built by IE Innovation LAB · Mahou San Miguel Nexho Project · 2026 · Powered by Claude")
