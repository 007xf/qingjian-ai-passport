#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PASSPORT_BLE_SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define PASSPORT_BLE_RX_UUID "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define PASSPORT_BLE_TX_UUID "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
#define PASSPORT_BLE_MAX_RX 244
#define PASSPORT_BLE_MAX_WRITE 4096
#define PASSPORT_BLE_TX_QUEUE_DEPTH 2

typedef enum {
    PASSPORT_BLE_PAIR_NONE, PASSPORT_BLE_PAIR_WAITING, PASSPORT_BLE_PAIR_SUCCESS,
    PASSPORT_BLE_PAIR_FAILED, PASSPORT_BLE_PAIR_DISCONNECTED
} passport_ble_pair_result_t;

typedef struct {
    bool running, synced, advertising, connected, secured, subscribed, pairing_open;
    uint16_t mtu;
    uint32_t session, rx_rejected, tx_rejected;
    uint32_t heap_before_start, heap_after_sync;
    int last_error;
    passport_ble_pair_result_t pairing_result;
    char name[24];
} passport_ble_status_t;

/* Both callbacks execute in the NimBLE host task. They must COPY and queue
 * their input with zero wait; never call LVGL, NVS, parsers, or blocking work.
 * RX data is borrowed until return. data=NULL,len=0 is an ordered parser-reset
 * barrier on every connection/disconnection; return false to fail closed.
 * A session is valid only until the next barrier. Retain it with each command. */
typedef bool (*passport_ble_rx_callback_t)(const uint8_t *data, size_t len,
                                         uint32_t session, void *ctx);
/* visible=true requests a six-digit display (including leading zeroes).
 * visible=false is sent after success, failure and disconnect; the status
 * snapshot distinguishes those results. Never log or persist the passkey. */
typedef bool (*passport_ble_pairing_callback_t)(bool visible, uint32_t passkey, void *ctx);

/* Install the UI queue sink BEFORE start. Start rejects a missing sink and
 * insecure SDK configurations. Start must run outside the LVGL/button task;
 * it initializes NimBLE and launches its host, but never erases NVS. */
bool passport_ble_set_pairing_callback(passport_ble_pairing_callback_t callback, void *ctx);
bool passport_ble_start(passport_ble_rx_callback_t callback, void *ctx);
/* Only call from a trusted device menu or USB action. 0 closes the window;
 * new pairing is closed by default, maximum duration is 120 seconds. Existing
 * authenticated SC bonds reconnect without reopening this window. */
bool passport_ble_open_pairing(uint32_t duration_ms);

/* Atomic bounded enqueue of 1..4096 bytes; false means NOT queued. Notifications
 * are fragmented by the host at min(negotiated MTU-3,244). For async replies use
 * the request's original session; write() is only for current-session events.
 * Accepted is not remote delivery: disconnect discards outstanding data. */
bool passport_ble_write(const uint8_t *data, size_t len);
bool passport_ble_write_session(const uint8_t *data, size_t len, uint32_t session);
void passport_ble_disconnect(void); /* asynchronous, clears pending stream */
void passport_ble_get_status(passport_ble_status_t *out);
bool passport_ble_available(void); /* host running/synced, not necessarily connected */

/* Integration/build settings (apply to the GENERATED sdkconfig, not just its
 * defaults). main requires the IDF bt, esp_timer and nvs_flash components.
 *
 * CONFIG_BT_ENABLED=y
 * CONFIG_BT_NIMBLE_ENABLED=y
 * CONFIG_BT_NIMBLE_ROLE_PERIPHERAL=y
 * CONFIG_BT_NIMBLE_ROLE_BROADCASTER=y
 * CONFIG_BT_NIMBLE_ROLE_CENTRAL=n
 * CONFIG_BT_NIMBLE_ROLE_OBSERVER=n
 * CONFIG_BT_NIMBLE_MAX_CONNECTIONS=1
 * CONFIG_BT_NIMBLE_SECURITY_ENABLE=y
 * CONFIG_BT_NIMBLE_SM_SC=y
 * CONFIG_BT_NIMBLE_SM_SC_ONLY=1
 * CONFIG_BT_NIMBLE_SM_LVL=4
 * CONFIG_BT_NIMBLE_SM_LEGACY=n
 * CONFIG_BT_NIMBLE_SM_SC_DEBUG_KEYS=n
 * CONFIG_BT_NIMBLE_NVS_PERSIST=y
 * CONFIG_BT_NIMBLE_HANDLE_REPEAT_PAIRING_DELETION=n
 * CONFIG_BT_NIMBLE_MAX_BONDS=3
 * CONFIG_BT_NIMBLE_ATT_PREFERRED_MTU=247
 * CONFIG_BT_NIMBLE_ATT_MAX_PREP_ENTRIES=4
 * CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=4096
 * CONFIG_BT_NIMBLE_LOG_LEVEL_WARNING=y
 *
 * Adapter storage is three 4,104-byte fixed TX slots (12,312 bytes), plus a
 * two-index queue and small NPL/control state. It creates no extra TX/RX task.
 * RX uses <=244 stack bytes. NimBLE host/controller allocations are ADDITIONAL;
 * measure heap_before_start/heap_after_sync and pairing high-water marks.
 *
 * CoreBluetooth RX writes MUST use .withResponse and split at the smaller of
 * 244 and maximumWriteValueLength(.withoutResponse), even though writes are
 * acknowledged. Long/prepare writes are deliberately outside this protocol.
 */
