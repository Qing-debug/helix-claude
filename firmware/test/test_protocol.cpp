// Host test for firmware/Helix_BLE/protocol.h (Plan A Task F4).
// Driven by firmware/test/vectors.json, the golden vectors shared with the Python tests (P1).
// Build and run (Git Bash, repo root in $REPO):
//   g++ -std=c++17 -Wall -Werror -I$REPO/firmware/Helix_BLE $REPO/firmware/test/test_protocol.cpp
//       -o $REPO/firmware/build/test_protocol.exe && $REPO/firmware/build/test_protocol.exe
// The vectors path defaults to vectors.json next to this source file; argv[1] overrides it.
// Prints ALL PASS and exits 0 on success; prints each failure and exits 1 otherwise.

#include "protocol.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

// ---- minimal JSON reader (objects, arrays, strings without escapes beyond \" and \\, integers, booleans) ----
struct Json {
    enum Type { NUL, BOOL, NUM, STR, ARR, OBJ } type = NUL;
    long long num = 0;
    bool b = false;
    std::string str;
    std::vector<Json> arr;
    std::map<std::string, Json> obj;
    bool has(const std::string& k) const { return type == OBJ && obj.count(k) != 0; }
    const Json& at(const std::string& k) const {
        auto it = obj.find(k);
        if (it == obj.end()) { std::fprintf(stderr, "missing key %s\n", k.c_str()); std::exit(2); }
        return it->second;
    }
};

struct JsonReader {
    const std::string& s;
    size_t i = 0;
    explicit JsonReader(const std::string& src) : s(src) {}
    [[noreturn]] void fail(const char* what) {
        std::fprintf(stderr, "JSON parse error at offset %zu: %s\n", i, what);
        std::exit(2);
    }
    void ws() { while (i < s.size() && std::strchr(" \t\r\n", s[i])) ++i; }
    char peek() { ws(); if (i >= s.size()) fail("unexpected end"); return s[i]; }
    void expect(char c) { if (peek() != c) fail("unexpected character"); ++i; }
    std::string str() {
        expect('"');
        std::string out;
        while (i < s.size() && s[i] != '"') {
            if (s[i] == '\\') { ++i; if (i >= s.size()) fail("bad escape"); }
            out += s[i++];
        }
        expect('"');
        return out;
    }
    Json value() {
        Json v;
        char c = peek();
        if (c == '{') {
            v.type = Json::OBJ; ++i;
            if (peek() == '}') { ++i; return v; }
            for (;;) {
                std::string k = str();
                expect(':');
                v.obj[k] = value();
                if (peek() == ',') { ++i; continue; }
                expect('}');
                return v;
            }
        }
        if (c == '[') {
            v.type = Json::ARR; ++i;
            if (peek() == ']') { ++i; return v; }
            for (;;) {
                v.arr.push_back(value());
                if (peek() == ',') { ++i; continue; }
                expect(']');
                return v;
            }
        }
        if (c == '"') { v.type = Json::STR; v.str = str(); return v; }
        if (s.compare(i, 4, "true") == 0) { v.type = Json::BOOL; v.b = true; i += 4; return v; }
        if (s.compare(i, 5, "false") == 0) { v.type = Json::BOOL; v.b = false; i += 5; return v; }
        if (s.compare(i, 4, "null") == 0) { i += 4; return v; }
        if (c == '-' || (c >= '0' && c <= '9')) {
            size_t start = i;
            if (s[i] == '-') ++i;
            while (i < s.size() && s[i] >= '0' && s[i] <= '9') ++i;
            if (i < s.size() && std::strchr(".eE", s[i])) fail("only integers are supported");
            v.type = Json::NUM;
            v.num = std::stoll(s.substr(start, i - start));
            return v;
        }
        fail("unexpected value");
    }
};

// ---- test helpers ----
static int g_failures = 0;
static int g_checks = 0;

#define CHECK(cond, ...)                                              \
    do {                                                              \
        ++g_checks;                                                   \
        if (!(cond)) {                                                \
            ++g_failures;                                             \
            std::printf("FAIL %s:%d: ", __FILE__, __LINE__);          \
            std::printf(__VA_ARGS__);                                 \
            std::printf("\n");                                        \
        }                                                             \
    } while (0)

static std::vector<uint8_t> from_hex(const std::string& h) {
    std::vector<uint8_t> out;
    if (h.size() % 2 != 0) { std::fprintf(stderr, "odd-length hex %s\n", h.c_str()); std::exit(2); }
    for (size_t i = 0; i < h.size(); i += 2) out.push_back((uint8_t)std::stoul(h.substr(i, 2), nullptr, 16));
    return out;
}

