"""Automatic port selection (bind_server) and its CLI integration."""

import errno
import random
import socket
import subprocess
import sys

import pytest

from webmd import cli


class FakeServer:
    def __init__(self, addr):
        self.server_address = addr


def conflict():
    return OSError(errno.EADDRINUSE, "Address already in use")


def scripted(outcomes):
    """make_server that fails per `outcomes` (exception or None=success), recording each address tried."""
    tried = []
    outcomes = list(outcomes)

    def make(addr):
        tried.append(addr)
        outcome = outcomes.pop(0) if outcomes else None
        if outcome is not None:
            raise outcome
        return FakeServer(addr)

    return make, tried


def quiet(*args, **kwargs):
    pass


def test_free_default_port_binds_first_try():
    make, tried = scripted([None])
    server = cli.bind_server(make, "127.0.0.1", 8000, strict=False, log=quiet)
    assert tried == [("127.0.0.1", 8000)] and server.server_address == ("127.0.0.1", 8000)


def test_occupied_default_falls_back_to_dynamic_range():
    make, tried = scripted([conflict(), None])
    server = cli.bind_server(make, "127.0.0.1", 8000, strict=False, log=quiet)
    assert len(tried) == 2
    port = server.server_address[1]
    assert 49152 <= port <= 65535
    assert tried[1] == ("127.0.0.1", port)  # same interface as configured


def test_fallback_keeps_trying_until_one_binds():
    make, tried = scripted([conflict()] * 4 + [None])
    cli.bind_server(make, "0.0.0.0", 8000, strict=False, log=quiet)
    assert len(tried) == 5 and all(host == "0.0.0.0" for host, _ in tried)


def test_exhausting_ten_fallbacks_exits_with_message():
    make, tried = scripted([conflict()] * 11)
    with pytest.raises(SystemExit) as exc:
        cli.bind_server(make, "127.0.0.1", 8000, strict=False, log=quiet)
    assert len(tried) == 11  # configured port + exactly 10 fallbacks
    assert "10 random fallback ports" in str(exc.value) and "-p" in str(exc.value)


def test_fallback_candidates_are_unique_and_in_range():
    for seed in range(50):
        make, tried = scripted([conflict()] * 11)
        with pytest.raises(SystemExit):
            cli.bind_server(make, "127.0.0.1", 8000, strict=False, rng=random.Random(seed), log=quiet)
        fallbacks = [port for _, port in tried[1:]]
        assert len(set(fallbacks)) == 10
        assert all(49152 <= p <= 65535 for p in fallbacks)


def test_fallback_never_retries_the_configured_port():
    make, tried = scripted([conflict()] * 11)
    with pytest.raises(SystemExit):
        cli.bind_server(make, "127.0.0.1", 50000, strict=False, rng=random.Random(1), log=quiet)
    assert 50000 not in [port for _, port in tried[1:]]


def test_fallback_range_endpoints_are_reachable():
    assert cli.FALLBACK_PORTS[0] == 49152 and cli.FALLBACK_PORTS[-1] == 65535


def test_explicit_port_conflict_is_an_error_not_a_fallback():
    make, tried = scripted([conflict()])
    with pytest.raises(SystemExit) as exc:
        cli.bind_server(make, "127.0.0.1", 9000, strict=True, log=quiet)
    assert tried == [("127.0.0.1", 9000)]
    assert "9000" in str(exc.value) and "in use" in str(exc.value)


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(errno.EACCES, "Permission denied"),
        OSError(errno.EADDRNOTAVAIL, "Can't assign requested address"),
        OSError(errno.EINVAL, "Invalid argument"),
        socket.gaierror(socket.EAI_NONAME, "Name or service not known"),
    ],
)
def test_non_conflict_errors_fail_immediately(error):
    make, tried = scripted([error])
    with pytest.raises(SystemExit) as exc:
        cli.bind_server(make, "127.0.0.1", 8000, strict=False, log=quiet)
    assert len(tried) == 1  # no fallback attempts
    assert "cannot listen on 127.0.0.1:8000" in str(exc.value)


def test_non_conflict_error_during_fallback_stops_retrying():
    make, tried = scripted([conflict(), PermissionError(errno.EACCES, "Permission denied")])
    with pytest.raises(SystemExit) as exc:
        cli.bind_server(make, "127.0.0.1", 8000, strict=False, log=quiet)
    assert len(tried) == 2 and "Permission denied" in str(exc.value)


def test_windows_conflict_codes(monkeypatch):
    in_use = OSError(None, "in use")
    in_use.winerror = 10048
    assert cli.is_port_conflict(in_use)
    denied = OSError(errno.EACCES, "denied")
    denied.winerror = 10013
    monkeypatch.setattr(sys, "platform", "win32")
    assert cli.is_port_conflict(denied)
    monkeypatch.setattr(sys, "platform", "linux")
    assert not cli.is_port_conflict(denied)


