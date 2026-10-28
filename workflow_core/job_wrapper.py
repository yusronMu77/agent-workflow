"""Standalone job wrapper: run argv, keep its log, write the real exit code. No package imports."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys


def kill_children_with_wrapper():
    """Windows: join a job object that kills every member when the wrapper dies.

    Children inherit the job, so taskkill on the wrapper also ends a child created a moment
    before the process tree was enumerated. Best effort; POSIX uses the process group instead."""
    if os.name != "nt":
        return None
    try:
        class Basic(ctypes.Structure):
            _fields_ = [("user_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", ctypes.c_uint32),
                        ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t), ("processes", ctypes.c_uint32),
                        ("affinity", ctypes.c_size_t), ("priority", ctypes.c_uint32), ("scheduling", ctypes.c_uint32)]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
        kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        job = kernel32.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if job and kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))                 and kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            return job  # Keep the handle open for the wrapper's lifetime.
    except (OSError, AttributeError):
        pass
    return None


def main(argv):
    folder, separator, command = Path(argv[0]), argv[1], argv[2:]
    if separator != "--" or not command:
        return 2
    job = kill_children_with_wrapper()  # noqa: F841 - handle must outlive the child
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
