//This works as expected but my own implementation doesnt.
// #include <NimBleKeyboard.h>
// BleKeyboard bleKeyboard;

// void setup() {
//   Serial.begin(115200);
//   Serial.println("Starting NimBLE work!");
//   bleKeyboard.begin();
// }

// void loop() {
//   if(bleKeyboard.isConnected()) {
//     Serial.println("Sending 'Hello world'...");
//     bleKeyboard.print("Hello world");

//     delay(1000);

//     Serial.println("Sending Enter key...");
//     bleKeyboard.write(KEY_RETURN);

//     delay(1000);

//     Serial.println("Sending Play/Pause media key...");
//     bleKeyboard.write(KEY_MEDIA_PLAY_PAUSE);

//     delay(1000);
    
//    //
//    // Below is an example of pressing multiple keyboard modifiers 
//    // which by default is commented out. 
//    // 
//    /* Serial.println("Sending Ctrl+Alt+Delete...");
//     bleKeyboard.press(KEY_LEFT_CTRL);
//     bleKeyboard.press(KEY_LEFT_ALT);
//     bleKeyboard.press(KEY_DELETE);
//     delay(100);
//     bleKeyboard.releaseAll();
//     */

//   }
//   Serial.println("Waiting 5 seconds...");
//   delay(5000);
// }



#include <NimBleKeyboard.h>
#include <atomic>
#include <string.h>
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "protocol.h"   // wire protocol v1 (Plan A C1)

#define SERVICE_UUID        "a723b49a-2b53-46a6-a2db-02bb9e9a577c"
#define KEYBOARDINPUTCHARACTERISTIC_UUID "61adcd67-20e9-4c38-b1d0-879445c5b732"
#define PCRESPONSECHARACTERISTIC_UUID "89f7c2ca-89b5-4234-905c-d4a931feaa3b"

// Start jitter (PA-D1 -> A, PA-rev I8): t0 = now + U(0, CI). A probe build may pass -DSTART_JITTER=0; HEAD stays 1.
#ifndef START_JITTER
#define START_JITTER 1
#endif

// Reply-notify diagnostic (A-M6-D1 -> A). A diagnostic build passes -DNOTIFY_DIAG=1; HEAD stays 0 and compiles none of
// the NOTIFY_DIAG code. On: notifyPeer keeps each reply's NimBLE rc and prints serial `NOTIFY ...` lines (see notifyPeer).
#ifndef NOTIFY_DIAG
#define NOTIFY_DIAG 0
#endif

// ---- Connection table (PA-adj1c-D1 -> A). Written by the NimBLE host task, read by the loop task: all under linksMux.
struct LinkEntry { uint16_t handle; bool subscribed; };
static LinkEntry links[CONFIG_BT_NIMBLE_MAX_CONNECTIONS];
static portMUX_TYPE linksMux = portMUX_INITIALIZER_UNLOCKED;

static void linksInit() {
  for (auto& e : links) { e.handle = BLE_HS_CONN_HANDLE_NONE; e.subscribed = false; }
}

// Caller holds linksMux.
static LinkEntry* linkFindLocked(uint16_t handle) {
  for (auto& e : links) if (e.handle == handle) return &e;
  return nullptr;
}

// onConnect adds {handle, false}; onSubscribe adds the entry if absent and sets `subscribed`.
static void linkUpsert(uint16_t handle, bool setSubscribed, bool subscribed) {
  portENTER_CRITICAL(&linksMux);
  LinkEntry* e = linkFindLocked(handle);
  if (!e) {
    e = linkFindLocked(BLE_HS_CONN_HANDLE_NONE);
    if (e) { e->handle = handle; e->subscribed = false; }
  }
  if (e && setSubscribed) e->subscribed = subscribed;
  portEXIT_CRITICAL(&linksMux);
}

static void linkRemove(uint16_t handle) {
  portENTER_CRITICAL(&linksMux);
  LinkEntry* e = linkFindLocked(handle);
  if (e) { e->handle = BLE_HS_CONN_HANDLE_NONE; e->subscribed = false; }
  portEXIT_CRITICAL(&linksMux);
}

static bool linkConnected(uint16_t handle) {
  portENTER_CRITICAL(&linksMux);
  bool found = linkFindLocked(handle) != nullptr;
  portEXIT_CRITICAL(&linksMux);
  return found;
}

