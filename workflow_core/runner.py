"""Bounded process execution for harness checks, probes and trials: argv only, never a shell."""
import os
import subprocess
import time

OUTPUT_LIMIT = 4000000


def run(argv, cwd, timeout=600, env=None, stdin=None):
    """Run argv and return the exit code observed from the process itself."""
    if not argv or not all(isinstance(part, str) and part for part in argv):
        raise ValueError("A nonempty argv list of strings is required")
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("Timeout must be an integer between 1 and 86400 seconds")
    started = time.monotonic()
    try:
        completed = subprocess.run(argv, cwd=cwd, env=env, input=stdin, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=timeout, check=False)
        exit_code, output, timed_out = completed.returncode, completed.stdout, False
    except subprocess.TimeoutExpired as error:
        exit_code, output, timed_out = None, error.stdout or b"", True
    except FileNotFoundError:
        exit_code, output, timed_out = 127, ("executable not found: " + argv[0] + "\n").encode("utf-8"), False
    output = output[-OUTPUT_LIMIT:]
    # Native tools may print in a legacy code page; store valid UTF-8 for capture/read-output.
    text = output.decode("utf-8", errors="replace")
    return {"argv": list(argv), "exit_code": exit_code, "timed_out": timed_out,
            "seconds": round(time.monotonic() - started, 3), "output": text.encode("utf-8")}


def sandbox_env(home):
    """Environment whose user/profile/temp directories point into an isolated folder."""
    env = {key: value for key, value in os.environ.items()
           if key.upper() in ("PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC", "WINDIR", "LANG", "TERM")}
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                "XDG_CACHE_HOME", "TEMP", "TMP", "TMPDIR"):
        env[key] = str(home)
    env["AGENT_WORKFLOW_SANDBOX"] = "1"
    return env
