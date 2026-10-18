"""Página de importação manual de encartes (upload) + extração com Gemini.

Faz parte da aplicação em app.py:
    uv run streamlit run src/monitor_precos/dashboard/app.py
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from monitor_precos.collectors.upload import (
    KNOWN_COMPETITORS,
    UploadedFile,
    import_uploads,
    slugify,
)
from monitor_precos.config import get_settings
from monitor_precos.extractors.service import ExtractionService
from monitor_precos.storage.repository import Repository

settings = get_settings()
today = datetime.now(settings.tz).date()
OTHER = "Outro..."

st.session_state.setdefault("upload_round", 0)  # muda a key do uploader para limpá-lo

st.title("📥 Importar encartes")
st.caption(
    "Para concorrentes cujo site não permite coleta automática. Baixe o encarte no "
    "seu navegador (ou receba por WhatsApp) e envie aqui."
)
st.warning(
    "Envie apenas encartes PÚBLICOS de concorrentes: os arquivos são enviados à API do "
    "Google (Gemini). Nunca envie documentos internos da empresa."
)

# ------------------------------------------------------------------ 1. concorrente
with Repository(settings.database_path, settings.data_dir) as repo:
    existing = {row[0] for row in repo.connection.execute(
        "SELECT DISTINCT concorrente FROM coletas"
    ).fetchall()}  # fmt: skip

choice = st.selectbox("Concorrente", [*sorted(existing | set(KNOWN_COMPETITORS)), OTHER])
if choice == OTHER:
    competitor = slugify(st.text_input("Nome do concorrente", placeholder="ex.: Atacadão"))
    if competitor:
        st.caption(f"Será gravado como `{competitor}`.")
else:
    competitor = choice

# ------------------------------------------------------------------ 2. upload
st.subheader("1. Enviar arquivos")
campaign = st.text_input("Campanha (opcional)", placeholder="ex.: Encarte semanal 15/10")
valid_from = valid_until = None
if st.checkbox(
    "Informar a validade do encarte",
    help="Usada nas ofertas cujo card não traz a data impressa.",
):
    period = st.date_input("Válido de / até", value=(today, today), format="DD/MM/YYYY")
    valid_from, valid_until = (*period, *period)[:2] if period else (None, None)

uploads = st.file_uploader(
    "Encartes (PDF, JPG, PNG ou WEBP)",
    type=["pdf", "jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
    key=f"uploader-{st.session_state.upload_round}",
)

if st.button("📥 Importar", type="primary", disabled=not (uploads and competitor)):
    files = [UploadedFile(upload.name, upload.getvalue()) for upload in uploads]
    with Repository(settings.database_path, settings.data_dir) as repo:
        report = import_uploads(
            settings,
            repo,
            competitor,
            files,
            campaign=campaign.strip() or None,
            valid_from=valid_from,
            valid_until=valid_until,
        )
    st.session_state.last_report = (competitor, report)
    st.session_state.upload_round += 1  # uploader volta vazio
    st.cache_data.clear()  # painel e de-para enxergam os dados novos
    st.rerun()

if last := st.session_state.get("last_report"):
    reported_competitor, report = last
    if report.inserted:
        st.success(
            f"{len(report.inserted)} arquivo(s) novo(s) de `{reported_competitor}`: "
            + ", ".join(report.inserted)
        )
    if report.duplicates:
        st.info(
            f"{len(report.duplicates)} arquivo(s) já tinham sido importados antes "
            "(mesmo conteúdo): " + ", ".join(report.duplicates)
        )
    for problem in report.rejected:
        st.error(problem)

# ------------------------------------------------------------------ 3. extração
st.divider()
st.subheader("2. Extrair ofertas com o Gemini")
if not competitor:
    st.stop()

retry_errors = st.checkbox("Incluir arquivos que deram erro antes")
with Repository(settings.database_path, settings.data_dir) as repo:
    pending = len(
        repo.collections_to_extract(competitor, include_errors=retry_errors, limit=10_000)
    )

if summary := st.session_state.pop("last_extraction", None):
    st.success(
        f"Extração concluída: {summary.processed} arquivo(s) — {summary.extracted} ok, "
        f"{summary.review} para revisão, {summary.errors} com erro · "
        f"{summary.offers} oferta(s) gravada(s) · tokens {summary.input_tokens} + "
        f"{summary.output_tokens}"
    )
    st.page_link("de_para_app.py", label="Próximo passo: decidir os vínculos no De-para", icon="🔗")

if pending == 0:
    st.info(f"Nenhum arquivo de `{competitor}` aguardando extração.")
    st.stop()
if not settings.gemini_api_key:
    st.error("GEMINI_API_KEY não definida no arquivo .env.")
    st.stop()

st.write(f"**{pending}** arquivo(s) de `{competitor}` aguardando extração.")
limit = st.number_input(
    "Quantos extrair agora",
    min_value=1,
    max_value=pending,
    value=min(pending, settings.extraction_max_per_run),
)
st.caption(
    f"Cada imagem (ou página de PDF) é 1 chamada à API, com {settings.llm_delay_seconds:.0f} s "
    f"de pausa entre arquivos: tempo estimado acima de {int(limit * settings.llm_delay_seconds)} s. "
    "Não feche a página durante a extração."
)

if st.button("🤖 Extrair agora"):
    # Import tardio: o SDK do Google só carrega quando a extração é pedida.
    from monitor_precos.extractors.gemini import GeminiExtractor

    extractor = GeminiExtractor(settings.gemini_api_key, settings.gemini_model)
    with (
        st.spinner("Extraindo com o Gemini..."),
        Repository(settings.database_path, settings.data_dir) as repo,
    ):
        service = ExtractionService(
            repo,
            extractor,
            min_confidence=settings.extraction_min_confidence,
            delay_seconds=settings.llm_delay_seconds,
        )
        st.session_state.last_extraction = service.run(
            competitor=competitor, limit=int(limit), include_errors=retry_errors
        )
    st.cache_data.clear()
    st.rerun()
