"""``python -m agent24.evals`` -- validate and execute the eval registry."""

from __future__ import annotations

import sys

from .runner import main

if __name__ == "__main__":
    sys.exit(main())
