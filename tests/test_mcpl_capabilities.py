"""Phase 1 regression tests for MCPL capability advertisement."""

from mcp.server.fastmcp import FastMCP

from telegram_mcp.mcpl import MCPL_VERSION
from telegram_mcp.mcpl.capabilities import (
    build_experimental_capabilities,
    build_mcpl_capabilities,
)


def test_mcpl_version_pinned():
    assert MCPL_VERSION == "0.4"


def test_capabilities_shape():
    caps = build_mcpl_capabilities()
    assert caps["version"] == "0.4"
    assert caps["pushEvents"] is True
    assert caps["channels"]["register"] is True
    assert caps["channels"]["publish"] is True
    assert caps["channels"]["lifecycle"] is True


def test_experimental_wrapper_namespaces_under_mcpl():
    wrapped = build_experimental_capabilities()
    assert set(wrapped.keys()) == {"mcpl"}
    assert wrapped["mcpl"] == build_mcpl_capabilities()


def test_capabilities_flow_through_create_initialization_options():
    """Smoke test: capabilities reach the actual initialize-response surface."""
    mcp = FastMCP("telegram-test")
    opts = mcp._mcp_server.create_initialization_options(
        experimental_capabilities=build_experimental_capabilities(),
    )
    assert opts.capabilities.experimental is not None
    assert "mcpl" in opts.capabilities.experimental
    assert opts.capabilities.experimental["mcpl"]["version"] == "0.4"
    assert opts.capabilities.experimental["mcpl"]["pushEvents"] is True
