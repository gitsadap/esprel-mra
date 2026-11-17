"""รายการแพ็กเกจใน pyproject.toml (Vercel ใช้) ต้องตรงกับ requirements.txt (Mac/CI ใช้)"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_dependencies_in_sync():
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as fh:
        req = {line.strip() for line in fh if line.strip() and not line.startswith("#")}
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        block = re.search(r"dependencies = \[(.*?)\n\]", fh.read(), re.S).group(1)
    deps = set(re.findall(r'"([^"]+)"', block))
    assert deps == req
    assert any(d.lower().startswith("flask") for d in deps)
