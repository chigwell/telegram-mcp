import os
from pathlib import Path
import pytest
from telegram_mcp import runtime
import main


def test_parse_allowed_roots_env_empty_and_none():
    assert runtime._parse_allowed_roots_env(None) == []
    assert runtime._parse_allowed_roots_env("") == []
    assert runtime._parse_allowed_roots_env("   ") == []


def test_parse_allowed_roots_env_delimiters():
    # Semicolon delimiter (universal, safe with Windows drive letters)
    assert runtime._parse_allowed_roots_env("C:\\path1;D:\\path2") == ["C:\\path1", "D:\\path2"]
    # Comma delimiter
    assert runtime._parse_allowed_roots_env("/data/one,/data/two") == ["/data/one", "/data/two"]
    # Colon delimiter on POSIX-like paths
    assert runtime._parse_allowed_roots_env("/data/one:/data/two") == ["/data/one", "/data/two"]
    # Strips quotes and surrounding whitespace
    assert runtime._parse_allowed_roots_env(" \"/path/one\" ; '/path/two' ") == [
        "/path/one",
        "/path/two",
    ]


def test_configure_allowed_roots_from_env_variable(tmp_path, monkeypatch):
    dir1 = tmp_path / "media"
    dir2 = tmp_path / "downloads"
    dir1.mkdir()
    dir2.mkdir()

    env_val = f"{dir1};{dir2}"
    monkeypatch.setenv("TELEGRAM_ALLOWED_ROOTS", env_val)

    runtime._configure_allowed_roots_from_cli([])
    assert runtime.SERVER_ALLOWED_ROOTS == [dir1.resolve(), dir2.resolve()]


def test_combine_cli_args_and_env_roots(tmp_path, monkeypatch):
    cli_dir = tmp_path / "cli_root"
    env_dir = tmp_path / "env_root"
    cli_dir.mkdir()
    env_dir.mkdir()

    monkeypatch.setenv("TELEGRAM_ALLOWED_ROOTS", str(env_dir))

    runtime._configure_allowed_roots_from_cli([str(cli_dir)])
    assert runtime.SERVER_ALLOWED_ROOTS == [cli_dir.resolve(), env_dir.resolve()]


def test_env_roots_missing_directory_auto_created(tmp_path, monkeypatch):
    missing_dir = tmp_path / "auto_created_env_root"
    assert not missing_dir.exists()

    monkeypatch.setenv("TELEGRAM_ALLOWED_ROOTS", str(missing_dir))

    runtime._configure_allowed_roots_from_cli([])
    assert missing_dir.exists()
    assert runtime.SERVER_ALLOWED_ROOTS == [missing_dir.resolve()]


def test_main_alias_syncs_env_roots(tmp_path, monkeypatch):
    test_dir = tmp_path / "main_sync_root"
    test_dir.mkdir()

    monkeypatch.setenv("TELEGRAM_ALLOWED_ROOTS", str(test_dir))

    main._configure_allowed_roots_from_cli([])
    assert main.SERVER_ALLOWED_ROOTS == [test_dir.resolve()]
    assert runtime.SERVER_ALLOWED_ROOTS == [test_dir.resolve()]
