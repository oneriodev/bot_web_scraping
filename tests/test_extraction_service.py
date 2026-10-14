"""Testa o serviço de extração com um extrator FALSO: nenhuma chamada de API."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from monitor_precos.collectors.base import CollectedFile, ContentType
from monitor_precos.extractors.base import BaseExtractor, ExtractionOutput
from monitor_precos.extractors.service import ExtractionService
from monitor_precos.storage.repository import Repository

OFERTA_OK = {
    "product": "Arroz Branco Tipo 1",
    "brand": "Tio João",
    "quantity": 5,
    "unit": "KG",
    "regular_price": 32.9,
    "promo_price": "R$ 27,90",
    "special_condition": None,
    "valid_from": None,
    "valid_until": None,
    "confidence": 0.95,
}


class FakeExtractor(BaseExtractor):
    """Devolve respostas pré-programadas (ou levanta o erro programado)."""

    def __init__(self, respostas: list) -> None:
        self.respostas = list(respostas)
        self.chamadas = 0

    @property
    def engine(self) -> str:
        return "fake:teste"

    def extract(self, image: bytes, mime_type: str) -> ExtractionOutput:
        self.chamadas += 1
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


@pytest.fixture
def repo(settings):
    with Repository(settings.database_path, settings.data_dir) as repository:
        yield repository


def _coleta(repo, settings, sha: str, nome: str) -> None:
    path = settings.raw_dir / "serve_mais" / "2026-10-11" / nome
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"imagem-fake")
    repo.save_collections(
        [
            CollectedFile(
                competitor="serve_mais",
                source_url=f"https://site/{nome}",
                local_path=path,
                content_type=ContentType.IMAGE,
                sha256=sha,
                collected_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
                valid_from=date(2026, 9, 23),
                valid_until=date(2026, 10, 2),
            )
        ]
    )


def _servico(repo, extrator) -> ExtractionService:
    return ExtractionService(
        repo, extrator, min_confidence=0.8, delay_seconds=0, sleep=lambda s: None
    )


def _status_coletas(repo) -> list[str]:
    rows = repo.connection.execute("SELECT status_extracao FROM coletas ORDER BY id").fetchall()
    return [row[0] for row in rows]


def test_extrai_valida_e_grava(repo, settings) -> None:
    _coleta(repo, settings, "h1", "a.webp")
    extrator = FakeExtractor([ExtractionOutput(True, [OFERTA_OK], 100, 50)])

    resumo = _servico(repo, extrator).run(limit=10)

    assert (resumo.extracted, resumo.offers, resumo.input_tokens) == (1, 1, 100)
    linha = repo.connection.execute(
        "SELECT produto, unidade, preco_promocional, validade_fim, status_revisao, motor "
        "FROM ofertas_extraidas"
    ).fetchone()
    # Unidade padronizada, preço convertido e validade herdada da página:
    assert linha == (
        "Arroz Branco Tipo 1", "kg", Decimal("27.90"), date(2026, 10, 2), "automatico", "fake:teste"
    )  # fmt: skip
    assert _status_coletas(repo) == ["extraido"]


def test_confianca_baixa_vai_para_revisao(repo, settings) -> None:
    _coleta(repo, settings, "h1", "a.webp")
    duvidosa = {**OFERTA_OK, "confidence": 0.4}
    _servico(repo, FakeExtractor([ExtractionOutput(True, [duvidosa])])).run()

    assert _status_coletas(repo) == ["revisao"]
    status = repo.connection.execute("SELECT status_revisao FROM ofertas_extraidas").fetchone()
    assert status == ("pendente",)


def test_item_invalido_vai_para_revisao_sem_derrubar_os_outros(repo, settings) -> None:
    _coleta(repo, settings, "h1", "a.webp")
    sem_preco = {**OFERTA_OK, "promo_price": None}
    _servico(repo, FakeExtractor([ExtractionOutput(True, [OFERTA_OK, sem_preco])])).run()

    assert _status_coletas(repo) == ["revisao"]
    assert repo.connection.execute("SELECT COUNT(*) FROM ofertas_extraidas").fetchone() == (1,)


def test_imagem_que_nao_e_oferta_fica_como_extraida(repo, settings) -> None:
    _coleta(repo, settings, "h1", "logo.webp")
    _servico(repo, FakeExtractor([ExtractionOutput(False, [])])).run()
    assert _status_coletas(repo) == ["extraido"]


def test_erro_da_api_marca_erro_e_continua(repo, settings) -> None:
    _coleta(repo, settings, "h1", "a.webp")
    _coleta(repo, settings, "h2", "b.webp")
    extrator = FakeExtractor([RuntimeError("API fora do ar"), ExtractionOutput(True, [OFERTA_OK])])

    resumo = _servico(repo, extrator).run()

    assert (resumo.errors, resumo.extracted) == (1, 1)
    assert _status_coletas(repo) == ["erro", "extraido"]


def test_limite_controla_quantas_imagens_vao_para_a_api(repo, settings) -> None:
    for i in range(3):
        _coleta(repo, settings, f"h{i}", f"{i}.webp")
    extrator = FakeExtractor([ExtractionOutput(True, [OFERTA_OK])] * 3)

    _servico(repo, extrator).run(limit=2)

    assert extrator.chamadas == 2  # o teto de custo foi respeitado
    assert _status_coletas(repo) == ["extraido", "extraido", "pendente"]
