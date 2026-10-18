"""Aplicação única do Monitor de Preços (multipágina).

Rodar:
    uv run streamlit run src/monitor_precos/dashboard/app.py

Este arquivo é a ENTRADA: configura a janela e monta o menu lateral. Cada página
continua no seu próprio arquivo (caminhos relativos a esta pasta).
"""

import streamlit as st

st.set_page_config(page_title="Monitor de Preços", page_icon="🛒", layout="wide")

pages = [
    st.Page("painel_app.py", title="Painel de preços", icon="📊", default=True),
    st.Page("importar_app.py", title="Importar encartes", icon="📥"),
    st.Page("de_para_app.py", title="De-para (vínculos)", icon="🔗"),
]
st.navigation(pages).run()
