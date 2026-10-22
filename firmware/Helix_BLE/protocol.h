// Helix wire protocol v1: command parser and notification encoders (Plan A contract C1, key mapping C2).
// Header-only and free of Arduino includes so the host test (firmware/test/test_protocol.cpp) builds it with g++.
// All multi-byte fields are little-endian. The golden vectors are firmware/test/vectors.json (shared with P1).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace proto {

constexpr uint8_t VER = 1;
enum Op : uint8_t { PLAY = 1, RELEASE_ALL = 2, PING = 3 };
enum Reject : uint8_t { BAD_VERSION = 1, BAD_LENGTH = 2, BAD_SCHEDULE = 3, QUEUE_FULL = 4, UNKNOWN_OP = 5,
                        HID_NOT_SUBSCRIBED = 6 };   // 4 and 6 come from onWrite / the executor, never from parse()
enum PongFlag : uint8_t { BONDED = 1, ENCRYPTED = 2, AUTHENTICATED = 4, SUBSCRIBED = 8 };
enum DoneStatus : uint8_t { OK = 0, NOTIFY_FAILED = 1, DISCONNECTED = 2, ABORTED = 3 };
constexpr size_t MAX_STEPS = 16, MAX_CMD = 5 + 3 * MAX_STEPS;
struct Step { uint32_t t_us; uint8_t mask; };            // absolute offset from start, after decoding deltas
struct Command { uint8_t op; uint16_t seq; uint8_t n; Step steps[MAX_STEPS]; };

namespace detail {
constexpr uint8_t KIND_ACCEPTED = 0x81, KIND_DONE = 0x82, KIND_REJECTED = 0x83, KIND_PONG = 0x84;
constexpr uint32_t MAX_SUM_DELTA = 100000;   // 100 us units: 10 s per schedule
constexpr uint32_t DELTA_UNIT_US = 100;

inline uint16_t get_u16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }
inline uint8_t* put_u16(uint8_t* p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); return p + 2; }
inline uint8_t* put_u32(uint8_t* p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
    return p + 4;
}
inline uint8_t* put_header(uint8_t* p, uint8_t kind, uint16_t seq) {
    p[0] = VER; p[1] = kind;
    return put_u16(p + 2, seq);
}
inline bool exactly_one_bit(uint8_t x) { return x != 0 && (x & (x - 1)) == 0; }
}  // namespace detail

// Returns 0 on success, else a Reject code; seq is filled whenever len >= 4.
// Check order (decides the reason when a frame breaks several rules):
//   len 0 -> BAD_LENGTH; ver != 1 -> BAD_VERSION; len < 4 -> BAD_LENGTH; op unknown -> UNKNOWN_OP;
//   RELEASE_ALL / PING: len != 4 -> BAD_LENGTH.
//   PLAY: len < 5 -> BAD_LENGTH; n outside 1..16 -> BAD_SCHEDULE; len != 5 + 3n -> BAD_LENGTH;
//         then per step: mask bit 7 set, or not exactly one key changed -> BAD_SCHEDULE;
//         then last mask != 0 or sum of deltas > 100000 -> BAD_SCHEDULE.
// On success: op and seq are set; n = 0 for RELEASE_ALL / PING; for PLAY, steps[0..n) hold absolute t_us offsets.
inline uint8_t parse(const uint8_t* buf, size_t len, Command& out) {
    if (len >= 4) out.seq = detail::get_u16(buf + 2);
    if (len == 0) return BAD_LENGTH;
    if (buf[0] != VER) return BAD_VERSION;
    if (len < 4) return BAD_LENGTH;
    const uint8_t op = buf[1];
    if (op != PLAY && op != RELEASE_ALL && op != PING) return UNKNOWN_OP;
    if (op != PLAY) {
        if (len != 4) return BAD_LENGTH;
        out.op = op;
        out.n = 0;
        return 0;
    }
    if (len < 5) return BAD_LENGTH;
    const uint8_t n = buf[4];
    if (n < 1 || n > MAX_STEPS) return BAD_SCHEDULE;
    if (len != 5 + 3 * (size_t)n) return BAD_LENGTH;

    Step steps[MAX_STEPS];
    uint32_t sum = 0;      // 100 us units; at most 16 * 65535, no overflow
    uint8_t prev = 0;      // the state before step 0 is all-up
    for (uint8_t i = 0; i < n; ++i) {
        const uint8_t* p = buf + 5 + 3 * i;
        const uint8_t mask = p[2];
        if (mask & 0x80) return BAD_SCHEDULE;
        if (!detail::exactly_one_bit((uint8_t)(mask ^ prev))) return BAD_SCHEDULE;
        sum += detail::get_u16(p);
        steps[i].t_us = sum * detail::DELTA_UNIT_US;
        steps[i].mask = mask;
        prev = mask;
    }
    if (prev != 0) return BAD_SCHEDULE;
    if (sum > detail::MAX_SUM_DELTA) return BAD_SCHEDULE;

    out.op = PLAY;
    out.n = n;
    for (uint8_t i = 0; i < n; ++i) out.steps[i] = steps[i];
    return 0;
}

