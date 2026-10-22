// Host test for firmware/Helix_BLE/notify_retry.h (A-M6fix): the reply-notify retry stops on success, on a peer that
// left, or once its window has passed, and its time is bounded by window + one pause + one attempt.
// Build and run (Git Bash, repo root in $REPO):
//   g++ -std=c++17 -Wall -Werror -I$REPO/firmware/Helix_BLE $REPO/firmware/test/test_notify_retry.cpp
//       -o $REPO/firmware/build/test_notify_retry.exe && $REPO/firmware/build/test_notify_retry.exe
// Prints ALL PASS and exits 0 on success; prints each failure and exits 1 otherwise.

#include "notify_retry.h"

#include <cstdio>
#include <functional>

static int g_checks = 0, g_failures = 0;
#define CHECK(cond, ...)                                                    \
    do {                                                                    \
        ++g_checks;                                                         \
        if (!(cond)) {                                                      \
            ++g_failures;                                                   \
            std::printf("FAIL %s:%d: ", __FILE__, __LINE__);                \
            std::printf(__VA_ARGS__);                                       \
            std::printf("\n");                                              \
        }                                                                   \
    } while (0)

// A fake board: a clock in us, a send() whose outcome is chosen per test, counted alive() and pause() calls.
struct Fake {
    int64_t t = 0;
    int64_t attempt_us = 0;          // time one send() takes
    int64_t pause_us = 1000;         // time one pause() takes (vTaskDelay(1) at 1 kHz: up to 1 ms)
    int sends = 0, alives = 0, pauses = 0;
    std::function<int(const Fake&)> outcome = [](const Fake&) { return 0; };   // rc of the next send
    std::function<bool(const Fake&)> peer = [](const Fake&) { return true; };

    notify_retry::Result run(int64_t window_us) {
        return notify_retry::send_with_retry(
            [this] { const int rc = outcome(*this); ++sends; t += attempt_us; return rc; },
            [this] { ++alives; return peer(*this); },
            [this] { return t; },
            [this] { ++pauses; t += pause_us; },
            window_us);
    }
};

static constexpr int ENOMEM_RC = 6;   // BLE_HS_ENOMEM, the rc seen live (notes/A-M6live.md:32)

static void test_first_try_succeeds() {
    Fake f;
    const auto r = f.run(notify_retry::WINDOW_US);
    CHECK(r.rc == 0 && r.tries == 1, "rc=%d tries=%u", r.rc, r.tries);
    CHECK(f.pauses == 0 && f.alives == 0 && f.t == 0, "pauses=%d alives=%d t=%lld", f.pauses, f.alives, (long long)f.t);
}

// onWrite (host task) passes window 0: one attempt, never alive() (a spinlock) nor pause() (a block), A-F5-D3.
static void test_window_zero_is_one_attempt_without_waiting() {
    Fake f;
    f.outcome = [](const Fake&) { return ENOMEM_RC; };
    const auto r = f.run(0);
    CHECK(r.rc == ENOMEM_RC && r.tries == 1, "rc=%d tries=%u", r.rc, r.tries);
    CHECK(f.sends == 1 && f.pauses == 0 && f.alives == 0 && f.t == 0, "sends=%d pauses=%d alives=%d t=%lld", f.sends,
          f.pauses, f.alives, (long long)f.t);
}

// The m6 case: refused until the host's buffers drain (here 2.5 ms after the first attempt), then queued.
static void test_succeeds_once_buffers_drain() {
    Fake f;
    f.outcome = [](const Fake& s) { return s.t < 2500 ? ENOMEM_RC : 0; };
    const auto r = f.run(notify_retry::WINDOW_US);
    CHECK(r.rc == 0 && r.tries == 4, "rc=%d tries=%u", r.rc, r.tries);   // attempts at t = 0, 1000, 2000, 3000
    CHECK(f.pauses == 3 && f.t == 3000, "pauses=%d t=%lld", f.pauses, (long long)f.t);
}

// Never accepted: gives up with the last rc after the window, so the reply is dropped as before.
static void test_gives_up_after_the_window() {
    Fake f;
    f.outcome = [](const Fake&) { return ENOMEM_RC; };
    const auto r = f.run(notify_retry::WINDOW_US);
    CHECK(r.rc == ENOMEM_RC, "rc=%d", r.rc);
    CHECK(r.tries == 31 && f.pauses == 30, "tries=%u pauses=%d", r.tries, f.pauses);   // t = 0, 1000, ..., 30000
    CHECK(f.t == notify_retry::WINDOW_US, "t=%lld", (long long)f.t);
}

// The bound: whatever the attempt cost and pause length, time spent <= window + one pause + one attempt.
static void test_time_spent_is_bounded() {
    const int64_t attempts[] = {0, 1, 50, 400, 2000};
    const int64_t pauses[] = {1, 999, 1000, 1500, 7000};
    for (int64_t a : attempts) {
        for (int64_t p : pauses) {
            Fake f;
            f.attempt_us = a;
            f.pause_us = p;
            f.outcome = [](const Fake&) { return ENOMEM_RC; };
            const auto r = f.run(notify_retry::WINDOW_US);
            const int64_t bound = notify_retry::WINDOW_US + p + a;
            CHECK(f.t <= bound, "attempt=%lld pause=%lld: spent %lld > bound %lld", (long long)a, (long long)p,
                  (long long)f.t, (long long)bound);
            CHECK(f.t >= notify_retry::WINDOW_US, "attempt=%lld pause=%lld: gave up early at %lld", (long long)a,
                  (long long)p, (long long)f.t);
            CHECK((int)r.tries == f.sends && f.pauses == f.sends - 1, "tries=%u sends=%d pauses=%d", r.tries, f.sends,
                  f.pauses);
        }
    }
}

// The peer left (linkConnected false): stop at once instead of waiting out the window.
static void test_stops_when_the_peer_leaves() {
    Fake f;
    f.outcome = [](const Fake&) { return 7; };   // BLE_HS_ENOTCONN
    f.peer = [](const Fake& s) { return s.sends < 2; };
    const auto r = f.run(notify_retry::WINDOW_US);
    CHECK(r.rc == 7 && r.tries == 2, "rc=%d tries=%u", r.rc, r.tries);
    CHECK(f.pauses == 1 && f.alives == 2, "pauses=%d alives=%d", f.pauses, f.alives);
}

// A success on the last attempt inside the window still counts.
static void test_success_on_the_last_attempt() {
    Fake f;
    f.outcome = [](const Fake& s) { return s.t < notify_retry::WINDOW_US ? ENOMEM_RC : 0; };
    const auto r = f.run(notify_retry::WINDOW_US);
    CHECK(r.rc == 0 && r.tries == 31, "rc=%d tries=%u", r.rc, r.tries);
}

int main() {
    test_first_try_succeeds();
    test_window_zero_is_one_attempt_without_waiting();
    test_succeeds_once_buffers_drain();
    test_gives_up_after_the_window();
    test_time_spent_is_bounded();
    test_stops_when_the_peer_leaves();
    test_success_on_the_last_attempt();
    std::printf("%d checks\n", g_checks);
    if (g_failures) { std::printf("%d FAILED\n", g_failures); return 1; }
    std::printf("ALL PASS\n");
    return 0;
}
