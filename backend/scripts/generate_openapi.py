"""Generate openapi.yaml matching running service conforming to Integration Standard #2."""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from app.main import app

def generate_spec():
    spec = app.openapi()
    spec["openapi"] = "3.1.0"
    output_path = Path("openapi.yaml")
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(spec, f, sort_keys=False, allow_unicode=True)
    print(f"Successfully generated {output_path.resolve()} (OpenAPI 3.1)")

if __name__ == "__main__":
    generate_spec()
