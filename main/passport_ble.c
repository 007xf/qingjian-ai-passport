#include "passport_ble.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "host/ble_att.h"
#include "host/ble_gap.h"
#include "host/ble_gatt.h"
#include "host/ble_hs.h"
#include "host/ble_sm.h"
#include "host/ble_store.h"
#include "host/ble_uuid.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "nvs_flash.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include <stdio.h>
#include <string.h>

static const char *TAG = "passport_ble";
#define PREFERRED_MTU 247
#define UNAUTHENTICATED_TIMEOUT_US INT64_C(5000000)
#define PASSKEY_TIMEOUT_US INT64_C(45000000)
#define TX_STALL_TIMEOUT_US INT64_C(3000000)

typedef struct {
    uint32_t session;
    uint16_t length;
    uint8_t bytes[PASSPORT_BLE_MAX_WRITE];
} tx_packet_t;

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static passport_ble_status_t s_status;
static passport_ble_rx_callback_t s_rx_callback;
static passport_ble_pairing_callback_t s_pair_callback;
static void *s_rx_ctx, *s_pair_ctx;
static QueueHandle_t s_tx_queue;
static struct ble_npl_event s_tx_event, s_control_event;
static struct ble_npl_callout s_tx_retry, s_watchdog;
#define TX_SLOT_COUNT (PASSPORT_BLE_TX_QUEUE_DEPTH + 1)
_Static_assert(TX_SLOT_COUNT <= 8, "TX slot mask has eight bits");
static tx_packet_t s_tx_slots[TX_SLOT_COUNT];
static uint8_t s_free_slots = (1u << TX_SLOT_COUNT) - 1;
static int s_active_slot = -1;
static size_t s_active_offset;
static bool s_initialized, s_disconnect_requested, s_clear_pin_pending;
static bool s_store_failed;
static uint16_t s_connection = BLE_HS_CONN_HANDLE_NONE, s_tx_handle;
static uint8_t s_address_type;
static int64_t s_pair_deadline_us, s_security_deadline_us, s_last_tx_progress_us;
static ble_store_write_fn *s_original_store_write;

static int gap_event(struct ble_gap_event *event, void *unused);
static void tx_flush(struct ble_npl_event *event);
static void watchdog(struct ble_npl_event *event);
static int advertise(void);

static const ble_uuid128_t s_service_uuid = BLE_UUID128_INIT(
    0x9E,0xCA,0xDC,0x24,0x0E,0xE5,0xA9,0xE0,0x93,0xF3,0xA3,0xB5,0x01,0x00,0x40,0x6E);
static const ble_uuid128_t s_rx_uuid = BLE_UUID128_INIT(
    0x9E,0xCA,0xDC,0x24,0x0E,0xE5,0xA9,0xE0,0x93,0xF3,0xA3,0xB5,0x02,0x00,0x40,0x6E);
static const ble_uuid128_t s_tx_uuid = BLE_UUID128_INIT(
    0x9E,0xCA,0xDC,0x24,0x0E,0xE5,0xA9,0xE0,0x93,0xF3,0xA3,0xB5,0x03,0x00,0x40,0x6E);

static void set_error(int error) {
    taskENTER_CRITICAL(&s_lock);
    s_status.last_error = error;
    taskEXIT_CRITICAL(&s_lock);
}

static bool pairing_open(void) {
    taskENTER_CRITICAL(&s_lock);
    int64_t deadline = s_pair_deadline_us;
    taskEXIT_CRITICAL(&s_lock);
    return deadline > esp_timer_get_time();
}

void passport_ble_get_status(passport_ble_status_t *out) {
    if (!out) return;
    taskENTER_CRITICAL(&s_lock);
    *out = s_status;
    int64_t deadline = s_pair_deadline_us;
    taskEXIT_CRITICAL(&s_lock);
    out->pairing_open = deadline > esp_timer_get_time();
}

bool passport_ble_available(void) {
    passport_ble_status_t status;
    passport_ble_get_status(&status);
    return status.running && status.synced;
}