static bool linkSubscribed(uint16_t handle) {
  portENTER_CRITICAL(&linksMux);
  LinkEntry* e = linkFindLocked(handle);
  bool sub = e && e->subscribed;
  portEXIT_CRITICAL(&linksMux);
  return sub;
}

// ---- Command queue (ISSUES #5 fix): onWrite (host task) queues, loop() (loop task) executes.
struct QueueItem {
  uint16_t handle;                       // the peer that wrote the command; all replies go to it
  uint16_t len;                          // bytes in `bytes` (an over-long write is cut to MAX_CMD + 1, still BAD_LENGTH)
  bool counted;                          // a RELEASE_ALL counted in abortRequested; decremented once at dequeue
  uint8_t bytes[proto::MAX_CMD + 1];
};
static QueueHandle_t cmdQueue = nullptr;
// Queued RELEASE_ALLs (A-PF m9, PA-rev6b-I-1, PA-rev6c-D1): ++ in onWrite before the enqueue, -- at dequeue of a
// counted item, back out only on QUEUE_FULL. Never reset. A PLAY is aborted while it is > 0.
static std::atomic<int> abortRequested{0};

static NimBLECharacteristic* pResponseChar = nullptr;   // C1 notify characteristic
static NimBLECharacteristic* inputReport = nullptr;     // HID keyboard input report (report ID 1), or nullptr

#if NOTIFY_DIAG
// Every reply notify's NimBLE rc is kept. Running totals over all reply kinds (written by both tasks):
static std::atomic<uint32_t> diagSent{0}, diagFailed{0}, diagPongFailed{0}, diagRejectedFailed{0};
// The last ACCEPTED's rc, printed with its DONE. Loop task only: ACCEPTED and DONE are sent only from loop()
// (runPlay, runReleaseAll, the queued-ABORT reply); onWrite sends only PONG and REJECTED (QUEUE_FULL).
static bool diagAccPending = false;
static uint16_t diagAccSeq = 0;
static int diagAccRc = 0;

// One serial line per DONE, printed by the loop task right after the DONE is sent, so never inside a schedule or in
// onWrite (A-F5-D3). Non-blocking: a line that does not fit the TX ring is dropped whole (setup()); `sent` then jumps.
//   NOTIFY seq=<s> accepted_rc=<rc|none> done_rc=<rc|none> sent=<n> failed=<k> pong_failed=<p> rejected_failed=<r>
// accepted_rc=none: no ACCEPTED before this DONE (RELEASE_ALL, queued ABORTED). done_rc=none: an ACCEPTED whose DONE
// was never sent (DROP), printed at the next ACCEPTED or DONE. rc is 0 or the NimBLE rc (BLE_HS_ENOMEM = 6, ...).
static void diagPrint(uint16_t seq, const char* accRc, const char* doneRc) {
  Serial.printf("NOTIFY seq=%u accepted_rc=%s done_rc=%s sent=%lu failed=%lu pong_failed=%lu rejected_failed=%lu\n",
                seq, accRc, doneRc, (unsigned long)diagSent.load(), (unsigned long)diagFailed.load(),
                (unsigned long)diagPongFailed.load(), (unsigned long)diagRejectedFailed.load());
}

static void diagFlushAccepted() {
  if (!diagAccPending) return;
  diagAccPending = false;
  char acc[12];
  snprintf(acc, sizeof(acc), "%d", diagAccRc);
  diagPrint(diagAccSeq, acc, "none");
}

