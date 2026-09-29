"""Install this local setup's service certificates from stdin, without host mounts."""

import json
import sys
from pathlib import Path

files = json.load(sys.stdin)
expected = {"api/service.crt", "api/service.key", "inference/service.crt", "inference/service.key", "ca/ca.crt"}
if set(files) != expected:
    raise ValueError("expected exactly the two service certificates/keys and public CA")
for name, content in files.items():
    target = Path("/certificates") / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(content)
    temporary.chmod(0o600 if name.endswith(".key") else 0o444)
    temporary.replace(target)
