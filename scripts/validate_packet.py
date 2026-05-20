from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python scripts/validate_packet.py <packet.json> <schema.json>")
        return 2
    packet_path = Path(sys.argv[1])
    schema_path = Path(sys.argv[2])
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if jsonschema is None:
        print("jsonschema not installed. Install it or validate via Pydantic models.")
        return 1
    jsonschema.validate(instance=packet, schema=schema)
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