static bool ready(uint32_t session) {
    return s_status.connected && s_status.secured && s_status.subscribed &&
           s_status.session == session && s_connection != BLE_HS_CONN_HANDLE_NONE;
}

static void clear_pin(passport_ble_pair_result_t result) {
    taskENTER_CRITICAL(&s_lock);
    s_status.pairing_result = result;
    taskEXIT_CRITICAL(&s_lock);
    s_clear_pin_pending = !s_pair_callback(false, 0, s_pair_ctx);
}

static int acquire_slot(void) {
    int slot = -1;
    taskENTER_CRITICAL(&s_lock);
    for (unsigned i = 0; i < TX_SLOT_COUNT; i++) {
        if (s_free_slots & (1u << i)) {
            s_free_slots &= (uint8_t)~(1u << i);
            slot = (int)i;
            break;
        }
    }
    taskEXIT_CRITICAL(&s_lock);
    return slot;
}

static void release_slot(unsigned slot) {
    if (slot >= TX_SLOT_COUNT) return;
    taskENTER_CRITICAL(&s_lock);
    s_free_slots |= (uint8_t)(1u << slot);
    taskEXIT_CRITICAL(&s_lock);
}

static bool clear_stream(void) {
    if (s_active_slot >= 0) release_slot((unsigned)s_active_slot);
    s_active_slot = -1;
    s_active_offset = 0;
    uint8_t slot;
    if (s_tx_queue) while (xQueueReceive(s_tx_queue, &slot, 0) == pdTRUE) release_slot(slot);
    ble_npl_callout_stop(&s_tx_retry);
    taskENTER_CRITICAL(&s_lock);
    s_status.session++;
    if (!s_status.session) s_status.session++;
    uint32_t session = s_status.session;
    taskEXIT_CRITICAL(&s_lock);
    bool accepted = s_rx_callback(NULL, 0, session, s_rx_ctx);
    if (!accepted) set_error(BLE_HS_ENOMEM);
    return accepted;
}

static void terminate_connection(int error) {
    if (error) set_error(error);
    taskENTER_CRITICAL(&s_lock);
    s_status.secured = false;
    taskEXIT_CRITICAL(&s_lock);
    if (s_connection != BLE_HS_CONN_HANDLE_NONE)
        ble_gap_terminate(s_connection, BLE_ERR_REM_USER_CONN_TERM);
}

static bool has_secure_bond(const struct ble_gap_conn_desc *connection) {
    struct ble_store_key_sec key = {.peer_addr = connection->peer_id_addr};
    if (key.peer_addr.type > BLE_ADDR_RANDOM_ID) return false;
    key.peer_addr.type &= 1; /* Store keys use identity types 0/1, never 2/3. */
    struct ble_store_value_sec value = {0};
    int rc = ble_store_read_our_sec(&key, &value);
    if (rc != 0 || !value.ltk_present) rc = ble_store_read_peer_sec(&key, &value);
    return rc == 0 && value.ltk_present && value.authenticated && value.sc && value.key_size == 16;
}

