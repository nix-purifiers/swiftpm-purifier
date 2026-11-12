# SPDX-License-Identifier: MIT
"""Bound direct-child exit and inherited pipe EOF; kill only an owned group."""

import os, selectors, signal, subprocess, time, types


def run(args, input=None, env=None, timeout=90, output_limit=8388608, cwd=None):
    child = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    output = [bytearray(), bytearray()]
    try:
        if input is not None:
            assert len(input) <= 4096
            child.stdin.write(input)
            child.stdin.close()
        for i, stream in enumerate((child.stdout, child.stderr)):
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
            args=args,
            returncode=child.returncode,
            stdout=bytes(output[0]),
            stderr=bytes(output[1]),
        )
    except Exception as error:
        error.stdout = bytes(output[0])
        error.stderr = bytes(output[1])
        raise
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)
        selector.close()
        child.stdout.close()
        child.stderr.close()


def run_recorded(argv, *, record, logs, save, **options):
    """Persist raw streams and outcome before returning or rethrowing."""
    import hashlib

    failure = None
    try:
        result = run(argv, **options)
    except Exception as error:
        failure = error
        result = types.SimpleNamespace(
            returncode=None,
            stdout=getattr(error, "stdout", b""),
            stderr=getattr(error, "stderr", b""),
        )
    record.update(argv=list(argv), exit=result.returncode)
    if failure is not None:
        record["boundedFailure"] = {
            "type": type(failure).__name__,
            "message": str(failure),
        }
    for stream in ("stdout", "stderr"):
        raw = getattr(result, stream)
        (logs / (record["label"] + "." + stream)).write_bytes(raw)
        record[stream + "Sha256"] = hashlib.sha256(raw).hexdigest()
    save()
    if failure is not None:
        raise failure
    return result
