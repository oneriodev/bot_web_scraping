-- Esquema do banco analítico do Monitor de Preços.
--
-- Escrito em SQL padrão sempre que possível (sequências, DECIMAL, DATE, TIMESTAMP,
-- CHECK, UNIQUE, REFERENCES) para que a migração futura ao PostgreSQL seja simples.
-- "IF NOT EXISTS" torna o script idempotente: rodar de novo não apaga nada.

-- ---------------------------------------------------------------------------------
-- coletas: um arquivo bruto baixado (camada "raw"). Também é a FILA da extração:
-- a Etapa 4 processa só as linhas com status_extracao = 'pendente'.
-- ---------------------------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS seq_coletas START 1;

CREATE TABLE IF NOT EXISTS coletas (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_coletas'),
    concorrente      VARCHAR     NOT NULL,             -- slug: serve_mais, assai...
    origem_url       VARCHAR     NOT NULL,             -- URL ou inbox://...
    caminho_arquivo  VARCHAR     NOT NULL,             -- relativo à pasta data/
    tipo_conteudo    VARCHAR     NOT NULL,             -- image, pdf, html, json
    sha256           VARCHAR(64) NOT NULL UNIQUE,      -- garante: mesmo arquivo 1 vez só
    campanha         VARCHAR,
    validade_inicio  DATE,
    validade_fim     DATE,
    coletado_em      TIMESTAMP   NOT NULL,             -- UTC
    status_extracao  VARCHAR     NOT NULL DEFAULT 'pendente',
    CHECK (status_extracao IN ('pendente', 'extraido', 'revisao', 'erro'))
);

-- ---------------------------------------------------------------------------------
-- ofertas_extraidas: um produto lido de um encarte (tabela FATO). Preenchida na Etapa 4.
-- ---------------------------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS seq_ofertas START 1;

CREATE TABLE IF NOT EXISTS ofertas_extraidas (
    id                 INTEGER PRIMARY KEY DEFAULT nextval('seq_ofertas'),
    coleta_id          INTEGER       NOT NULL REFERENCES coletas (id),
    produto            VARCHAR       NOT NULL,          -- descrição como veio no encarte
    marca              VARCHAR,
    quantidade         DECIMAL(10, 3),                  -- 5 (kg), 900 (ml), 6 (un)...
    unidade            VARCHAR,                         -- kg, g, l, ml, un
    preco_regular      DECIMAL(10, 2),                  -- "de"
    preco_promocional  DECIMAL(10, 2) NOT NULL,         -- "por"
    condicao_especial  VARCHAR,                         -- "app clube", "leve 3 pague 2"
    validade_inicio    DATE,
    validade_fim       DATE,
    confianca          DECIMAL(4, 3),                   -- 0.000 a 1.000
    motor              VARCHAR       NOT NULL,          -- llm:gemini-flash, ocr:tesseract
    extraido_em        TIMESTAMP     NOT NULL,
    status_revisao     VARCHAR       NOT NULL DEFAULT 'automatico',
    CHECK (status_revisao IN ('automatico', 'pendente', 'aprovado', 'rejeitado')),
    CHECK (preco_promocional > 0),
    CHECK (confianca IS NULL OR confianca BETWEEN 0 AND 1)
);

-- ---------------------------------------------------------------------------------
-- produtos_internos: o cadastro da SUA rede (DIMENSÃO). No portfólio: dados FICTÍCIOS.
-- ---------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS produtos_internos (
    id             INTEGER       PRIMARY KEY,           -- código interno do produto
    codigo_barras  VARCHAR,
    descricao      VARCHAR       NOT NULL,
    marca          VARCHAR,
    categoria      VARCHAR       NOT NULL,
    quantidade     DECIMAL(10, 3),
    unidade        VARCHAR,
    curva_abc      CHAR(1)       NOT NULL,
    preco_venda    DECIMAL(10, 2) NOT NULL,
    atualizado_em  TIMESTAMP     NOT NULL,
    CHECK (curva_abc IN ('A', 'B', 'C')),
    CHECK (preco_venda > 0)
);

-- ---------------------------------------------------------------------------------
-- de_para_concorrente: vínculo APROVADO entre a descrição do concorrente e o produto
-- interno. Aprovado uma vez, vale para sempre (Etapa 6).
-- ---------------------------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS seq_de_para START 1;

CREATE TABLE IF NOT EXISTS de_para_concorrente (
    id                     INTEGER PRIMARY KEY DEFAULT nextval('seq_de_para'),
    concorrente            VARCHAR      NOT NULL,
    descricao_concorrente  VARCHAR      NOT NULL,       -- já normalizada
    produto_id             INTEGER      NOT NULL REFERENCES produtos_internos (id),
    score                  DECIMAL(5, 2),               -- similaridade 0-100 no momento da sugestão
    aprovado_em            TIMESTAMP    NOT NULL,
    UNIQUE (concorrente, descricao_concorrente)
);

-- ---------------------------------------------------------------------------------
-- execucoes: uma linha por execução de coleta (trilha de auditoria do pipeline).
-- O agendador (Etapa 9) consulta esta tabela para saber se algo falhou.
-- ---------------------------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS seq_execucoes START 1;

CREATE TABLE IF NOT EXISTS execucoes (
    id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_execucoes'),
    concorrente         VARCHAR   NOT NULL,
    iniciado_em         TIMESTAMP NOT NULL,             -- UTC
    finalizado_em       TIMESTAMP,                      -- NULL = ainda rodando (ou travou)
    status              VARCHAR   NOT NULL DEFAULT 'em_andamento',
    arquivos_novos      INTEGER   NOT NULL DEFAULT 0,
    arquivos_repetidos  INTEGER   NOT NULL DEFAULT 0,
    falhas              INTEGER   NOT NULL DEFAULT 0,
    mensagem            VARCHAR,                        -- erro ou aviso, quando houver
    CHECK (status IN ('em_andamento', 'sucesso', 'parcial', 'falha'))
);


-- ---------------------------------------------------------------------------------
-- chaves_ofertas: a chave normalizada de cada oferta (calculada em Python na Etapa 6).
-- Faz a ponte ofertas_extraidas → de_para_concorrente para o painel juntar em SQL.
-- ---------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chaves_ofertas (
    oferta_id    INTEGER PRIMARY KEY,
    concorrente  VARCHAR NOT NULL,
    chave        VARCHAR NOT NULL
);

-- ---------------------------------------------------------------------------------
-- de_para_rejeitados: produto do concorrente SEM equivalente no nosso cadastro.
-- Decidido uma vez, sai da fila de aprovação.
-- ---------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS de_para_rejeitados (
    concorrente            VARCHAR   NOT NULL,
    descricao_concorrente  VARCHAR   NOT NULL,
    rejeitado_em           TIMESTAMP NOT NULL,
    PRIMARY KEY (concorrente, descricao_concorrente)
);