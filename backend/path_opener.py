import subprocess
import sys
from pathlib import Path
from typing import List


class PathOpenError(Exception):
    pass


def _is_under_any_base(target: Path, bases: List[Path]) -> bool:
    for base in bases:
        try:
            target.relative_to(base)
            return True
        except ValueError:
            continue
    return False


def open_in_file_manager(target_path: str, allowed_base_dirs: List[str]) -> None:
    p = Path(target_path).expanduser().resolve()
    if not p.exists():
        raise PathOpenError("路径不存在")

    bases = [Path(d).expanduser().resolve() for d in allowed_base_dirs]
    if not _is_under_any_base(p, bases):
        raise PathOpenError("路径不在允许范围内")

    if sys.platform == "darwin":
        if p.is_file():
            cmd = ["open", "-R", str(p)]
        else:
            cmd = ["open", str(p)]
    elif sys.platform.startswith("win"):
        if p.is_file():
            cmd = ["explorer", "/select,", str(p)]
        else:
            cmd = ["explorer", str(p)]
    else:
        cmd = ["xdg-open", str(p if p.is_dir() else p.parent)]

    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
