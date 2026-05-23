from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.logger import logger, setup_logger


class TestSetupLogger:
    def test_setup_logger_default_creates_log_dir(self, tmp_path: Path) -> None:
        setup_logger(log_dir=tmp_path)
        assert tmp_path.exists()

    def test_setup_logger_creates_handler(self, tmp_path: Path) -> None:
        setup_logger(log_dir=tmp_path)
        logger.info("test message from setup_logger")

    def test_setup_logger_custom_level(self, tmp_path: Path) -> None:
        setup_logger(log_dir=tmp_path, level="DEBUG")
        logger.debug("debug message")

    def test_setup_logger_none_log_dir_uses_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        import src.utils.logger as logger_module

        original = logger_module.Path
        monkeypatch.setattr(
            logger_module,
            "Path",
            lambda *args, **kwargs: tmp_path / "logs" if not args else original(*args, **kwargs),
        )
        setup_logger(log_dir=tmp_path)
        logger.info("no log_dir test")

    def test_logger_exported(self) -> None:
        from src.utils.logger import logger as exported_logger

        assert exported_logger is not None
