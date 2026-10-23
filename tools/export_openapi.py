"""Export the runtime FastAPI contract to the checked-in JSON artifact."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app


destination = Path(__file__).resolve().parents[1] / "openapi.json"
destination.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(destination)
