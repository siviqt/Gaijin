"""Read a newly added driver from the single current kernel patch."""
from pathlib import Path

PATCH = Path(__file__).resolve().parents[1] / "linux/patches/0001-gaijin-esp32p4.patch"


def added_file(path):
    section = PATCH.read_text().split(f"+++ b/{path}\n", 1)[1].split("\n--- ", 1)[0]
    if not section.startswith("@@ -0,0 +1,"):
        raise ValueError(f"{path} is not a complete added source file")
    return "".join(line[1:] for line in section.splitlines(True)
                   if line.startswith("+") and not line.startswith("+++"))
