# SPDX-License-Identifier: MIT
"""Bound direct-child exit and inherited pipe EOF; kill only an owned group."""

import os, selectors, signal, subprocess, time, types


def run(
    args,
    input=None,
    env=None,
    timeout=90,
    output_limit=8388608,
    cwd=None,
    protocol_fd_flag=None,
):
    """Optionally capture a native protocol on its own inherited descriptor.

    The protocol shares the output bound and the child/pipe deadline. Its
    descriptor is appended with the native flag; stdout remains independent.
    """
    protocol_stream = None
    command = args
    inherited = ()
    if protocol_fd_flag is not None:
        if not isinstance(protocol_fd_flag, str) or not protocol_fd_flag:
            raise ValueError("invalid protocol descriptor flag")
        read_fd, write_fd = os.pipe()
        protocol_stream = os.fdopen(read_fd, "rb", buffering=0)
        inherited = (write_fd,)
        command = [*args, protocol_fd_flag, str(write_fd)]
    try:
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            start_new_session=True,
            pass_fds=inherited,
        )
    except BaseException:
        if protocol_stream is not None:
            protocol_stream.close()
        raise
    finally:
        if inherited:
            os.close(inherited[0])
    streams = [child.stdout, child.stderr]
    if protocol_stream is not None:
        streams.append(protocol_stream)
    selector = selectors.DefaultSelector()
    output = [bytearray() for _ in streams]
    try:
        if input is not None:
            assert len(input) <= 4096
            child.stdin.write(input)
            child.stdin.close()
        for i, stream in enumerate(streams):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, i)
        deadline = time.monotonic() + timeout
        while selector.get_map() or child.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("owned command/pipe deadline")
            for key, _ in selector.select(min(remaining, 0.05)):
                raw = os.read(key.fileobj.fileno(), 65536)
                if raw:
                    output[key.data].extend(raw)
                    if sum(map(len, output)) > output_limit:
                        raise ValueError("owned command output bound")
                else:
                    selector.unregister(key.fileobj)
        return types.SimpleNamespace(
            args=command,
            returncode=child.returncode,
            stdout=bytes(output[0]),
            stderr=bytes(output[1]),
            protocol=bytes(output[2]) if protocol_stream is not None else b"",
        )
    except Exception as error:
        error.stdout = bytes(output[0])
        error.stderr = bytes(output[1])
        error.protocol = bytes(output[2]) if protocol_stream is not None else b""
        error.command_args = command
        raise
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)
        selector.close()
        for stream in streams:
            stream.close()


def run_recorded(argv, *, record, logs, save, **options):
    """Persist raw streams and outcome before returning or rethrowing."""
    import hashlib

    failure = None
    try:
        result = run(argv, **options)
    except Exception as error:
        failure = error
        result = types.SimpleNamespace(
            args=getattr(error, "command_args", argv),
            returncode=None,
            stdout=getattr(error, "stdout", b""),
            stderr=getattr(error, "stderr", b""),
            protocol=getattr(error, "protocol", b""),
        )
    record.update(argv=list(result.args), exit=result.returncode)
    if failure is not None:
        record["boundedFailure"] = {
            "type": type(failure).__name__,
            "message": str(failure),
        }
    streams = (
        ("stdout", "stderr", "protocol")
        if options.get("protocol_fd_flag") is not None
        else ("stdout", "stderr")
    )
    for stream in streams:
        raw = getattr(result, stream)
        (logs / (record["label"] + "." + stream)).write_bytes(raw)
        record[stream + "Sha256"] = hashlib.sha256(raw).hexdigest()
    save()
    if failure is not None:
        raise failure
    return result