static std::string to_hex(const uint8_t* b, size_t n) {
    static const char* d = "0123456789abcdef";
    std::string s;
    for (size_t i = 0; i < n; ++i) { s += d[b[i] >> 4]; s += d[b[i] & 15]; }
    return s;
}

static long long num(const Json& o, const char* k) { return o.at(k).num; }

// ---- command vectors: proto::parse ----
static void check_command(const std::string& name, const std::vector<uint8_t>& bytes, const Json& ex) {
    proto::Command c;
    std::memset(&c, 0xAA, sizeof c);  // poison so unfilled fields show up
    const uint8_t* buf = bytes.empty() ? nullptr : bytes.data();
    uint8_t rc = proto::parse(buf, bytes.size(), c);
    if (bytes.size() >= 4) {
        uint16_t seq = (uint16_t)(bytes[2] | (bytes[3] << 8));
        CHECK(c.seq == seq, "%s: seq %u, want %u (seq is filled whenever len >= 4)", name.c_str(), c.seq, seq);
    }
    if (ex.has("reject")) {
        CHECK(rc == num(ex, "reject"), "%s: parse returned %u, want reject %lld", name.c_str(), rc, num(ex, "reject"));
        return;
    }
    CHECK(rc == 0, "%s: parse returned %u, want 0", name.c_str(), rc);
    if (rc != 0) return;
    CHECK(num(ex, "ver") == proto::VER, "%s: vector ver %lld != proto::VER", name.c_str(), num(ex, "ver"));
    CHECK(c.op == num(ex, "op"), "%s: op %u, want %lld", name.c_str(), c.op, num(ex, "op"));
    CHECK(c.seq == num(ex, "seq"), "%s: seq %u, want %lld", name.c_str(), c.seq, num(ex, "seq"));
    if (c.op != proto::PLAY) {
        CHECK(c.n == 0, "%s: n %u, want 0 for a non-PLAY command", name.c_str(), c.n);
        return;
    }
    const Json& steps = ex.at("steps");
    CHECK(c.n == num(ex, "n") && (size_t)c.n == steps.arr.size(), "%s: n %u, want %lld", name.c_str(), c.n,
          num(ex, "n"));
    uint32_t t = 0;
    for (size_t k = 0; k < steps.arr.size() && k < (size_t)c.n; ++k) {
        t += (uint32_t)steps.arr[k].arr[0].num * 100u;  // delta is in 100 us units; Step.t_us is absolute
        uint8_t mask = (uint8_t)steps.arr[k].arr[1].num;
        CHECK(c.steps[k].t_us == t, "%s: step %zu t_us %u, want %u", name.c_str(), k, c.steps[k].t_us, t);
        CHECK(c.steps[k].mask == mask, "%s: step %zu mask 0x%02x, want 0x%02x", name.c_str(), k, c.steps[k].mask,
              mask);
    }
}

// ---- notification vectors: proto::encode_* ----
static void check_notification(const std::string& name, const std::vector<uint8_t>& bytes, const Json& ex) {
    uint8_t out[64];
    std::memset(out, 0xEE, sizeof out);
    size_t len = 0;
    uint16_t seq = (uint16_t)num(ex, "seq");
    CHECK(num(ex, "ver") == proto::VER, "%s: vector ver %lld != proto::VER", name.c_str(), num(ex, "ver"));
    switch (num(ex, "kind")) {
        case 0x81:
            len = proto::encode_accepted(out, seq, (uint32_t)num(ex, "t0_us"));
            break;
        case 0x82: {
            std::vector<uint16_t> late;
            for (const Json& v : ex.at("late_us").arr) late.push_back((uint16_t)v.num);
            CHECK((long long)late.size() == num(ex, "n"), "%s: vector n != len(late_us)", name.c_str());
            len = proto::encode_done(out, seq, (uint8_t)num(ex, "status"), (uint8_t)num(ex, "fail_step"),
                                     (int16_t)num(ex, "rc"), (uint32_t)num(ex, "t0_us"), (uint8_t)num(ex, "n"),
                                     late.empty() ? nullptr : late.data());
            break;
        }
        case 0x83:
            len = proto::encode_rejected(out, seq, (uint8_t)num(ex, "reason"));
            break;
        case 0x84:
            len = proto::encode_pong(out, seq, (uint16_t)num(ex, "itvl"), (uint16_t)num(ex, "latency"),
                                     (uint16_t)num(ex, "timeout"), (uint16_t)num(ex, "mtu"),
                                     (uint8_t)num(ex, "flags"), (uint32_t)num(ex, "board_us"));
            break;
        default:
            CHECK(false, "%s: unknown kind %lld", name.c_str(), num(ex, "kind"));
            return;
    }
    std::string got = to_hex(out, len), want = to_hex(bytes.data(), bytes.size());
    CHECK(got == want, "%s: encoded %s, want %s", name.c_str(), got.c_str(), want.c_str());
    CHECK(len <= 45, "%s: notification length %zu over the 45-byte maximum", name.c_str(), len);
    CHECK(out[len] == 0xEE, "%s: encoder wrote past its returned length", name.c_str());
}

