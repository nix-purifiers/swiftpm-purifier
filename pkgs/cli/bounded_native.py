# SPDX-License-Identifier: MIT
"""Bound direct-child exit and inherited pipe EOF; kill only an owned group."""

import contextlib
import os
import selectors
import signal
import subprocess
import threading
import time
import types


class Termination(BaseException):
    """Unwind owned cleanup before retaining the original termination status."""

    def __init__(self, signum):
        self.signum = signum
        super().__init__(f"command interrupted by signal {signum}")


class _DefaultTermination:
    def __init__(self):
        self.depth = 0
        self.pending = None
        self.unwinding = False

    def __call__(self, signum, _frame):
        if self.unwinding:
            return
        if self.depth:
            self.pending = signum
        else:
            self.unwinding = True
            raise Termination(signum)

    @contextlib.contextmanager
    def registering(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1
            if not self.depth and self.pending is not None:
                signum, self.pending = self.pending, None
                self.unwinding = True
                raise Termination(signum)


@contextlib.contextmanager
def default_sigterm():
    """Unwind a CLI's default SIGTERM; preserve custom foreground relays."""
    previous = signal.getsignal(signal.SIGTERM)
    if (
        threading.current_thread() is not threading.main_thread()
        or previous != signal.SIG_DFL
    ):
        yield
        return
    handler = _DefaultTermination()
    signal.signal(signal.SIGTERM, handler)
    try:
        yield
    except Termination as error:
        signal.signal(signal.SIGTERM, previous)
        os.kill(os.getpid(), error.signum)
        raise SystemExit(128 + error.signum)
    finally:
        if signal.getsignal(signal.SIGTERM) is handler:
            signal.signal(signal.SIGTERM, previous)


def _registration():
    handler = signal.getsignal(signal.SIGTERM)
    return (
        handler.registering()
        if isinstance(handler, _DefaultTermination)
        else contextlib.nullcontext()
    )


def _cleanup_child(child):
    if child is None:
        return
    try:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)
    finally:
        for stream in [child.stdin, child.stdout, child.stderr]:
            if stream is not None:
                stream.close()


def run_command(
    args, *, input=None, capture_output=False, timeout=None, check=False, **options
):
    """Preserve subprocess.run streams and statuses while owning its group."""
    if input is not None:
        if "stdin" in options:
            raise ValueError("stdin and input arguments may not both be used")
        options["stdin"] = subprocess.PIPE
    if capture_output:
        if "stdout" in options or "stderr" in options:
            raise ValueError("capture_output conflicts with stdout or stderr")
        options["stdout"] = options["stderr"] = subprocess.PIPE
    if options.pop("start_new_session", True) is not True:
        raise ValueError("owned commands require a private process group")
    child = None
    try:
        with _registration():
            child = subprocess.Popen(args, start_new_session=True, **options)
        stdout, stderr = child.communicate(input, timeout=timeout)
        result = subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        _cleanup_child(child)


def run(
    args,
    input=None,
    env=None,
    timeout=90,
    output_limit=8388608,
    cwd=None,
    protocol_fd_flag=None,
):
    """Capture independent protocol bytes under the child and pipe deadline."""
    protocol_stream = None
    command = args
    inherited = ()
    child = None
    selector = None
    if protocol_fd_flag is not None:
        if not isinstance(protocol_fd_flag, str) or not protocol_fd_flag:
            raise ValueError("invalid protocol descriptor flag")
        read_fd, write_fd = os.pipe()
        protocol_stream = os.fdopen(read_fd, "rb", buffering=0)
        inherited = (write_fd,)
        command = [*args, protocol_fd_flag, str(write_fd)]
    output = [bytearray() for _ in range(3 if protocol_stream is not None else 2)]
    try:
        # A signal during Popen must wait until the created child is tracked.
        with _registration():
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
            if inherited:
                descriptor, inherited = inherited[0], ()
                os.close(descriptor)
        streams = [child.stdout, child.stderr]
        if protocol_stream is not None:
            streams.append(protocol_stream)
        selector = selectors.DefaultSelector()
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
    except (Exception, Termination) as error:
        error.stdout = bytes(output[0])
        error.stderr = bytes(output[1])
        error.protocol = bytes(output[2]) if protocol_stream is not None else b""
        error.command_args = command
        raise
    finally:
        try:
            if inherited:
                os.close(inherited[0])
        finally:
            try:
                _cleanup_child(child)
            finally:
                if selector is not None:
                    selector.close()
                if protocol_stream is not None:
                    protocol_stream.close()


def run_recorded(argv, *, record, logs, save, **options):
    """Persist raw streams and outcome before returning or rethrowing."""
    import hashlib

    failure = None
    try:
        result = run(argv, **options)
    except (Exception, Termination) as error:
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
