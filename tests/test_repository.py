"""Testes do repositório com um banco DuckDB temporário (arquivo em tmp_path)."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from monitor_precos.collectors.base import CollectedFile, ContentType
from monitor_precos.storage.repository import Repository


@pytest.fixture
def repo(settings):
    with Repository(settings.database_path, settings.data_dir) as repository:
        yield repository


def _file(settings, sha: str, day: str = "2026-10-11", name: str = "a.webp") -> CollectedFile:
    path = settings.raw_dir / "serve_mais" / day / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(sha.encode())
    return CollectedFile(
        competitor="serve_mais",
        source_url=f"https://site/{name}",
        local_path=path,
        content_type=ContentType.IMAGE,
        sha256=sha,
        collected_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        campaign="out26",
        valid_from=date(2026, 9, 23),
        valid_until=date(2026, 10, 2),
    )


def test_cria_as_quatro_tabelas(repo) -> None:
    tabelas = {row[0] for row in repo.connection.execute("SHOW TABLES").fetchall()}
    assert {"coletas", "ofertas_extraidas", "produtos_internos", "de_para_concorrente"} <= tabelas


def test_init_schema_e_idempotente(repo) -> None:
    repo.init_schema()  # rodar de novo não pode dar erro nem apagar nada
    repo.init_schema()


def test_grava_coleta_com_metadados(repo, settings) -> None:
    result = repo.save_collections([_file(settings, "h1")])

    assert len(result.inserted) == 1
    [row] = repo.connection.execute(
        "SELECT caminho_arquivo, campanha, validade_fim, status_extracao FROM coletas"
    ).fetchall()
    assert row == ("raw/serve_mais/2026-10-11/a.webp", "out26", date(2026, 10, 2), "pendente")


def test_mesmo_conteudo_nao_duplica(repo, settings) -> None:
    primeiro = _file(settings, "h1", day="2026-10-11")
    repetido_outro_dia = _file(settings, "h1", day="2026-10-12")

    repo.save_collections([primeiro])
    result = repo.save_collections([repetido_outro_dia])

    assert result.inserted == []
    [(arquivo, caminho_guardado)] = result.duplicates
    assert caminho_guardado == settings.data_dir / "raw/serve_mais/2026-10-11/a.webp"
    assert repo.connection.execute("SELECT COUNT(*) FROM coletas").fetchone() == (1,)


def test_fila_de_pendentes_e_mudanca_de_status(repo, settings) -> None:
    repo.save_collections(
        [_file(settings, "h1", name="a.webp"), _file(settings, "h2", name="b.webp")]
    )

    pendentes = repo.pending_collections()
    assert [p.sha256 for p in pendentes] == ["h1", "h2"]
    assert pendentes[0].local_path.read_bytes() == b"h1"  # caminho volta absoluto

    repo.set_extraction_status(pendentes[0].id, "extraido")
    assert [p.sha256 for p in repo.pending_collections()] == ["h2"]
    assert repo.pending_collections(competitor="assai") == []


def test_status_invalido_e_recusado(repo) -> None:
    with pytest.raises(ValueError):
        repo.set_extraction_status(1, "pronto")


def test_banco_recusa_preco_invalido(repo) -> None:
    """As regras CHECK protegem os dados mesmo se alguém gravar direto via SQL."""
    with pytest.raises(duckdb.ConstraintException):
        repo.connection.execute(
            """INSERT INTO produtos_internos
               (id, descricao, categoria, curva_abc, preco_venda, atualizado_em)
               VALUES (1, 'Arroz 5kg', 'Mercearia', 'A', 0, now())"""
        )


def test_transacao_desfaz_tudo_se_der_erro(repo, settings) -> None:
    valido = _file(settings, "h1")
    # replace() cria uma cópia com um campo alterado; competitor=None viola o NOT NULL.
    invalido = replace(_file(settings, "h2", name="b.webp"), competitor=None)

    with pytest.raises(duckdb.ConstraintException):
        repo.save_collections([valido, invalido])

    assert repo.connection.execute("SELECT COUNT(*) FROM coletas").fetchone() == (0,)


def test_banco_persiste_entre_conexoes(settings) -> None:
    with Repository(settings.database_path, settings.data_dir) as repo:
        repo.save_collections([_file(settings, "h1")])
    with Repository(settings.database_path, settings.data_dir) as repo:
        assert len(repo.pending_collections()) == 1
    assert Path(settings.database_path).exists()


# ------------------------------------------------------------------ Etapa 3: execuções
def test_registro_de_execucao(repo) -> None:
    run_id = repo.start_run("serve_mais")
    status, fim = repo.connection.execute(
        "SELECT status, finalizado_em FROM execucoes WHERE id = ?", [run_id]
    ).fetchone()
    assert (status, fim) == ("em_andamento", None)  # gravado ANTES de coletar

    repo.finish_run(run_id, "parcial", inserted=10, duplicates=2, failures=1, message="1 falha")
    linha = repo.connection.execute(
        "SELECT status, arquivos_novos, arquivos_repetidos, falhas, mensagem FROM execucoes"
    ).fetchone()
    assert linha == ("parcial", 10, 2, 1, "1 falha")


def test_finish_run_recusa_status_invalido(repo) -> None:
    run_id = repo.start_run("serve_mais")
    with pytest.raises(ValueError):
        repo.finish_run(run_id, "ok")


def test_latest_valid_until(repo, settings) -> None:
    assert repo.latest_valid_until("serve_mais") is None
    repo.save_collections([_file(settings, "h1")])
    assert repo.latest_valid_until("serve_mais") == date(2026, 10, 2)


def test_known_source_urls(repo, settings) -> None:
    assert repo.known_source_urls("serve_mais") == set()
    repo.save_collections([_file(settings, "h1", name="a.webp")])
    assert repo.known_source_urls("serve_mais") == {"https://site/a.webp"}
    assert repo.known_source_urls("assai") == set()


def test_arquivo_sumido_e_restaurado(repo, settings) -> None:
    original = _file(settings, "h1", day="2026-10-11")
    repo.save_collections([original])
    [coleta] = repo.pending_collections()
    repo.set_extraction_status(coleta.id, "erro")  # a extração falhou por falta do arquivo

    original.local_path.unlink()  # alguém apagou a pasta
    assert repo.known_source_urls("serve_mais") == set()  # então o coletor baixa de novo

    nova_copia = _file(settings, "h1", day="2026-10-13")
    result = repo.save_collections([nova_copia])

    assert result.repaired == [nova_copia]
    assert result.duplicates == []
    caminho, status = repo.connection.execute(
        "SELECT caminho_arquivo, status_extracao FROM coletas"
    ).fetchone()
    assert caminho == "raw/serve_mais/2026-10-13/a.webp"
    assert status == "pendente"  # voltou para a fila
