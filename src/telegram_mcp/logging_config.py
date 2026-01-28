"""Logging configuration for telegram-mcp."""

import logging
import os

from pythonjsonlogger import jsonlogger


def setup_logger() -> logging.Logger:
    """Setup and return the configured logger."""
    logger = logging.getLogger("telegram_mcp")
    logger.setLevel(logging.ERROR)

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)

    # Create file handler with absolute path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_file_path = os.path.join(script_dir, "mcp_errors.log")

    try:
        file_handler = logging.FileHandler(log_file_path, mode="a")
        file_handler.setLevel(logging.ERROR)

        # Console formatter remains in the old format
        console_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
        console_handler.setFormatter(console_formatter)

        # File formatter is now JSON
        json_formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        file_handler.setFormatter(json_formatter)

        # Add handlers to logger
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        logger.info(f"Logging initialized to {log_file_path}")
    except Exception as log_error:
        print(f"WARNING: Error setting up log file: {log_error}")
        # Fallback to console-only logging
        logger.addHandler(console_handler)
        logger.error(f"Failed to set up log file handler: {log_error}")

    return logger


logger = setup_logger()