static int receive_value(uint16_t connection, uint16_t attribute,
                          struct ble_gatt_access_ctxt *context, void *unused) {
    (void)attribute;
    (void)unused;
    if (context->op != BLE_GATT_ACCESS_OP_WRITE_CHR ||
        ble_uuid_cmp(context->chr->uuid, &s_rx_uuid.u) != 0) return BLE_ATT_ERR_READ_NOT_PERMITTED;
    struct ble_gap_conn_desc desc;
    if (connection != s_connection || ble_gap_conn_find(connection, &desc) != 0 ||
        !desc.sec_state.encrypted || !desc.sec_state.authenticated || !desc.sec_state.bonded ||
        desc.sec_state.key_size != 16 || !s_status.secured || s_store_failed)
        return BLE_ATT_ERR_INSUFFICIENT_AUTHEN;
    uint16_t length = OS_MBUF_PKTLEN(context->om);
    uint16_t mtu = ble_att_mtu(connection);
    if (mtu < 23) return BLE_ATT_ERR_UNLIKELY;
    uint16_t max_payload = mtu - 3;
    if (!length || length > PASSPORT_BLE_MAX_RX || length > max_payload)
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    uint8_t bytes[PASSPORT_BLE_MAX_RX];
    if (os_mbuf_copydata(context->om, 0, length, bytes) != 0) return BLE_ATT_ERR_UNLIKELY;
    if (!s_rx_callback(bytes, length, s_status.session, s_rx_ctx)) {
        taskENTER_CRITICAL(&s_lock);
        s_status.rx_rejected++;
        taskEXIT_CRITICAL(&s_lock);
        /* A dropped command fragment must never join a later command. */
        clear_stream();
        terminate_connection(BLE_HS_ENOMEM);
        return BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return 0;
}

static const struct ble_gatt_svc_def s_services[] = {{
    .type = BLE_GATT_SVC_TYPE_PRIMARY,
    .uuid = &s_service_uuid.u,
    .characteristics = (struct ble_gatt_chr_def[]) {{
        .uuid = &s_rx_uuid.u, .access_cb = receive_value,
        .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_ENC | BLE_GATT_CHR_F_WRITE_AUTHEN,
        .min_key_size = 16,
    }, {
        .uuid = &s_tx_uuid.u, .access_cb = receive_value, .val_handle = &s_tx_handle,
        .flags = BLE_GATT_CHR_F_NOTIFY | BLE_GATT_CHR_F_NOTIFY_INDICATE_ENC | BLE_GATT_CHR_F_NOTIFY_INDICATE_AUTHEN,
        .min_key_size = 16,
    }, {0}},
}, {0}};

/* All notification submission runs on the host event queue. GAP disconnects
 * therefore cannot interleave a session check with a reused connection handle. */
static void tx_flush(struct ble_npl_event *event) {
    (void)event;
    unsigned sent = 0;
    while (sent < 3) {
        if (s_active_slot < 0) {
            uint8_t slot;
            if (xQueueReceive(s_tx_queue, &slot, 0) != pdTRUE) return;
            s_active_slot = slot;
            s_active_offset = 0;
            s_last_tx_progress_us = esp_timer_get_time();
        }
        tx_packet_t *packet = &s_tx_slots[s_active_slot];
        if (!ready(packet->session)) {
            release_slot((unsigned)s_active_slot);
            s_active_slot = -1;
            continue;
        }
        uint16_t mtu = ble_att_mtu(s_connection);
        if (mtu < 23) { clear_stream(); terminate_connection(BLE_HS_EBADDATA); return; }
        size_t length = packet->length - s_active_offset;
        if (length > (size_t)mtu - 3) length = mtu - 3;
        if (length > PASSPORT_BLE_MAX_RX) length = PASSPORT_BLE_MAX_RX;
        struct os_mbuf *buffer = ble_hs_mbuf_from_flat(packet->bytes + s_active_offset, length);
        int rc = buffer ? ble_gatts_notify_custom(s_connection, s_tx_handle, buffer) : BLE_HS_ENOMEM;
        /* notify_custom consumes the mbuf on both success and failure. */
        if (rc != 0) {
            if (esp_timer_get_time() - s_last_tx_progress_us >= TX_STALL_TIMEOUT_US) {
                clear_stream();
                terminate_connection(rc);
            } else ble_npl_callout_reset(&s_tx_retry, ble_npl_time_ms_to_ticks32(10));
            return;
        }
        s_active_offset += length;
        s_last_tx_progress_us = esp_timer_get_time();
        if (s_active_offset == packet->length) {
            release_slot((unsigned)s_active_slot);
            s_active_slot = -1;
        }
        sent++;
    }
    if (s_active_slot >= 0 || uxQueueMessagesWaiting(s_tx_queue))
        ble_npl_callout_reset(&s_tx_retry, ble_npl_time_ms_to_ticks32(2));
}

bool passport_ble_write_session(const uint8_t *data, size_t length, uint32_t session) {
    if (!data || !length || length > PASSPORT_BLE_MAX_WRITE) return false;
    taskENTER_CRITICAL(&s_lock);
    bool accepted = s_status.running && s_status.connected && s_status.secured &&
                    s_status.subscribed && s_status.session == session;
    taskEXIT_CRITICAL(&s_lock);
    if (!accepted || !s_tx_queue) return false;
    int available = acquire_slot();
    if (available >= 0) {
        uint8_t slot = (uint8_t)available;
        tx_packet_t *packet = &s_tx_slots[slot];
        packet->session = session;
        packet->length = (uint16_t)length;
        memcpy(packet->bytes, data, length);
        /* The queue carries an index into a fixed three-slot pool. No large
         * caller stack object, per-packet allocation, or unbounded heap use. */
        if (xQueueSend(s_tx_queue, &slot, 0) == pdTRUE) {
            ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &s_tx_event);
            return true;
        }
        release_slot(slot);
    }
    taskENTER_CRITICAL(&s_lock);
    s_status.tx_rejected++;
    s_status.last_error = BLE_HS_ENOMEM;
    taskEXIT_CRITICAL(&s_lock);
    return false;
}