static void notifyPeer(uint16_t handle, const uint8_t* buf, size_t len) {
  // The calls notify() makes for one peer and a custom value (NimBLECharacteristic::sendValue, NimBLE-Arduino 2.5.0
  // NimBLECharacteristic.cpp:276-290, :321-325, which turns rc != 0 into false), made here so the rc is kept, as
  // sendKeyReport does. The diagnostic build drops only sendValue's NIMBLE_LOGE line on a failure.
  os_mbuf* om = ble_hs_mbuf_from_flat(buf, len);
  const int rc = om == nullptr ? BLE_HS_ENOMEM : ble_gattc_notify_custom(handle, pResponseChar->getHandle(), om);
  const uint8_t kind = buf[1];
  const uint16_t seq = (uint16_t)(buf[2] | (buf[3] << 8));   // C1 header: ver, kind, seq (u16 LE), protocol.h
  diagSent.fetch_add(1);
  if (rc != 0) {
    diagFailed.fetch_add(1);
    if (kind == proto::detail::KIND_PONG) diagPongFailed.fetch_add(1);
    if (kind == proto::detail::KIND_REJECTED) diagRejectedFailed.fetch_add(1);
  }
  if (kind == proto::detail::KIND_ACCEPTED) {   // held until its DONE
    diagFlushAccepted();
    diagAccPending = true;
    diagAccSeq = seq;
    diagAccRc = rc;
  } else if (kind == proto::detail::KIND_DONE) {
    if (diagAccPending && diagAccSeq != seq) diagFlushAccepted();
    char acc[12], done[12];
    if (diagAccPending) snprintf(acc, sizeof(acc), "%d", diagAccRc);
    else snprintf(acc, sizeof(acc), "none");
    snprintf(done, sizeof(done), "%d", rc);
    diagAccPending = false;
    diagPrint(seq, acc, done);
  }
}
#else
static void notifyPeer(uint16_t handle, const uint8_t* buf, size_t len) {
  pResponseChar->notify(buf, len, handle);
}
#endif

static void sendRejected(uint16_t handle, uint16_t seq, uint8_t reason) {
  uint8_t buf[5];
  notifyPeer(handle, buf, proto::encode_rejected(buf, seq, reason));
}

static void sendDone(uint16_t handle, uint16_t seq, uint8_t status, uint8_t failStep, int16_t rc, uint32_t t0us,
                     uint8_t n, const uint16_t* late) {
  uint8_t buf[13 + 2 * proto::MAX_STEPS];   // n <= MAX_STEPS at every call site (A-F4-R M4)
  notifyPeer(handle, buf, proto::encode_done(buf, seq, status, failStep, rc, t0us, n, late));
}

// One HID keyboard report to one peer. Returns 0 or the NimBLE rc; -1 without a NimBLE call if the lookup failed.
// Same calls as NimBLECharacteristic::sendValue's custom-value branch (NimBLECharacteristic.cpp:276-289), made here
// so the rc reaches DONE (notify() returns only a bool).
static int sendKeyReport(uint16_t handle, uint8_t mask) {
  if (inputReport == nullptr) return -1;
  uint8_t report[8];
  proto::mask_to_report(mask, report);
  os_mbuf* om = ble_hs_mbuf_from_flat(report, sizeof(report));
  if (om == nullptr) return BLE_HS_ENOMEM;
  return ble_gattc_notify_custom(handle, inputReport->getHandle(), om);
}

static void sendPong(uint16_t handle, uint16_t seq) {
  NimBLEConnInfo info = NimBLEDevice::getServer()->getPeerInfoByHandle(handle);   // live, not the onConnect copy
  uint8_t flags = (info.isBonded() ? proto::BONDED : 0) | (info.isEncrypted() ? proto::ENCRYPTED : 0) |
                  (info.isAuthenticated() ? proto::AUTHENTICATED : 0) | (linkSubscribed(handle) ? proto::SUBSCRIBED : 0);
  uint8_t buf[17];
  notifyPeer(handle, buf, proto::encode_pong(buf, seq, info.getConnInterval(), info.getConnLatency(),
                                             info.getConnTimeout(), info.getMTU(), flags,
                                             (uint32_t)esp_timer_get_time()));
}

class CommandCallbacks : public NimBLECharacteristicCallbacks {
  public:
  void onWrite(NimBLECharacteristic* pChar, NimBLEConnInfo& connInfo) override {
    const NimBLEAttValue& val = pChar->getValue();   // the bytes just written
    QueueItem item;
    item.handle = connInfo.getConnHandle();
    item.len = val.length() > proto::MAX_CMD + 1 ? proto::MAX_CMD + 1 : val.length();
    item.counted = false;
    memcpy(item.bytes, val.data(), item.len);

    proto::Command cmd;
    cmd.seq = 0;
    const bool valid = proto::parse(item.bytes, item.len, cmd) == 0;
    if (valid && cmd.op == proto::PING) {   // answered here, never queued
      sendPong(item.handle, cmd.seq);
      return;
    }
    if (valid && cmd.op == proto::RELEASE_ALL) {
      item.counted = true;
      abortRequested.fetch_add(1);           // first, so the count is never below the queued RELEASE_ALLs
    }
    if (xQueueSendToBack(cmdQueue, &item, 0) != pdTRUE) {
      if (item.counted) abortRequested.fetch_sub(1);   // never queued: this is its one decrement
      sendRejected(item.handle, cmd.seq, proto::QUEUE_FULL);
    }
  }
};

