"""Failed-login throttling (AuthThrottle) and its HTTP behaviour."""

import base64
import threading
import time

import pytest

from webmd.cli import AuthThrottle


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


def make(clock, **kwargs):
    return AuthThrottle(clock=clock, **kwargs)


def test_first_failures_are_free(clock):
    t = make(clock, free_failures=5)
    for _ in range(5):
        assert t.retry_after("1.2.3.4") == 0
        t.failure("1.2.3.4")
    assert t.retry_after("1.2.3.4") == 0


def test_backoff_doubles_and_is_bounded(clock):
    t = make(clock, free_failures=0, base_delay=1, max_delay=8)
    waits = []
    for _ in range(7):
        t.failure("ip")
        waits.append(t.retry_after("ip"))
        clock.now += waits[-1]  # wait it out, then fail again
    assert waits == [1, 2, 4, 8, 8, 8, 8]


def test_wait_expires_without_intervention(clock):
    t = make(clock, free_failures=0, base_delay=4)
    t.failure("ip")
    assert t.retry_after("ip") == 4
    clock.now += 4
    assert t.retry_after("ip") == 0


def test_success_resets_the_client(clock):
    t = make(clock, free_failures=1, base_delay=1)
    for _ in range(4):
        t.failure("ip")
        clock.now += 60
    t.success("ip")
    t.failure("ip")
    assert t.retry_after("ip") == 0  # back to the free allowance


def test_clients_are_independent(clock):
    t = make(clock, free_failures=0, base_delay=10)
    t.failure("attacker")
    assert t.retry_after("attacker") == 10
    assert t.retry_after("legit") == 0


def test_global_bucket_caps_distributed_guessing(clock):
    t = make(clock, free_failures=1000, global_burst=10, global_rate=2.0)
    for i in range(10):
        t.failure(f"10.0.0.{i}")  # every guess from a different address
    assert t.retry_after("10.0.0.99") == pytest.approx(0.5)  # bucket empty: one token per 0.5s
    clock.now += 0.5
    assert t.retry_after("10.0.0.99") == 0


def test_stale_entries_are_forgotten(clock):
    t = make(clock, free_failures=0, forget_after=900)
    for i in range(20):
        t.failure(f"ip{i}")
    assert t.tracked_clients() == 20
    clock.now += 901
    t.retry_after("someone")  # any access prunes
    assert t.tracked_clients() == 0


def test_table_size_is_capped(clock):
    t = make(clock, max_clients=100, global_burst=10_000)
    for i in range(1000):
        t.failure(f"ip{i}")
        clock.now += 0.001
    assert t.tracked_clients() == 100
    assert t.retry_after("ip999") >= 0  # most recent kept, oldest evicted


def test_concurrent_failures_are_all_counted(clock):
    t = make(clock, free_failures=10_000, global_burst=100_000)
    threads = [threading.Thread(target=lambda: [t.failure("ip") for _ in range(500)]) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert t._clients["ip"][0] == 4000


# --- Over HTTP ----------------------------------------------------------------


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_http_backoff_then_recovery(serve):
    s = serve("-b", "0.0.0.0")
    good = s.credentials()
    statuses = [s.get("/", headers=basic("webmd", f"wrong{i}"))[0] for i in range(7)]
    # 5 free misses; the 6th miss starts a 1s wait, so the 7th attempt isn't even checked.
    assert statuses == [401] * 6 + [429]

    # During the backoff even the right password is refused unchecked, with a bounded wait.
    status, headers, _ = s.get("/", headers=basic(*good))
    assert status == 429 and 1 <= int(headers["Retry-After"]) <= 60

    time.sleep(int(headers["Retry-After"]))
    assert s.get("/", headers=basic(*good))[0] == 200  # no permanent lockout
    assert s.get("/", headers=basic("webmd", "wrong-again"))[0] == 401  # success reset the counter


def test_http_credentialless_prompt_is_not_a_failure(serve):
    s = serve("-b", "0.0.0.0")
    for _ in range(20):
        assert s.get("/")[0] == 401  # browsers send this first; never throttled
    assert s.get("/", auth=s.credentials())[0] == 200


def test_http_429_reveals_nothing(serve):
    s = serve("-b", "0.0.0.0")
    for i in range(6):
        s.get("/", headers=basic("webmd", f"bad{i}"))
    status, headers, body = s.get("/", headers=basic("webmd", "bad"))
    assert status == 429 and body == ""
    assert "WWW-Authenticate" not in headers
    assert "webmd" not in "".join(f"{k}{v}" for k, v in headers.items() if k.lower() not in ("server",))


def test_http_ignores_spoofed_forwarded_headers(serve):
    s = serve("-b", "0.0.0.0")
    for i in range(6):
        s.get("/", headers={**basic("webmd", f"bad{i}"), "X-Forwarded-For": f"203.0.113.{i}"})
    status, _, _ = s.get("/", headers={**basic("webmd", "bad"), "X-Forwarded-For": "198.51.100.7"})
    assert status == 429  # still the same TCP peer, whatever the header claims


def test_passwords_are_never_logged(serve, tmp_path):
    s = serve("-b", "0.0.0.0", env={"WEBMD_USER": "u", "WEBMD_PASSWORD": "s3cret-pw"})
    s.get("/", headers=basic("u", "guess-pw"))
    s.get("/", headers=basic("u", "s3cret-pw"))
    log = next(tmp_path.glob("server-*.log")).read_text()
    assert "guess-pw" not in log and "s3cret-pw" not in log
    assert base64.b64encode(b"u:guess-pw").decode() not in log
