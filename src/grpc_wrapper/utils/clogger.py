# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import logging

from datetime import datetime
from pathlib import Path
from typing import Optional


class CustomFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None, style="%", prefix="ROOT"):
        super().__init__(fmt, datefmt, style)
        self.prefix = prefix

    def format(self, record):
        record.prefix = self.prefix
        record.asctime = datetime.now().strftime(self.datefmt)
        return super().format(record)


def create_logger(
    logger_name: str,
    prefix: str,
    loglevel: int = logging.DEBUG,
    filepath: Optional[Path] = None,
) -> logging.Logger:
    # if the logger already exists we can just return it, adding another handler
    # would lead to double messages
    if logger_name in logging.Logger.manager.loggerDict:
        return logging.getLogger(logger_name)

    # create new logger
    fmt = "%(prefix)s | %(levelname)-7s | %(asctime)s.%(msecs)03d | %(message)s"
    formatter = CustomFormatter(
        fmt=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        prefix=prefix,
    )

    logger = logging.getLogger(logger_name)
    logger.propagate = False

    # always add stream handler
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if filepath is not None:
        filepath.mkdir(exist_ok=True)
        handler = logging.FileHandler(filepath / logger_name)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(loglevel)
    return logger


def create_file_logger(
    filename: str,
    filepath: Path,
    prefix: str,
    loglevel: int,
) -> logging.Logger:
    return create_logger(
        logger_name=filename,
        prefix=prefix,
        loglevel=loglevel,
        filepath=filepath,
    )
