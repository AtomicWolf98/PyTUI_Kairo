"""Compatibility entry point retained for legacy `python main.py` usage.

The canonical CLI entry point is `kairo:main` (defined in pyproject.toml).
"""
from kairo import main

if __name__ == "__main__":
    main()
