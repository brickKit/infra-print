"""One render worker process (renderer.py starts it): reads a request frame from stdin, renders it with Typst and
writes the answer frames, until stdin closes. It sets its own limits before Typst loads and gives Typst nothing to
read: an empty root, the bundled fonts only, a read-only package cache (no package is downloaded) and a dead proxy
(no network). Run as python -m infra_print.worker <fonts dir> <address space bytes>."""
import json
import os
import resource
import struct
import sys
import tempfile


def frame(out, b: bytes) -> None:
    out.write(struct.pack(">I", len(b)))
    out.write(b)


def main() -> None:
    fonts, memory = sys.argv[1], int(sys.argv[2])
    if memory:
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    inp = sys.stdin.buffer
    out = os.fdopen(os.dup(1), "wb")  # the frames; anything else printed goes to stderr
    os.dup2(2, 1)
    import typst  # after the limit: its allocations count

    root = tempfile.mkdtemp(prefix="print-root-")
    packages = tempfile.mkdtemp(prefix="print-packages-")
    os.chmod(packages, 0o555)
    while True:
        head = inp.read(4)
        if len(head) < 4:
            return
        req = json.loads(inp.read(struct.unpack(">I", head)[0]))
        try:
            pdf = typst.Compiler(req["content"].encode(), root=root, font_paths=[fonts], ignore_system_fonts=True,
                                 package_path=packages, package_cache_path=packages).compile(
                format="pdf", sys_inputs=req["inputs"], timestamp=req["timestamp"])
            frame(out, b'{"ok": true}')
            frame(out, pdf)
        except Exception as e:  # noqa: BLE001 - the template's or the data's fault, answered to the caller
            frame(out, json.dumps({"ok": False, "error": str(e)[:2000]}).encode())
            frame(out, b"")
        out.flush()


if __name__ == "__main__":
    main()