bool passport_ble_write(const uint8_t *data, size_t length) {
    passport_ble_status_t status;
    passport_ble_get_status(&status);
    return passport_ble_write_session(data, length, status.session);
}

static int advertise(void) {
    if (s_connection != BLE_HS_CONN_HANDLE_NONE) return 0;
    struct ble_hs_adv_fields fields = {0};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = (ble_uuid128_t *)&s_service_uuid;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;
    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) return rc;
    struct ble_hs_adv_fields response = {0};
    response.name = (const uint8_t *)s_status.name;
    response.name_len = strlen(s_status.name);
    response.name_is_complete = 1;
    rc = ble_gap_adv_rsp_set_fields(&response);
    if (rc != 0) return rc;
    struct ble_gap_adv_params params = {0};
    params.conn_mode = BLE_GAP_CONN_MODE_UND;
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    params.itvl_min = pairing_open() ? 160 : 480;
    params.itvl_max = pairing_open() ? 240 : 640;
    rc = ble_gap_adv_start(s_address_type, NULL, BLE_HS_FOREVER, &params, gap_event, NULL);
    taskENTER_CRITICAL(&s_lock);
    s_status.advertising = rc == 0;
    s_status.last_error = rc;
    taskEXIT_CRITICAL(&s_lock);
    return rc;
}

static void control_event(struct ble_npl_event *event) {
    (void)event;
    taskENTER_CRITICAL(&s_lock);
    bool disconnect = s_disconnect_requested;
    s_disconnect_requested = false;
    taskEXIT_CRITICAL(&s_lock);
    if (disconnect) { clear_stream(); terminate_connection(0); return; }
    if (s_connection == BLE_HS_CONN_HANDLE_NONE) {
        ble_gap_adv_stop();
        advertise();
    }
}

bool passport_ble_open_pairing(uint32_t duration_ms) {
    if (duration_ms > 120000) duration_ms = 120000;
    taskENTER_CRITICAL(&s_lock);
    bool running = s_status.running;
    if (running) s_pair_deadline_us = duration_ms ? esp_timer_get_time() + (int64_t)duration_ms * 1000 : 0;
    taskEXIT_CRITICAL(&s_lock);
    if (running) ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &s_control_event);
    return running;
}

void passport_ble_disconnect(void) {
    taskENTER_CRITICAL(&s_lock);
    bool running = s_status.running;
    if (running) s_disconnect_requested = true;
    taskEXIT_CRITICAL(&s_lock);
    if (running) ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &s_control_event);
}

static int safe_store_write(int type, const union ble_store_value *value) {
    if ((type == BLE_STORE_OBJ_TYPE_OUR_SEC || type == BLE_STORE_OBJ_TYPE_PEER_SEC) &&
        value->sec.ltk_present &&
        (!value->sec.authenticated || !value->sec.sc || value->sec.key_size != 16)) {
        s_store_failed = true;
        taskENTER_CRITICAL(&s_lock);
        s_status.secured = false;
        s_status.last_error = BLE_HS_EAUTHEN;
        taskEXIT_CRITICAL(&s_lock);
        return BLE_HS_EAUTHEN;
    }
    int rc = s_original_store_write(type, value);
    if (rc != 0) {
        s_store_failed = true;
        taskENTER_CRITICAL(&s_lock);
        s_status.secured = false;
        s_status.last_error = rc;
        taskEXIT_CRITICAL(&s_lock);
    }
    return rc;
}