// ---- C2 table: proto::mask_to_report ----
static void check_mask_to_report() {
    struct Case { uint8_t mask; uint8_t report[8]; };
    const Case cases[] = {
        {0x00, {0x00, 0, 0, 0, 0, 0, 0, 0}},
        {0x01, {0x00, 0, 0x50, 0, 0, 0, 0, 0}},  // LEFT
        {0x02, {0x00, 0, 0x4F, 0, 0, 0, 0, 0}},  // RIGHT
        {0x04, {0x00, 0, 0x52, 0, 0, 0, 0, 0}},  // UP
        {0x08, {0x00, 0, 0x51, 0, 0, 0, 0, 0}},  // DOWN
        {0x10, {0x00, 0, 0x08, 0, 0, 0, 0, 0}},  // E
        {0x20, {0x04, 0, 0, 0, 0, 0, 0, 0}},     // ALT -> modifiers (left Alt)
        {0x40, {0x00, 0, 0x2C, 0, 0, 0, 0, 0}},  // SPACE
        {0x11, {0x00, 0, 0x50, 0x08, 0, 0, 0, 0}},        // LEFT|E (teleport chord)
        {0x28, {0x04, 0, 0x51, 0, 0, 0, 0, 0}},           // DOWN|ALT (drop chord)
        {0x50, {0x00, 0, 0x08, 0x2C, 0, 0, 0, 0}},        // E|SPACE in mask-bit order
        {0x7F, {0x04, 0, 0x50, 0x4F, 0x52, 0x51, 0x08, 0x2C}},  // every key
    };
    for (const Case& cs : cases) {
        uint8_t r[8];
        std::memset(r, 0xEE, sizeof r);
        proto::mask_to_report(cs.mask, r);
        std::string got = to_hex(r, 8), want = to_hex(cs.report, 8);
        CHECK(got == want, "mask_to_report(0x%02x) = %s, want %s", cs.mask, got.c_str(), want.c_str());
    }
}

static std::string default_vectors_path() {
    std::string f = __FILE__;
    size_t slash = f.find_last_of("/\\");
    return (slash == std::string::npos ? std::string() : f.substr(0, slash + 1)) + "vectors.json";
}

int main(int argc, char** argv) {
    std::string path = argc > 1 ? argv[1] : default_vectors_path();
    std::ifstream in(path, std::ios::binary);
    if (!in) { std::fprintf(stderr, "cannot open %s\n", path.c_str()); return 2; }
    std::stringstream ss;
    ss << in.rdbuf();
    std::string text = ss.str();
    JsonReader reader(text);
    Json root = reader.value();
    if (root.type != Json::ARR || root.arr.empty()) { std::fprintf(stderr, "vectors.json: expected a non-empty array\n"); return 2; }

    int commands = 0, notifications = 0, skipped = 0;
    for (const Json& v : root.arr) {
        const std::string& name = v.at("name").str;
        if (v.has("pc_only") && v.at("pc_only").b) { ++skipped; continue; }
        std::vector<uint8_t> bytes = from_hex(v.at("hex").str);
        const Json& ex = v.at("expect");
        if (ex.has("kind")) { check_notification(name, bytes, ex); ++notifications; }
        else if (ex.has("op") || ex.has("reject")) { check_command(name, bytes, ex); ++commands; }
        else CHECK(false, "%s: expect has neither kind, op nor reject", name.c_str());
    }
    check_mask_to_report();

    std::printf("%d command vectors, %d notification vectors, %d pc_only skipped, %d checks\n", commands,
                notifications, skipped, g_checks);
    if (g_failures) { std::printf("%d FAILED\n", g_failures); return 1; }
    std::printf("ALL PASS\n");
    return 0;
}
