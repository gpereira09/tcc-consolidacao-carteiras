"""
Identidade visual da aplicação.

Direção: relatório de patrimônio de banca privada — sóbrio, denso em
informação e sem ornamento. A cor é usada como dado, não como decoração:
o azul-tinta estrutura, e só ganho/perda e o eixo líquido/ilíquido têm
cor própria, porque só eles carregam significado que o número sozinho
não transmite.

Paleta
    tinta      #17253F   estrutura, títulos, texto forte
    grafite    #55607A   texto secundário e rótulos
    papel      #FAFAF8   fundo
    linha      #E3E1DA   divisores e bordas
    verde      #1E6B4F   resultado positivo
    vinho      #9E3B3B   resultado negativo
    latão      #8C6D2F   acento de destaque (usado com parcimônia)

Tipografia
    títulos e números de destaque em serifa (Source Serif 4), que é a
    convenção do relatório financeiro impresso; dados tabulares e
    interface em sans humanista.
"""
from __future__ import annotations

import streamlit as st

TINTA = "#17253F"
GRAFITE = "#55607A"
PAPEL = "#FAFAF8"
LINHA = "#E3E1DA"
VERDE = "#1E6B4F"
VINHO = "#9E3B3B"
LATAO = "#8C6D2F"

SEQUENCIA = ["#17253F", "#2F4468", "#4A6490", "#6E86AC", "#98A9C4", "#8C6D2F", "#B39355"]

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif;
}}
.stApp {{ background: {PAPEL}; }}

h1, h2, h3 {{
    font-family: 'Source Serif 4', Georgia, serif !important;
    color: {TINTA} !important;
    letter-spacing: -0.01em;
    font-weight: 600 !important;
}}
h1 {{ font-size: 1.9rem !important; }}
h2 {{ font-size: 1.35rem !important; margin-top: 1.6rem !important; }}
h3 {{ font-size: 1.05rem !important; }}

section[data-testid="stSidebar"] {{
    background: {TINTA};
    border-right: none;
}}
/* Texto do sidebar em claro — exceto dentro de controles, que têm fundo
   claro próprio e ficariam ilegíveis com texto branco. */
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {{ color: #E8EAEF !important; }}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {{ color: #FFFFFF !important; }}

/* Caixa de seleção e botão do sidebar: fundo translúcido e texto claro. */
section[data-testid="stSidebar"] div[data-baseweb="select"] > div {{
    background: rgba(255,255,255,0.10) !important;
    border-color: rgba(255,255,255,0.28) !important;
}}
section[data-testid="stSidebar"] div[data-baseweb="select"] * {{
    color: #FFFFFF !important;
}}
section[data-testid="stSidebar"] .stButton button {{
    background: transparent !important;
    color: #E8EAEF !important;
    border: 1px solid rgba(255,255,255,0.35) !important;
}}
section[data-testid="stSidebar"] .stButton button:hover {{
    background: rgba(255,255,255,0.10) !important;
    border-color: #FFFFFF !important;
}}
section[data-testid="stSidebar"] hr {{ border-color: rgba(255,255,255,0.18) !important; }}

/* Rádio de navegação. Os seletores se apoiam em data-testid e data-selected,
   que são estáveis; as classes emotion do Streamlit mudam entre versões e
   não servem de gancho. O latão marca a opção ativa — único acento da tela. */
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div > div:first-child {{
    background-color: transparent !important;
    border: 1.5px solid rgba(255,255,255,0.45) !important;
}}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div > div:first-child > div {{
    background-color: transparent !important;
}}
section[data-testid="stSidebar"] label[data-selected="true"] > div > div > div:first-child {{
    border-color: #C8A44E !important;
}}
section[data-testid="stSidebar"] label[data-selected="true"] > div > div > div:first-child > div {{
    background-color: #C8A44E !important;
}}
section[data-testid="stSidebar"] label[data-selected="true"] p {{
    color: #FFFFFF !important;
    font-weight: 600 !important;
}}
.sidebar-identidade {{
    color: #9FAAC0 !important;
    font-size: 0.82rem;
    line-height: 1.45;
    margin-bottom: 1rem;
}}

/* Bloco de patrimônio: o único elemento com peso tipográfico alto. */
.patrimonio {{
    font-family: 'Source Serif 4', Georgia, serif;
    font-size: 3.1rem;
    line-height: 1.05;
    color: {TINTA};
    font-weight: 600;
    font-variant-numeric: tabular-nums;
}}
.patrimonio-rotulo {{
    color: {GRAFITE};
    font-size: 0.82rem;
    margin-bottom: 0.1rem;
}}
.patrimonio-moeda {{
    font-size: 1.25rem;
    color: {GRAFITE};
    margin-right: 0.35rem;
    font-weight: 400;
}}

/* Barra líquido/ilíquido — o eixo que define esta classe de investidor. */
.barra {{ display: flex; height: 10px; border-radius: 2px; overflow: hidden; margin: 0.9rem 0 0.4rem; }}
.barra span {{ display: block; height: 100%; }}
.barra-legenda {{ color: {GRAFITE}; font-size: 0.8rem; display: flex; gap: 1.4rem; }}
.barra-legenda b {{ color: {TINTA}; font-weight: 600; }}

.positivo {{ color: {VERDE}; font-weight: 600; font-variant-numeric: tabular-nums; }}
.negativo {{ color: {VINHO}; font-weight: 600; font-variant-numeric: tabular-nums; }}
.neutro   {{ color: {GRAFITE}; font-variant-numeric: tabular-nums; }}

.ficha {{
    border: 1px solid {LINHA};
    border-left: 3px solid {TINTA};
    background: #FFFFFF;
    padding: 0.85rem 1.05rem;
    margin-bottom: 0.6rem;
}}
.ficha-rotulo {{ color: {GRAFITE}; font-size: 0.78rem; }}
.ficha-valor {{
    color: {TINTA}; font-size: 1.4rem; font-weight: 600;
    font-family: 'Source Serif 4', Georgia, serif;
    font-variant-numeric: tabular-nums;
}}

.nota {{
    color: {GRAFITE}; font-size: 0.82rem;
    border-top: 1px solid {LINHA}; padding-top: 0.6rem; margin-top: 1.4rem;
}}
div[data-testid="stDataFrame"] {{ border: 1px solid {LINHA}; }}
.stButton button {{ border-radius: 2px; font-weight: 500; }}
</style>
"""


def aplicar() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def layout_grafico(**extra) -> dict:
    """Layout Plotly comum a todos os gráficos: fundo transparente e fonte Inter."""
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": dict(family="Inter, sans-serif", color=GRAFITE, size=12),
        **extra,
    }