static int store_status(struct ble_store_status_event *event, void *unused) {
    (void)unused;
    /* FULL is a pessimistic preflight (it counts the current procedure). Let
     * the bounded store attempt the write; only actual OVERFLOW is rejected. */
    if (event->event_code == BLE_STORE_EVENT_FULL) return 0;
    /* Preserve existing bonds; do not use the example's round-robin deletion. */
    s_store_failed = true;
    set_error(BLE_HS_ENOMEM);
    return BLE_HS_ENOMEM;
}

static void watchdog(struct ble_npl_event *event) {
    (void)event;
    if (s_connection != BLE_HS_CONN_HANDLE_NONE && !s_status.secured &&
        (s_store_failed || esp_timer_get_time() >= s_security_deadline_us ||
         (s_status.pairing_result == PASSPORT_BLE_PAIR_WAITING && !pairing_open()))) {
        clear_pin(PASSPORT_BLE_PAIR_FAILED);
        terminate_connection(BLE_HS_ETIMEOUT);
    }
    if (s_clear_pin_pending && s_status.pairing_result != PASSPORT_BLE_PAIR_WAITING)
        s_clear_pin_pending = !s_pair_callback(false, 0, s_pair_ctx);
    ble_npl_callout_reset(&s_watchdog, ble_npl_time_ms_to_ticks32(500));
}

