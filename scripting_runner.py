"""The process that runs one SPADRA script (P0-02). It is started by ``scripting.py`` as ``python -I -S scripting_runner.py`` and talks to its parent
only through standard input (one JSON request) and standard output (one JSON reply). It imports nothing from SPADRA.

What it does, in this order, and *fails closed* at each step the request asks for:
  1. read the request, load the few standard modules a script may import, then
  2. set resource limits (CPU seconds, address space, file size, open files, core dumps),
  3. when ``sandbox`` is requested: no_new_privs, a Landlock ruleset that grants no file access and no TCP bind/connect, and a seccomp filter that
     refuses sockets, new processes, exec, ptrace and a few other calls. If any of these cannot be applied the run is refused (exit 78),
  4. compile the source with a short list of builtins and imports, run ``run(model, params)`` and write the reply.

Steps 2-3 are the security boundary; the builtin and import list in step 4 is only a second layer and is never relied on alone.
Linux only: there is no Windows or macOS sandbox here (the parent then runs only scripts a person has approved, and says so).
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import struct
import sys

EXIT_REFUSED = 78
ALLOWED_IMPORTS = ("math", "cmath", "statistics", "fractions", "decimal", "itertools", "functools", "collections", "random", "operator",
                   "bisect", "heapq", "json", "re", "string", "typing", "dataclasses", "enum", "numbers", "copy")
SAFE_BUILTINS = ("abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "frozenset", "int", "isinstance", "len", "list", "map",
                 "max", "min", "pow", "range", "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip", "print", "repr", "format",
                 "chr", "ord", "hash", "iter", "next", "callable", "bytes", "complex", "hasattr", "issubclass", "object", "property", "staticmethod",
                 "classmethod", "super", "type", "ArithmeticError", "AssertionError", "AttributeError", "Exception", "IndexError", "KeyError", "LookupError",
                 "OverflowError", "RuntimeError", "StopIteration", "TypeError", "ValueError", "ZeroDivisionError", "True", "False", "None",
                 "NotImplemented", "Ellipsis", "BaseException")


def refuse(message: str) -> None:
    sys.stdout.write(json.dumps({"ok": False, "refused": True, "error": message}))
    sys.stdout.flush()
    os._exit(EXIT_REFUSED)


# --------------------------------------------------------------------------------------------------------------------- limits
def set_limits(limits: dict) -> list[str]:
    """Apply per-process resource limits where the operating system has them (POSIX). Windows has no ``resource`` module: there the list is empty, and the
    parent process alone enforces the wall-clock timeout and the output cap (``isolation()`` says so; nothing here pretends otherwise)."""
    try:
        import resource
    except ImportError:
        return []
    applied = []
    for name, key, value in (("RLIMIT_CPU", "cpu_seconds", None), ("RLIMIT_AS", "memory_bytes", None), ("RLIMIT_FSIZE", None, 0),
                             ("RLIMIT_NOFILE", None, 16), ("RLIMIT_CORE", None, 0)):
        v = limits.get(key) if key else value
        if v is None:
            continue
        resource.setrlimit(getattr(resource, name), (int(v), int(v)))
        applied.append(name)
    return applied


# --------------------------------------------------------------------------------------------------------------------- Linux sandbox
_SYSCALLS = {
    "x86_64": {"landlock": (444, 445, 446), "arch": 0xC000003E,
               "deny": {"socket": 41, "connect": 42, "accept": 43, "sendto": 44, "recvfrom": 45, "bind": 49, "listen": 50, "socketpair": 53, "clone": 56,
                        "fork": 57, "vfork": 58, "execve": 59, "ptrace": 101, "mount": 165, "unshare": 272, "perf_event_open": 298, "setns": 308,
                        "memfd_create": 319, "execveat": 322, "io_uring_setup": 425, "clone3": 435, "process_vm_readv": 310, "process_vm_writev": 311,
                        "open_by_handle_at": 304, "kexec_load": 246, "init_module": 175, "bpf": 321}},
    "aarch64": {"landlock": (444, 445, 446), "arch": 0xC00000B7,
                "deny": {"socket": 198, "connect": 203, "accept": 202, "sendto": 206, "recvfrom": 207, "bind": 200, "listen": 201, "socketpair": 199,
                         "clone": 220, "execve": 221, "ptrace": 117, "mount": 40, "unshare": 97, "perf_event_open": 241, "setns": 268,
                         "memfd_create": 279, "execveat": 281, "io_uring_setup": 425, "clone3": 435, "process_vm_readv": 270, "process_vm_writev": 271,
                         "open_by_handle_at": 265, "bpf": 280}},
}


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]   # 64-bit pointers must not be cut to int
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    table = _SYSCALLS.get(platform.machine())
    if table is None or sys.platform != "linux":
        return 0
    libc = _libc()
    n = libc.syscall(table["landlock"][0], None, 0, 1)             # LANDLOCK_CREATE_RULESET_VERSION
    return int(n) if n > 0 else 0


def apply_landlock() -> str:
    """Deny every file access this ABI knows and, from ABI 4, TCP bind and connect. No rule grants anything back."""
    table = _SYSCALLS.get(platform.machine())
    abi = landlock_abi()
    if table is None or abi < 1:
        raise OSError("Landlock is not available in this kernel")
    fs = (1 << 13) - 1                                             # ABI 1: execute .. make_sym
    if abi >= 2:
        fs |= 1 << 13                                              # refer
    if abi >= 3:
        fs |= 1 << 14                                              # truncate
    if abi >= 5:
        fs |= 1 << 15                                              # ioctl on devices
    net = 0b11 if abi >= 4 else 0
    attr = struct.pack("QQ", fs, net) if abi >= 4 else struct.pack("Q", fs)
    libc = _libc()
    buf = ctypes.create_string_buffer(attr, len(attr))
    fd = libc.syscall(table["landlock"][0], buf, len(attr), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")
    if libc.syscall(table["landlock"][2], fd, 0) != 0:
        raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")
    os.close(fd)
    return f"landlock-abi-{abi}" + ("+tcp" if net else "")


def apply_seccomp() -> str:
    table = _SYSCALLS.get(platform.machine())
    if table is None:
        raise OSError("no seccomp table for this architecture")
    prog = [(0x20, 0, 0, 4),                                       # ld arch
            (0x15, 1, 0, table["arch"]),                           # jeq arch -> next, else kill
            (0x06, 0, 0, 0x00000000),                              # SECCOMP_RET_KILL
            (0x20, 0, 0, 0)]                                       # ld syscall number
    for nr in sorted(table["deny"].values()):
        prog += [(0x15, 0, 1, nr), (0x06, 0, 0, 0x00050001)]       # jeq nr -> return ERRNO(EPERM)
    prog.append((0x06, 0, 0, 0x7FFF0000))                          # allow
    raw = b"".join(struct.pack("HBBI", c, jt, jf, k) for c, jt, jf, k in prog)
    filt = ctypes.create_string_buffer(raw, len(raw))
    fprog = struct.pack("HxxxxxxP", len(prog), ctypes.addressof(filt))
    fbuf = ctypes.create_string_buffer(fprog, len(fprog))
    libc = _libc()
    if libc.prctl(38, 1, 0, 0, 0) != 0:                            # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "no_new_privs failed")
    if libc.prctl(22, 2, ctypes.addressof(fbuf), 0, 0) != 0:        # PR_SET_SECCOMP, SECCOMP_MODE_FILTER
        raise OSError(ctypes.get_errno(), "seccomp filter failed")
    return "seccomp-bpf"


def apply_sandbox() -> list[str]:
    out = []
    libc = _libc()
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "no_new_privs failed")
    out.append("no_new_privs")
    out.append(apply_landlock())
    out.append(apply_seccomp())
    return out


# --------------------------------------------------------------------------------------------------------------------- the script's world
class _Model:
    """The typed, read-only query API a script sees. Every method returns a fresh copy: changing what it returns changes nothing, because there is
    no call that writes. Lengths are metres, angles radians, in the frame the snapshot states. A script reads only what its version declared
    (``granted``): anything else raises ``PermissionError``."""

    def __init__(self, snap: dict) -> None:
        self._snap = snap

    def _need(self, scope: str) -> None:
        if scope not in self._snap.get("granted", ()):
            raise PermissionError(f"this script version did not declare read access to '{scope}'")

    @property
    def revision(self) -> int:
        return int(self._snap["revision"])

    @property
    def unit(self) -> str:
        return str(self._snap["unit"])

    @property
    def frame(self):
        return json.loads(json.dumps(self._snap.get("frame", {})))

    @property
    def granted(self):
        return list(self._snap.get("granted", ()))

    def levels(self):
        self._need("levels")
        return json.loads(json.dumps(self._snap["levels"]))

    def level(self, level_id):
        self._need("levels")
        for lv in self._snap["levels"]:
            if lv["id"] == level_id:
                return json.loads(json.dumps(lv))
        raise KeyError(f"no level {level_id!r}")

    def elements(self, category=None, level=None):
        self._need("elements")
        rows = [e for e in self._snap["elements"] if (category is None or e["category"] == category) and (level is None or e.get("level") == level)]
        return json.loads(json.dumps(rows))

    def element(self, element_id):
        self._need("elements")
        for e in self._snap["elements"]:
            if e["id"] == element_id:
                return json.loads(json.dumps(e))
        raise KeyError(f"no element {element_id!r}")

    def families(self):
        self._need("families")
        return json.loads(json.dumps(self._snap["families"]))

    def family(self, family_id):
        self._need("families")
        for f in self._snap["families"]:
            if f["id"] == family_id:
                return json.loads(json.dumps(f))
        raise KeyError(f"no family {family_id!r}")


def _builtins(modules: dict) -> dict:
    import builtins
    safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS if hasattr(builtins, name)}

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if level or root not in modules:
            raise ImportError(f"import of {name!r} is not allowed in a SPADRA script")
        return modules[root]

    def guarded_getattr(obj, name, *default):
        if not isinstance(name, str) or name.startswith("_"):
            raise AttributeError("attributes that start with an underscore are not available")
        return getattr(obj, name, *default)

    safe["__import__"] = guarded_import
    safe["getattr"] = guarded_getattr
    safe["__build_class__"] = builtins.__build_class__
    safe["__name__"] = "spadra_script"
    return safe


def main() -> None:
    raw = sys.stdin.buffer.read()
    try:
        req = json.loads(raw.decode("utf-8"))
        source, snap, params = req["source"], req["snapshot"], req["params"]
    except Exception as exc:
        refuse(f"bad request: {exc}")
    import importlib
    modules = {name: importlib.import_module(name) for name in ALLOWED_IMPORTS}
    import random
    random.seed(int(req.get("seed", 0)))
    if req.get("probe"):
        import socket                                              # the probe needs these *before* the sandbox closes the file system
        globals()["socket"] = socket
    code = compile(source, "<script>", "exec")                     # compiled before the sandbox: compile needs no files
    log: list[str] = []
    real_out = sys.stdout
    report = {"limits": [], "sandbox": []}
    try:
        report["limits"] = set_limits(req.get("limits", {}))
        if req.get("sandbox"):
            report["sandbox"] = apply_sandbox()
    except Exception as exc:
        refuse(f"the sandbox the run needs could not be applied ({exc}); nothing was run")
    if req.get("probe"):                                           # the self-test: try the forbidden things and report which succeeded
        real_out.write(json.dumps({"ok": True, "probe": _probe(), **report}))
        real_out.flush()
        os._exit(0)

    class _Log:
        def write(self, s):
            if sum(map(len, log)) < 20000:
                log.append(str(s))
            return len(s)

        def flush(self):
            pass

    sys.stdout = _Log()
    sys.stderr = _Log()
    ns = {"__builtins__": _builtins(modules), "__name__": "spadra_script"}
    try:
        exec(code, ns)
        fn = ns.get("run")
        if not callable(fn):
            raise RuntimeError("the script must define run(model, params)")
        result = fn(_Model(snap), json.loads(json.dumps(params)))
        try:
            text = json.dumps({"ok": True, "result": result, "log": "".join(log)[:20000], **report}, allow_nan=False)
        except ValueError:
            raise ValueError("the result contains NaN or infinity, which are not numbers a model can use") from None
        except TypeError as exc:
            raise TypeError(f"the result must be plain numbers, text, booleans, lists and dicts ({exc})") from None
    except BaseException as exc:                                   # includes MemoryError and the CPU-limit signal's aftermath
        text = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000], "log": "".join(log)[:20000], **report})
    real_out.write(text)
    real_out.flush()
    os._exit(0)


def _probe() -> dict:
    """What a sandboxed process can still do. Each entry is True if the forbidden action *worked*."""
    got = {}

    def attempt(name, fn):
        try:
            fn()
            got[name] = True
        except BaseException:
            got[name] = False

    attempt("read_file", lambda: open("/etc/passwd", "rb").read(1))
    attempt("write_file", lambda: open("/tmp/spadra_probe_should_not_exist", "wb").write(b"x"))
    attempt("list_dir", lambda: os.listdir("/"))
    attempt("socket", lambda: socket.socket())
    attempt("fork", lambda: os.fork())
    attempt("exec", lambda: os.execv("/bin/true", ["true"]))
    attempt("tcp_connect", lambda: socket.create_connection(("127.0.0.1", 9), timeout=0.2))
    return got


if __name__ == "__main__":
    main()
