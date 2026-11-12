"""gaik-hackathon server.

Importing this package loads `<repo>/.env` into the process environment (real environment
variables win) before any module can import gaik, which reads the environment at import.
"""

from app.core import settings as _settings  # noqa: F401  (side effect: loads .env first)
