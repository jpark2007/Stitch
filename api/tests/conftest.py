"""Make `import schemas` and `from screenplay import ...` work when pytest runs from api/."""

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = API_DIR.parent

if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