// HID input-report subscription tracking (the library attaches no callbacks to it, NimBleKeyboard.cpp:105).
class HidSubscribeCallbacks : public NimBLECharacteristicCallbacks {
  public:
  void onSubscribe(NimBLECharacteristic* pChar, NimBLEConnInfo& connInfo, uint16_t subValue) override {
    const bool notifyOn = (subValue & 1) != 0;
    linkUpsert(connInfo.getConnHandle(), true, notifyOn);
    Serial.printf("SUB handle=%u notify=%u\n", connInfo.getConnHandle(), notifyOn ? 1u : 0u);
  }
};

// CI request (Plan A F3 step 5): set by onConnect, sent from loop() >= 1.5 s later, once per connection.
volatile bool ciRequestPending = false;
volatile uint16_t ciRequestHandle = 0;
volatile unsigned long ciRequestAtMs = 0;

static void printConn(const char* ev, NimBLEConnInfo& connInfo) {
  Serial.printf("CONN ev=%s handle=%u itvl_ms=%.2f latency=%u timeout_ms=%u mtu=%u bonded=%d encrypted=%d auth=%d\n",
                ev, connInfo.getConnHandle(), connInfo.getConnInterval() * 1.25, connInfo.getConnLatency(),
                connInfo.getConnTimeout() * 10, connInfo.getMTU(), connInfo.isBonded(), connInfo.isEncrypted(),
                connInfo.isAuthenticated());
}

// Copy of the library's lookup (NimBLEHIDDevice.cpp:157-167) through the public API, with the unchecked
// descriptor dereference guarded: service 0x1812, characteristic 0x2A4D instances 0,1,2,..., the one whose
// 0x2908 descriptor value is {0x01, 0x01} (report ID 1, input).
static NimBLECharacteristic* findHidInputReport(NimBLEServer* pServer) {
  NimBLEService* hidSvc = pServer->getServiceByUUID(NimBLEUUID((uint16_t)0x1812));
  if (hidSvc == nullptr) return nullptr;
  const NimBLEUUID reportUuid((uint16_t)0x2A4D), refUuid((uint16_t)0x2908);
  for (uint16_t i = 0; i != 0xFFFF; ++i) {
    NimBLECharacteristic* candidate = hidSvc->getCharacteristic(reportUuid, i);
    if (candidate == nullptr) return nullptr;
    NimBLEDescriptor* dsc = candidate->getDescriptorByUUID(refUuid);
    if (dsc == nullptr) continue;
    const NimBLEAttValue& v = dsc->getValue();
    if (v.length() >= 2 && v.data()[0] == 0x01 && v.data()[1] == 0x01) return candidate;
  }
  return nullptr;
}

class HelixBLEKeyboard : public BleKeyboard {
  NimBLEService* pService;
  NimBLECharacteristic* pCharacteristic1;
  NimBLECharacteristic* pCharacteristic2;


  public:
  virtual void onStarted(NimBLEServer *pServer) override {
    pService = pServer->createService(SERVICE_UUID);
    pCharacteristic1 = pService->createCharacteristic(KEYBOARDINPUTCHARACTERISTIC_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE);
    pCharacteristic2 = pService->createCharacteristic(PCRESPONSECHARACTERISTIC_UUID, NIMBLE_PROPERTY::NOTIFY);
    pResponseChar = pCharacteristic2;
    pCharacteristic1->setCallbacks(new CommandCallbacks());
    NimBLEDevice::setSecurityAuth(true, false, true);       // bonding, no MITM, SC  (Helix 3544179, ISSUES #3 Fix)
    NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT); // Just Works (headless board)

