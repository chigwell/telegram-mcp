"""Docker and package installation must declare the same runtime dependencies."""

from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10; installed by the existing dev dependencies.
    import tomli as tomllib


def test_docker_requirements_match_project_dependencies():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [
        line.strip()
        for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert sorted(requirements) == sorted(project["project"]["dependencies"])
