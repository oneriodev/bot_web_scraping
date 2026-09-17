# -*- coding: utf-8 -*-
"""
utils/logger_config.py

Configuração centralizada de logs do bot.
Gera logs simultaneamente:
    - No console (nível INFO em diante)
    - Em arquivo, com rotação diária (nível DEBUG em diante), dentro de logs/
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from config.settings import LOG_DIR


def _rotacionador_seguro(source: str, dest: str) -> None:
    """
    Substitui o rotator padrão do TimedRotatingFileHandler para não derrubar
    a rotação (nem poluir o console com tracebacks) quando o arquivo de log
    está momentaneamente bloqueado por outro processo — situação comum no
    Windows quando o próprio arquivo .log está aberto no VS Code ou em outro
    terminal. Se não for possível rotacionar agora, o bot simplesmente segue
    escrevendo no arquivo atual e tenta rotacionar de novo na próxima vez.
    """
    try:
        os.replace(source, dest)
    except PermissionError:
        pass


def configurar_logger(nome: str = "bot_scanner") -> logging.Logger:
    """
    Cria e retorna um logger configurado com saída para console e arquivo.

    Args:
        nome: nome do logger (geralmente o nome do módulo que o utiliza).

    Returns:
        Instância de logging.Logger pronta para uso.
    """
    logger = logging.getLogger(nome)

    # Evita duplicar handlers se a função for chamada mais de uma vez.
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    formato = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler de console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formato)
    logger.addHandler(console_handler)

    # Handler de arquivo com rotação diária, mantendo 14 dias de histórico
    caminho_log = Path(LOG_DIR) / "bot_scanner.log"
    file_handler = TimedRotatingFileHandler(
        filename=caminho_log,
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formato)
    file_handler.rotator = _rotacionador_seguro
    logger.addHandler(file_handler)

    return logger