@pytest.mark.parametrize(
    "scheme,host,port,url",
    [
        ("http", "127.0.0.1", 8000, "http://127.0.0.1:8000/"),
        ("https", "0.0.0.0", 51234, "https://0.0.0.0:51234/"),
        ("http", "::1", 8000, "http://[::1]:8000/"),
    ],
)
def test_display_url(scheme, host, port, url):
    assert cli.display_url(scheme, host, port) == url


# --- Real sockets -------------------------------------------------------------


def occupy(host="127.0.0.1", port=0):
    s = socket.socket()
    s.bind((host, port))
    s.listen()
    return s


def test_real_server_conflict_is_detected():
    holder = occupy()
    try:
        with pytest.raises(OSError) as exc:
            cli.Server(("127.0.0.1", holder.getsockname()[1]), cli.Handler)
        assert cli.is_port_conflict(exc.value)
    finally:
        holder.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows reports this case as WSAEACCES; covered above")
def test_specific_bind_cannot_shadow_wildcard_listener():
    # macOS/BSD would allow this with SO_REUSEADDR, silently stealing traffic.
    holder = occupy("0.0.0.0")
    try:
        with pytest.raises(OSError) as exc:
            cli.Server(("127.0.0.1", holder.getsockname()[1]), cli.Handler)
        assert cli.is_port_conflict(exc.value)
    finally:
        holder.close()


def test_real_fallback_returns_bound_listening_socket():
    holder = occupy()
    port = holder.getsockname()[1]
    try:

        def make(addr):
            return cli.Server(addr, cli.Handler)

        server = cli.bind_server(make, "127.0.0.1", port, strict=False, log=quiet)
        try:
            new_port = server.server_address[1]
            assert new_port != port and 49152 <= new_port <= 65535
            # Still bound and listening: no release-then-rebind window.
            socket.create_connection(("127.0.0.1", new_port), timeout=2).close()
        finally:
            server.server_close()
    finally:
        holder.close()


def test_rebind_after_restart_with_connections_in_time_wait():
    server = cli.Server(("127.0.0.1", 0), cli.Handler)
    port = server.server_address[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.socket.accept()
    conn.close()  # server closes first: its side enters TIME_WAIT
    client.close()
    server.server_close()
    again = cli.Server(("127.0.0.1", port), cli.Handler)
    again.server_close()


# --- CLI end to end -----------------------------------------------------------


def test_cli_default_port_occupied_falls_back_and_reports_url(serve):
    holder = occupy()
    taken = holder.getsockname()[1]
    try:
        # No -p, so this is "the default port" (pointed at an occupied port for the test).
        s = serve("--no-auth", env={"WEBMD_DEFAULT_PORT": str(taken)}, port_flag=False)
        assert s.port != taken and 49152 <= s.port <= 65535
        assert f"port {taken} is in use" in s.output
        assert f"-> http://127.0.0.1:{s.port}/" in s.output
        assert s.get("/README.md")[0] == 200
    finally:
        holder.close()


def test_cli_default_port_used_when_free(serve):
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    s = serve(env={"WEBMD_DEFAULT_PORT": str(port)}, port_flag=False)
    assert s.port == port and "in use" not in s.output


def test_cli_explicit_port_occupied_exits(site, tmp_path):
    holder = occupy()
    port = holder.getsockname()[1]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "webmd", str(site), "-p", str(port)],
            capture_output=True,
            text=True,
            timeout=20,
            env={"XDG_CONFIG_HOME": str(tmp_path / "cfg"), **_base_env()},
        )
    finally:
        holder.close()
    assert result.returncode != 0
    assert f"port {port} on 127.0.0.1 is already in use" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("port", ["70000", "-1", "abc"])
def test_cli_rejects_invalid_port(site, port):
    result = subprocess.run(
        [sys.executable, "-m", "webmd", str(site), "-p", port], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 2 and "Traceback" not in result.stderr


def test_cli_unresolvable_bind_address_exits_cleanly(site, tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "webmd", str(site), "-b", "no-such-host.invalid", "--no-auth", "--no-tls"],
        capture_output=True,
        text=True,
        timeout=30,
        env={"XDG_CONFIG_HOME": str(tmp_path / "cfg"), **_base_env()},
    )
    assert result.returncode != 0
    assert "cannot listen on no-such-host.invalid" in result.stderr
    assert "Traceback" not in result.stderr


def _base_env():
    import os

    return {k: v for k, v in os.environ.items() if not k.startswith("WEBMD_") and k != "XDG_CONFIG_HOME"}
