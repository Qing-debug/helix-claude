// Bounded retry of a reply notify (A-M6fix). The board's NimBLE host refuses a reply notify with rc 6
// (BLE_HS_ENOMEM) right after a burst of back-to-back HID reports, until its buffers drain (notes/A-M6live.md:27-41).
// Plain C++ with the platform calls passed in, so firmware/test/test_notify_retry.cpp runs it on the host.
#pragma once

#include <stdint.h>

namespace notify_retry {

// Retry window of the loop task's replies (ACCEPTED, DONE, REJECTED), from the first attempt.
// About 2 connection intervals at the 15 ms the board runs at (A-M6live serial: itvl_ms=15.00).
constexpr int64_t WINDOW_US = 30000;

struct Result {
  int rc;          // the last attempt's rc: 0 = queued by NimBLE
  uint32_t tries;  // attempts made, >= 1 (31 or 32 with WINDOW_US and vTaskDelay(1) at 1 kHz; see pause() below)
};

// send() -> int rc (0 = success). alive() -> bool, false once the peer has left. now() -> int64_t microseconds.
// pause() sleeps (the firmware: vTaskDelay(1), which blocks until the next 1 kHz FreeRTOS tick: 0..1 ms, so the first
// pause can be short and the later ones about 1 ms). Order after a failed attempt: stop if the
// window has passed (so window_us = 0 is exactly one attempt and never calls alive() or pause()), stop if the peer is
// gone, else pause and try again. The last pause starts inside the window, so the time spent is at most
// window_us + one pause + one attempt.
template <class Send, class Alive, class Now, class Pause>
Result send_with_retry(Send send, Alive alive, Now now, Pause pause, int64_t window_us) {
  const int64_t start = now();
  Result r{0, 0};
  for (;;) {
    r.rc = send();
    ++r.tries;
    if (r.rc == 0) return r;
    if (now() - start >= window_us) return r;
    if (!alive()) return r;
    pause();
  }
}

// The loop task's all-up HID report (runPlay after a refused key report, runReleaseAll; A-M6fix-R [review], A-FC): a
// refused all-up leaves a key down on the PC until the next report, so it is retried like a loop-task reply, with
// WINDOW_US. Returns the last attempt's rc (0 = queued). Adds at most window + one pause + one attempt (~31 ms) before
// the DONE that follows it.
template <class Send, class Alive, class Now, class Pause>
int send_all_up(Send send, Alive alive, Now now, Pause pause) {
  return send_with_retry(send, alive, now, pause, WINDOW_US).rc;
}

}  // namespace notify_retry
