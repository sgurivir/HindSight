import os


def dump_open_files_psutil():
    """Dump all open files and connections for the current process."""
    import psutil
    p = psutil.Process(os.getpid())

    # regular files
    for f in p.open_files():
        print(f"file: {f.path} fd={f.fd}")

    # sockets (optional)
    for c in p.connections(kind="all"):
        print(f"conn: fd={c.fd} {c.type} {c.laddr} -> {c.raddr} {c.status}")