static int gap_event(struct ble_gap_event *event, void *unused) {
    (void)unused;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status != 0) { advertise(); return 0; }
        if (s_connection != BLE_HS_CONN_HANDLE_NONE) {
            ble_gap_terminate(event->connect.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
            return 0;
        }
        s_connection = event->connect.conn_handle;
        s_store_failed = false;
        taskENTER_CRITICAL(&s_lock);
        s_status.connected = true;
        s_status.advertising = false;
        s_status.secured = s_status.subscribed = false;
        s_status.mtu = 23;
        s_status.pairing_result = PASSPORT_BLE_PAIR_NONE;
        taskEXIT_CRITICAL(&s_lock);
        s_security_deadline_us = esp_timer_get_time() + UNAUTHENTICATED_TIMEOUT_US;
        if (!clear_stream()) {
            terminate_connection(BLE_HS_ENOMEM);
            return 0;
        }
        {
            int rc = ble_gap_security_initiate(s_connection);
            if (rc != 0 && rc != BLE_HS_EALREADY) terminate_connection(rc);
        }
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        if (event->disconnect.conn.conn_handle != s_connection) return 0;
        s_connection = BLE_HS_CONN_HANDLE_NONE;
        taskENTER_CRITICAL(&s_lock);
        s_status.connected = s_status.secured = s_status.subscribed = false;
        s_status.mtu = 23;
        taskEXIT_CRITICAL(&s_lock);
        clear_stream();
        clear_pin(PASSPORT_BLE_PAIR_DISCONNECTED);
        advertise();
        return 0;
    case BLE_GAP_EVENT_PASSKEY_ACTION:
        if (event->passkey.conn_handle != s_connection || !pairing_open() ||
            event->passkey.params.action != BLE_SM_IOACT_DISP) {
            clear_pin(PASSPORT_BLE_PAIR_FAILED);
            terminate_connection(BLE_HS_EAUTHEN);
            return 0;
        }
        {
            uint32_t random;
            do { random = esp_random(); } while (random >= UINT32_C(4294000000));
            struct ble_sm_io key = {.action = BLE_SM_IOACT_DISP, .passkey = random % 1000000};
            taskENTER_CRITICAL(&s_lock);
            s_status.pairing_result = PASSPORT_BLE_PAIR_WAITING;
            taskEXIT_CRITICAL(&s_lock);
            if (!s_pair_callback(true, key.passkey, s_pair_ctx)) {
                clear_pin(PASSPORT_BLE_PAIR_FAILED);
                terminate_connection(BLE_HS_ENOMEM);
                return 0;
            }
            s_security_deadline_us = esp_timer_get_time() + PASSKEY_TIMEOUT_US;
            int rc = ble_sm_inject_io(s_connection, &key);
            memset(&key, 0, sizeof(key));
            if (rc != 0) { clear_pin(PASSPORT_BLE_PAIR_FAILED); terminate_connection(rc); }
        }
        return 0;
    case BLE_GAP_EVENT_ENC_CHANGE:
        if (event->enc_change.conn_handle != s_connection) return 0;
        {
            struct ble_gap_conn_desc desc;
            bool secure = event->enc_change.conn_handle == s_connection && event->enc_change.status == 0 &&
                ble_gap_conn_find(s_connection, &desc) == 0 && desc.sec_state.encrypted &&
                desc.sec_state.authenticated && desc.sec_state.bonded && desc.sec_state.key_size == 16 &&
                !s_store_failed && has_secure_bond(&desc);
            taskENTER_CRITICAL(&s_lock);
            s_status.secured = secure;
            if (secure) s_pair_deadline_us = 0;
            taskEXIT_CRITICAL(&s_lock);
            clear_pin(secure ? PASSPORT_BLE_PAIR_SUCCESS : PASSPORT_BLE_PAIR_FAILED);
            if (!secure) terminate_connection(BLE_HS_EAUTHEN);
        }
        return 0;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.conn_handle == s_connection && event->subscribe.attr_handle == s_tx_handle) {
            taskENTER_CRITICAL(&s_lock);
            s_status.subscribed = event->subscribe.cur_notify != 0;
            taskEXIT_CRITICAL(&s_lock);
            if (!s_status.subscribed) clear_stream();
        }
        return 0;
    case BLE_GAP_EVENT_MTU:
        if (event->mtu.conn_handle == s_connection) {
            taskENTER_CRITICAL(&s_lock);
            s_status.mtu = event->mtu.value;
            taskEXIT_CRITICAL(&s_lock);
        }
        return 0;
    case BLE_GAP_EVENT_REPEAT_PAIRING:
        if (event->repeat_pairing.conn_handle != s_connection) return BLE_GAP_REPEAT_PAIRING_IGNORE;
        /* An unexpected replacement request is never permission to forget an
         * existing trusted peer. Recovery needs an explicit future USB action. */
        clear_pin(PASSPORT_BLE_PAIR_FAILED);
        terminate_connection(BLE_HS_EAUTHEN);
        return BLE_GAP_REPEAT_PAIRING_IGNORE;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        if (s_connection == BLE_HS_CONN_HANDLE_NONE) advertise();
        return 0;
    default:
        return 0;
    }
}

static void on_reset(int reason) {
    s_connection = BLE_HS_CONN_HANDLE_NONE;
    taskENTER_CRITICAL(&s_lock);
    s_status.synced = s_status.advertising = s_status.connected = s_status.secured = s_status.subscribed = false;
    s_status.last_error = reason;
    taskEXIT_CRITICAL(&s_lock);
    clear_stream();
    clear_pin(PASSPORT_BLE_PAIR_FAILED);
}

static void on_sync(void) {
    int rc = ble_hs_util_ensure_addr(0);
    if (rc == 0) rc = ble_hs_id_infer_auto(0, &s_address_type);
    uint32_t free_heap = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    taskENTER_CRITICAL(&s_lock);
    s_status.synced = rc == 0;
    s_status.heap_after_sync = free_heap;
    taskEXIT_CRITICAL(&s_lock);
    if (rc != 0) { set_error(rc); return; }
    advertise();
    ble_npl_callout_reset(&s_watchdog, ble_npl_time_ms_to_ticks32(500));
}

static void host_task(void *unused) {
    (void)unused;
    nimble_port_run();
    taskENTER_CRITICAL(&s_lock);
    s_status.running = false;
    taskEXIT_CRITICAL(&s_lock);
    nimble_port_freertos_deinit();
}

