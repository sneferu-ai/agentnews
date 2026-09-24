"""Setuptools build script for AgentNews.

This file is the sole packaging configuration for the project. It replaces
the former ``pyproject.toml``, which declared ``requires = ["setuptools>=68"]``
and a PEP 621 ``[project]`` table — both incompatible with the runtime
environment (Python 3.9.6, pip 21.2.4, setuptools 58.0.4):

1. setuptools 58 predates PEP 621, so the ``[project]`` table was never
   read — a ``pyproject.toml``-only project would install as an unnamed
   phantom package.
2. pip 21.2.4 predates PEP 660. When ``pyproject.toml`` is present, pip
   uses PEP 517 build isolation (installing a newer setuptools into the
   isolated build env) and then falls back to ``setup.py develop`` for
   editable installs, passing ``--user --prefix=`` (empty prefix). On
   setuptools >=62 an empty ``--prefix=`` overrides ``--user`` and targets
   the read-only system site-packages — a permission-denied exit 1.

Removing ``pyproject.toml`` makes pip use the legacy ``setup.py`` path with
the system setuptools, which honours ``--user`` correctly. ``pytest.ini``
preserves the test-path config that previously lived in
``[tool.pytest.ini_options]``.

The ``develop`` subclass below is a defensive safety net: it pre-sets
``install_dir`` to the user site when ``--user`` is active so that any
future environment running a newer setuptools with the same empty-prefix
quirk cannot redirect the editable install into the read-only system tree.
"""
from setuptools import find_packages, setup
from setuptools.command.develop import develop as _develop


class develop(_develop):
    """``develop`` that pins the user site under ``--user`` even when pip
    passes an empty ``--prefix=`` (pip 21.2.4 PEP 660 fallback)."""

    def finalize_options(self):
        if getattr(self, "user", False):
            import site as _site

            if _site.ENABLE_USER_SITE:
                # Set install_dir so set_undefined_options('install_lib', ...)
                # inherits the user site, not the system site.
                self.install_dir = _site.getusersitepackages()
                # Clear prefix so newer setuptools (>=62) cannot treat an empty
                # --prefix= as an explicit prefix override of --user.
                self.prefix = None
        super().finalize_options()


setup(
    name="agentnews",
    version="0.1.0",
    description="Paid machine-readable feed of Sneferu-produced research bundles",
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "fastapi>=0.100",
        "uvicorn[standard]>=0.20",
        "httpx>=0.24",
        "jinja2>=3.0",
        "pydantic>=2.0",
    ],
    extras_require={
        "dev": ["pytest", "build", "wheel"],
    },
    entry_points={
        "console_scripts": [
            "agentnews = agentnews.cli:main",
        ],
    },
    include_package_data=True,
    package_data={
        "agentnews": [
            "templates/*.html",
            "templates/*.xml",
            "static/*",
            "migrations/*.sql",
        ],
    },
    cmdclass={"develop": develop},
)