// C2: report = [modifiers, 0x00, k0..k5]; down keys other than Alt fill keys[] in mask-bit order, rest zero.
inline void mask_to_report(uint8_t mask, uint8_t report[8]) {
    static const uint8_t usage[7] = {0x50, 0x4F, 0x52, 0x51, 0x08, 0x00, 0x2C};  // bit 5 (ALT) is a modifier
    for (int i = 0; i < 8; ++i) report[i] = 0;
    if (mask & (1u << 5)) report[0] = 0x04;   // left Alt
    int slot = 2;
    for (int bit = 0; bit < 7; ++bit) {
        if (bit == 5 || !(mask & (1u << bit))) continue;
        report[slot++] = usage[bit];
    }
}

// Each encoder writes one notification into out and returns its length. out must hold the length:
// ACCEPTED 8, DONE 13 + 2n (at most 45), REJECTED 5, PONG 17.
inline size_t encode_accepted(uint8_t* out, uint16_t seq, uint32_t t0_us) {
    uint8_t* p = detail::put_header(out, detail::KIND_ACCEPTED, seq);
    p = detail::put_u32(p, t0_us);
    return (size_t)(p - out);
}

inline size_t encode_done(uint8_t* out, uint16_t seq, uint8_t status, uint8_t fail_step, int16_t rc, uint32_t t0_us,
                          uint8_t n, const uint16_t* late_us) {
    uint8_t* p = detail::put_header(out, detail::KIND_DONE, seq);
    *p++ = status;
    *p++ = fail_step;
    p = detail::put_u16(p, (uint16_t)rc);
    p = detail::put_u32(p, t0_us);
    *p++ = n;
    for (uint8_t i = 0; i < n; ++i) p = detail::put_u16(p, late_us[i]);
    return (size_t)(p - out);
}

inline size_t encode_rejected(uint8_t* out, uint16_t seq, uint8_t reason) {
    uint8_t* p = detail::put_header(out, detail::KIND_REJECTED, seq);
    *p++ = reason;
    return (size_t)(p - out);
}

inline size_t encode_pong(uint8_t* out, uint16_t seq, uint16_t itvl, uint16_t latency, uint16_t timeout, uint16_t mtu,
                          uint8_t flags, uint32_t board_us) {
    uint8_t* p = detail::put_header(out, detail::KIND_PONG, seq);
    p = detail::put_u16(p, itvl);
    p = detail::put_u16(p, latency);
    p = detail::put_u16(p, timeout);
    p = detail::put_u16(p, mtu);
    *p++ = flags;
    p = detail::put_u32(p, board_us);
    return (size_t)(p - out);
}

}  // namespace proto