    inputReport = findHidInputReport(pServer);
    if (inputReport == nullptr) {
      Serial.println("ERROR HID input report not found");   // no callbacks: never subscribed, every PLAY REJECTED 6
    } else {
      Serial.println("HID input report found");
      inputReport->setCallbacks(new HidSubscribeCallbacks());   // not `this`: it is the output report's callbacks
    }
   };

   virtual void onConnect(NimBLEServer* pServer, NimBLEConnInfo &connInfo) override{
    Serial.println("OnConnect method executed, advertising again.");
    linkUpsert(connInfo.getConnHandle(), false, false);
    printConn("connect", connInfo);
    const unsigned peers = pServer->getConnectedCount();
    Serial.printf("%sPEERS count=%u\n", peers > 1 ? "WARN " : "", peers);
    ciRequestHandle = connInfo.getConnHandle();
    ciRequestAtMs = millis();
    ciRequestPending = true;
    BleKeyboard::onConnect(pServer, connInfo);
    pServer->getAdvertising()->start();
    }

   virtual void onDisconnect(NimBLEServer* pServer, NimBLEConnInfo &connInfo, int reason) override {
    BleKeyboard::onDisconnect(pServer, connInfo, reason);
    Serial.printf("DISC handle=%u reason=%d\n", connInfo.getConnHandle(), reason);
    linkRemove(connInfo.getConnHandle());
   }

   virtual void onAuthenticationComplete(NimBLEConnInfo& connInfo) override {
    printConn("auth", connInfo);
   }

   virtual void onConnParamsUpdate(NimBLEConnInfo& connInfo) override {
    printConn("params", connInfo);
   }

   virtual void onMTUChange(uint16_t MTU, NimBLEConnInfo& connInfo) override {
    Serial.printf("MTU handle=%u mtu=%u\n", connInfo.getConnHandle(), MTU);
   }
};


int count = 0;

HelixBLEKeyboard bleKeyboard;
// BleKeyboard bleKeyboard;

void setup() {
  Serial.begin(115200);
  // Non-blocking log writes (A-F5-adj-D1): with the default 100 ms tx timeout, HWCDC::write retries up to 20 x 100 ms
  // once a host read the port and stopped (core 3.3.10 HWCDC.cpp:582-603), stalling loop()/onWrite ~2 s per line.
  // At 0, a line that does not fit the TX ring is dropped instead (HWCDC.cpp:544, :590).
  Serial.setTxTimeoutMs(0);
  while (!Serial && millis() < 3000) { delay(10); }  // host wait (PA-rev I9)
  Serial.println("Starting NimBLE work!");
  linksInit();
  cmdQueue = xQueueCreate(4, sizeof(QueueItem));
  bleKeyboard.setName("Guiness");
  bleKeyboard.setDelay(0);
  bleKeyboard.begin();
  Serial.printf("BONDS=%d\n", NimBLEDevice::getNumBonds());
}

// Waits until `target` (esp_timer us): vTaskDelay(1) while more than 2 ms remain, then spins.
// Returns false early when a RELEASE_ALL is counted or the handle has left the table.
static bool waitUntil(int64_t target, uint16_t handle) {
  for (;;) {
    if (abortRequested.load() > 0 || !linkConnected(handle)) return false;
    const int64_t remain = target - esp_timer_get_time();
    if (remain <= 0) return true;
    if (remain > 2000) vTaskDelay(1);
  }
}

static uint16_t lateUs(int64_t actual, int64_t scheduled) {
  const int64_t d = actual - scheduled;
  return d <= 0 ? 0 : (d >= 65535 ? 65535 : (uint16_t)d);
}

