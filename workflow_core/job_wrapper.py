"""Standalone job wrapper: run argv, keep its log, write the real exit code. No package imports."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main(argv):
    folder, separator, command = Path(argv[0]), argv[1], argv[2:]
    if separator != "--" or not command:
        return 2
    with (folder / "log.txt").open("ab") as log:
        try:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as error:
            log.write(f"start failed: {type(error).__name__}\n".encode("utf-8"))
            code = 127
        else:
            (folder / "child.json").write_text(json.dumps({"pid": child.pid, "wrapper_pid": os.getpid()}), encoding="utf-8")
            code = child.wait()
    staging = folder / "exit.json.tmp"
    staging.write_text(json.dumps({"exit_code": code}), encoding="utf-8")
    staging.replace(folder / "exit.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