bool passport_ble_set_pairing_callback(passport_ble_pairing_callback_t callback, void *ctx) {
    if (s_initialized) return false;
    s_pair_callback = callback;
    s_pair_ctx = ctx;
    return callback != NULL;
}

bool passport_ble_start(passport_ble_rx_callback_t callback, void *ctx) {
    if (s_initialized || !callback || !s_pair_callback) return false;
#if !CONFIG_BT_NIMBLE_NVS_PERSIST || !CONFIG_BT_NIMBLE_SECURITY_ENABLE || !CONFIG_BT_NIMBLE_SM_SC || CONFIG_BT_NIMBLE_SM_SC_DEBUG_KEYS || CONFIG_BT_NIMBLE_LOG_LEVEL_DEBUG || CONFIG_BT_NIMBLE_HANDLE_REPEAT_PAIRING_DELETION
    ESP_LOGE(TAG, "BLE requires persistent authenticated SC bonding without debug keys or automatic bond deletion");
    return false;
#endif
    s_status.heap_before_start = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    esp_err_t error = nvs_flash_init();
    if (error != ESP_OK) { set_error(error); return false; } /* Never erase existing NVS. */
    uint8_t mac[6];
    error = esp_read_mac(mac, ESP_MAC_BT);
    if (error != ESP_OK) { set_error(error); return false; }
    snprintf(s_status.name, sizeof(s_status.name), "QingJian-%02X%02X%02X", mac[3], mac[4], mac[5]);
    s_rx_callback = callback;
    s_rx_ctx = ctx;
    s_tx_queue = xQueueCreate(PASSPORT_BLE_TX_QUEUE_DEPTH, sizeof(uint8_t));
    if (!s_tx_queue) { set_error(ESP_ERR_NO_MEM); return false; }
    error = nimble_port_init();
    if (error != ESP_OK) { vQueueDelete(s_tx_queue); s_tx_queue = NULL; set_error(error); return false; }
    s_initialized = true;
    ble_npl_event_init(&s_tx_event, tx_flush, NULL);
    ble_npl_event_init(&s_control_event, control_event, NULL);
    int rc = ble_npl_callout_init(&s_tx_retry, nimble_port_get_dflt_eventq(), tx_flush, NULL);
    bool retry_created = rc == 0;
    if (rc == 0) rc = ble_npl_callout_init(&s_watchdog, nimble_port_get_dflt_eventq(), watchdog, NULL);
    if (rc != 0) {
        if (retry_created) ble_npl_callout_deinit(&s_tx_retry);
        nimble_port_deinit();
        vQueueDelete(s_tx_queue);
        s_tx_queue = NULL;
        s_initialized = false;
        set_error(rc);
        return false;
    }
    ble_svc_gap_init();
    ble_svc_gatt_init();
    rc = ble_svc_gap_device_name_set(s_status.name);
    if (rc == 0) rc = ble_att_set_preferred_mtu(PREFERRED_MTU);
    if (rc == 0) rc = ble_gatts_count_cfg(s_services);
    if (rc == 0) rc = ble_gatts_add_svcs(s_services);
    if (rc != 0) {
        set_error(rc);
        ble_npl_callout_deinit(&s_watchdog);
        ble_npl_callout_deinit(&s_tx_retry);
        nimble_port_deinit();
        vQueueDelete(s_tx_queue);
        s_tx_queue = NULL;
        s_initialized = false;
        return false;
    }
    ble_hs_cfg.reset_cb = on_reset;
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.sm_io_cap = BLE_SM_IO_CAP_DISP_ONLY;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 1;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_sc_only = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    extern void ble_store_config_init(void);
    ble_store_config_init();
    s_original_store_write = ble_hs_cfg.store_write_cb;
    ble_hs_cfg.store_write_cb = safe_store_write;
    ble_hs_cfg.store_status_cb = store_status;
    s_status.mtu = 23;
    s_status.running = true;
    nimble_port_freertos_init(host_task);
    ESP_LOGI(TAG, "Secure single-peer NUS transport started; new pairing is closed");
    return true;
}