static void runPlay(uint16_t handle, const proto::Command& cmd) {
  int64_t t0 = esp_timer_get_time();
#if START_JITTER
  const uint32_t ciUs = NimBLEDevice::getServer()->getPeerInfoByHandle(handle).getConnInterval() * 1250u;
  if (ciUs > 0) t0 += esp_random() % ciUs;   // U(0, CI) with the interval in force now
#endif
  const uint32_t t0us = (uint32_t)t0;
  uint8_t buf[8];
  notifyPeer(handle, buf, proto::encode_accepted(buf, cmd.seq, t0us));

  uint16_t late[proto::MAX_STEPS];
  uint8_t sent = 0, down = 0;
  for (uint8_t i = 0; i < cmd.n; ++i) {
    const int64_t due = t0 + cmd.steps[i].t_us;
    if (!waitUntil(due, handle)) break;
    late[i] = lateUs(esp_timer_get_time(), due);
    const int rc = sendKeyReport(handle, cmd.steps[i].mask);
    if (rc != 0) {
      sendKeyReport(handle, 0);
      sendDone(handle, cmd.seq, proto::NOTIFY_FAILED, i, (int16_t)rc, t0us, i + 1, late);
      Serial.printf("ACCEPTED seq=%u t0_us=%lu\nDONE seq=%u status=1 fail_step=%u rc=%d n=%u\n", cmd.seq,
                    (unsigned long)t0us, cmd.seq, i, rc, i + 1);
      return;
    }
    down = cmd.steps[i].mask;
    sent = i + 1;
  }
  if (sent == cmd.n) {
    sendDone(handle, cmd.seq, proto::OK, 0xFF, 0, t0us, sent, late);
    uint16_t maxLate = 0;
    for (uint8_t i = 0; i < sent; ++i) if (late[i] > maxLate) maxLate = late[i];
    Serial.printf("ACCEPTED seq=%u t0_us=%lu\nDONE seq=%u status=0 n=%u max_late_us=%u\n", cmd.seq,
                  (unsigned long)t0us, cmd.seq, sent, maxLate);
    return;
  }
  if (!linkConnected(handle)) {   // the peer left mid-schedule: stop, no DONE can be sent
    Serial.printf("ACCEPTED seq=%u t0_us=%lu\nDROP seq=%u disconnected after n=%u\n", cmd.seq, (unsigned long)t0us,
                  cmd.seq, sent);
    return;
  }
  if (down != 0) sendKeyReport(handle, 0);   // aborted by RELEASE_ALL: all-up if a key is down
  sendDone(handle, cmd.seq, proto::ABORTED, 0xFF, 0, t0us, sent, late);
  Serial.printf("ACCEPTED seq=%u t0_us=%lu\nDONE seq=%u status=3 n=%u\n", cmd.seq, (unsigned long)t0us, cmd.seq,
                sent);
}

static void runReleaseAll(uint16_t handle, uint16_t seq) {
  const uint32_t t0us = (uint32_t)esp_timer_get_time();   // read when the all-up send starts
  const uint16_t late[1] = {0};
  const int rc = sendKeyReport(handle, 0);
  if (rc != 0) sendDone(handle, seq, proto::NOTIFY_FAILED, 0, (int16_t)rc, t0us, 1, late);
  else sendDone(handle, seq, proto::OK, 0xFF, 0, t0us, 1, late);
  Serial.printf("DONE seq=%u release_all status=%u rc=%d\n", seq, rc != 0 ? 1u : 0u, rc);
}

void loop() {
  if (ciRequestPending && millis() - ciRequestAtMs >= 1500) {   // between commands, never inside a schedule
    ciRequestPending = false;
    NimBLEDevice::getServer()->updateConnParams(ciRequestHandle, 9, 12, 0, 100);  // 11.25-15 ms, latency 0, 1 s timeout
    Serial.printf("CIREQ handle=%u\n", ciRequestHandle);
  }
  if (count == 0){
    count += 1;
    unsigned long int x = (uint64_t) &bleKeyboard;
    Serial.println(x);
  }

  QueueItem item;
  if (xQueueReceive(cmdQueue, &item, pdMS_TO_TICKS(10)) != pdTRUE) return;
  if (item.counted) abortRequested.fetch_sub(1);   // exactly once per counted item, before anything else
  if (!linkConnected(item.handle)) return;          // writer gone: drop, no reply possible

  proto::Command cmd;
  cmd.seq = 0;
  const uint8_t reject = proto::parse(item.bytes, item.len, cmd);
  if (reject != 0) {
    sendRejected(item.handle, cmd.seq, reject);
    Serial.printf("REJECTED seq=%u reason=%u\n", cmd.seq, reject);
    return;
  }
  if (cmd.op == proto::RELEASE_ALL) { runReleaseAll(item.handle, cmd.seq); return; }
  if (cmd.op != proto::PLAY) return;   // PING is answered in onWrite and never queued
  if (abortRequested.load() > 0) {     // queued PLAY behind a RELEASE_ALL: ABORTED before it started
    sendDone(item.handle, cmd.seq, proto::ABORTED, 0xFF, 0, 0, 0, nullptr);
    Serial.printf("DONE seq=%u status=3 n=0 (queued)\n", cmd.seq);
    return;
  }
  if (!linkSubscribed(item.handle)) {   // subscription gate (PA-adj1c-D1 -> A)
    sendRejected(item.handle, cmd.seq, proto::HID_NOT_SUBSCRIBED);
    Serial.printf("REJECTED seq=%u reason=6\n", cmd.seq);
    return;
  }
  runPlay(item.handle, cmd);
}
