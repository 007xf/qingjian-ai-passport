#include "passport_usb.h"
#include "passport_ble.h"
#include "esp_mac.h"
#include <stdarg.h>
#include "passport.h"
#include "passport_wire.h"
#include "passport_avatar.h"
#include "passport_assets.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "cJSON.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha256.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Both links use the same command worker, but never share partial lines or
 * avatar transactions. Connection epochs prevent old BLE replies reaching a
 * newly connected central. Callbacks only enqueue bounded byte packets. */
#define BLE_RX_MAX 512
#define RESPONSE_MAX 2560

typedef struct {
    bool ble;
    uint32_t session;
    char line[1024];
    size_t line_used;
    bool discard;
    uint8_t upload[PASSPORT_AVATAR_BYTES];
    size_t upload_used;
    bool upload_active;
    char upload_hash[65];
    unsigned upload_stage;
} command_peer_t;
typedef struct {
    uint32_t session;
    uint16_t size;
    uint8_t data[BLE_RX_MAX];
} ble_packet_t;
static command_peer_t s_usb, s_bluetooth = {.ble=true};
static command_peer_t *s_peer = &s_usb;
static QueueHandle_t s_ble_packets;

static void output(const char *format, ...) {
    char text[RESPONSE_MAX];
    va_list args;
    va_start(args, format);
    int size = vsnprintf(text, sizeof(text), format, args);
    va_end(args);
    if (size < 0 || (size_t)size >= sizeof(text)) {
        strcpy(text, "@AP ERROR response_too_large\n");
        size = strlen(text);
    }
    if (s_peer->ble) {
        if (!passport_ble_write_session((const uint8_t *)text, size, s_peer->session)) {
            passport_ble_status_t ble;
            passport_ble_get_status(&ble);
            if (ble.connected && ble.session == s_peer->session) passport_ble_disconnect();
        }
    } else usb_serial_jtag_write_bytes(text, size, pdMS_TO_TICKS(1000));
}

static void reply(const char *name, bool ok)
{
    output("@AP %s_%s\n", name, ok ? "OK" : "ERROR");
}

static void status(void)
{
    passport_status_t s;
    passport_badge_t b;
    char hash[65];
    passport_get_status(&s);
    passport_get_badge(&b);
    passport_avatar_hash(hash);
    cJSON *o = cJSON_CreateObject();
    if (!o) { output("@AP ERROR memory\n"); return; }
    cJSON_AddNumberToObject(o, "protocol", 1);
    uint8_t mac[6]; char device_id[13];
    if (esp_efuse_mac_get_default(mac) == ESP_OK) {
        snprintf(device_id, sizeof(device_id), "%02X%02X%02X%02X%02X%02X", mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
        cJSON_AddStringToObject(o, "device_id", device_id);
    }
    cJSON_AddStringToObject(o, "transport", s_peer->ble ? "ble" : "usb");
    cJSON_AddBoolToObject(o, "avatar_upload_usb_only", true);
    passport_ble_status_t ble;
    passport_ble_get_status(&ble);
    cJSON_AddBoolToObject(o, "ble_running", ble.running && ble.synced);
    cJSON_AddBoolToObject(o, "ble_advertising", ble.advertising);
    cJSON_AddBoolToObject(o, "ble_connected", ble.connected);
    cJSON_AddBoolToObject(o, "ble_secured", ble.secured);
    cJSON_AddBoolToObject(o, "ble_pairing_open", ble.pairing_open);
    cJSON_AddStringToObject(o, "ble_name", ble.name);
    cJSON_AddNumberToObject(o, "ble_mtu", ble.mtu);
    cJSON_AddNumberToObject(o, "ble_last_error", ble.last_error);
    cJSON_AddNumberToObject(o, "ble_pairing_result", ble.pairing_result);
    cJSON_AddNumberToObject(o, "battery_soc", s.battery_soc);
    cJSON_AddNumberToObject(o, "battery_mv", s.battery_mv);
    cJSON_AddBoolToObject(o, "screen_on", s.screen_on);
    cJSON_AddBoolToObject(o, "time_synced", s.time_synced);
    cJSON_AddNumberToObject(o, "utc_ms", (double)s.utc_ms);
    cJSON_AddNumberToObject(o, "offset_min", s.offset_min);
    cJSON_AddStringToObject(o, "page", s.page);
    cJSON_AddNumberToObject(o, "free_heap", s.free_heap);
    cJSON_AddNumberToObject(o, "feature_revision", s.feature_revision);
    cJSON_AddNumberToObject(o, "threshold1", (double)s.threshold1);
    cJSON_AddNumberToObject(o, "threshold2", (double)s.threshold2);
    cJSON_AddBoolToObject(o, "feature_all", s.features_all);
    cJSON_AddNumberToObject(o, "feature_mask", s.custom_features);
    cJSON_AddNumberToObject(o, "supported_features", s.supported_features);
    cJSON_AddNumberToObject(o, "input_dropped", s.input_dropped);
    cJSON_AddBoolToObject(o, "save_busy", s.save_busy);
    cJSON_AddNumberToObject(o, "last_save_error", s.last_save_error);
    cJSON_AddStringToObject(o, "name", b.name);
    cJSON_AddStringToObject(o, "title", b.title);
    cJSON_AddStringToObject(o, "intro", b.intro);
    cJSON_AddStringToObject(o, "avatar_sha256", hash);
    cJSON_AddBoolToObject(o, "avatar_custom", passport_avatar_is_custom());
    cJSON_AddNumberToObject(o, "avatar_custom_stage", passport_avatar_custom_stage());
    // JSON numbers are exact throughout the supported uint53 range.
    cJSON_AddNumberToObject(o, "tokens", (double)b.tokens);
    cJSON_AddBoolToObject(o, "tokens_known", b.tokens_known);
    cJSON_AddBoolToObject(o, "tokens_stale", b.tokens_stale);
    cJSON_AddNumberToObject(o, "stage", b.stage);
    cJSON_AddBoolToObject(o, "codex_dashboard_supported", true);
    cJSON_AddNumberToObject(o, "codex_observed_utc_ms", (double)s.codex_observed_utc_ms);
    cJSON_AddBoolToObject(o, "codex_ready", s.codex_ready);
    cJSON_AddBoolToObject(o, "codex_stale", s.codex_stale);
    cJSON_AddBoolToObject(o, "cursor_quota_supported", true);
    cJSON_AddNumberToObject(o, "cursor_quota_observed_utc_ms", (double)s.cursor_quota_observed_utc_ms);
    cJSON_AddNumberToObject(o, "cursor_quota_reset_at_ms", (double)s.cursor_quota_reset_at_ms);
    cJSON_AddStringToObject(o, "cursor_quota_plan", s.cursor_quota_plan);
    cJSON_AddBoolToObject(o, "cursor_quota_ready", s.cursor_quota_ready);
    cJSON_AddBoolToObject(o, "cursor_quota_stale", s.cursor_quota_stale);
    const char *quota_keys[] = {"cursor_quota_cursor_used_percent", "cursor_quota_other_used_percent"};
    for (unsigned i = 0; i < 2; ++i) {
        if (s.cursor_quota_known[i]) cJSON_AddNumberToObject(o, quota_keys[i], s.cursor_quota_used_percent[i]);
        else cJSON_AddNullToObject(o, quota_keys[i]);
    }
    cJSON_AddNumberToObject(o, "dino_score", s.dino_score);
    cJSON_AddNumberToObject(o, "dino_best", s.dino_best);
    cJSON_AddNumberToObject(o, "dino_state", s.dino_state);
    cJSON_AddBoolToObject(o, "provider_dashboard_supported", true);
    passport_provider_snapshot_t providers[PASSPORT_PROVIDER_COUNT];
    passport_get_providers(providers);
    for (unsigned i = 0; i < PASSPORT_PROVIDER_COUNT; ++i) {
        char field[40];
        snprintf(field, sizeof(field), "%s_activity_update_id", passport_provider_name(i));
        cJSON_AddNumberToObject(o, field, (double)providers[i].update_id);
    }
    char *json = cJSON_PrintUnformatted(o);
    if (json) { output("@AP STATUS %s\n", json); cJSON_free(json); }
    else output("@AP ERROR memory\n");
    cJSON_Delete(o);
}

static void badge(const char *encoded)
{
    unsigned char decoded[512];
    size_t size = 0;
    bool ok = mbedtls_base64_decode(decoded, sizeof(decoded) - 1, &size,
                                    (const unsigned char *)encoded, strlen(encoded)) == 0;
    if (!ok || !ap_wire_flat_object(decoded, size)) { reply("BADGE", false); return; }
    decoded[size] = 0;
    const char *end = NULL;
    cJSON *json = cJSON_ParseWithOpts((char *)decoded, &end, true);
    if (!json || !cJSON_IsObject(json) || cJSON_GetArraySize(json) != 3) {
        cJSON_Delete(json); reply("BADGE", false); return;
    }
    cJSON *name = cJSON_GetObjectItemCaseSensitive(json, "name");
    cJSON *title = cJSON_GetObjectItemCaseSensitive(json, "title");
    cJSON *intro = cJSON_GetObjectItemCaseSensitive(json, "intro");
    passport_badge_t old;
    passport_get_badge(&old);
    ok = cJSON_IsString(name) && cJSON_IsString(title) && cJSON_IsString(intro) &&
         passport_set_badge(name->valuestring, title->valuestring, intro->valuestring,
                            old.tokens, old.tokens_known);
    cJSON_Delete(json);
    reply("BADGE", ok);
}

static void avatar(const char *args)
{
    if (!strcmp(args, "DEFAULT")) {
        s_peer->upload_active = false;
        reply("AVATAR_DEFAULT", passport_avatar_reset()); return;
    }
    if (!strncmp(args, "BEGIN ", 6)) {
        unsigned size = 0, stage = 0; char hash[65] = {0}; char extra;
        s_peer->upload_active = false;
        bool ok = sscanf(args + 6, "%u %64[0123456789abcdef] %u%c", &size, hash, &stage, &extra) == 3 &&
                  size == PASSPORT_AVATAR_BYTES && strlen(hash) == 64 && stage <= 2;
        if (ok) { s_peer->upload_used = 0; s_peer->upload_stage = stage; strcpy(s_peer->upload_hash, hash); s_peer->upload_active = true; }
        reply("AVATAR_BEGIN", ok);
    } else if (!strncmp(args, "DATA ", 5)) {
        char *end;
        errno = 0;
        unsigned long offset = strtoul(args + 5, &end, 10);
        unsigned char bytes[256]; size_t size = 0;
        bool ok = s_peer->upload_active && !errno && end != args + 5 && *end == ' ' && offset == s_peer->upload_used;
        if (ok) ok = mbedtls_base64_decode(bytes, sizeof(bytes), &size,
                          (unsigned char *)(end + 1), strlen(end + 1)) == 0 &&
                       size > 0 && s_peer->upload_used + size <= PASSPORT_AVATAR_BYTES;
        if (ok) { memcpy(s_peer->upload + s_peer->upload_used, bytes, size); s_peer->upload_used += size; }
        else s_peer->upload_active = false;
        reply("AVATAR_DATA", ok);
    } else if (!strcmp(args, "END")) {
        static const char hex[] = "0123456789abcdef";
        unsigned char digest[32]; char hash[65];
        bool ok = s_peer->upload_active && s_peer->upload_used == PASSPORT_AVATAR_BYTES;
        s_peer->upload_active = false;
        if (ok) {
            mbedtls_sha256(s_peer->upload, sizeof(s_peer->upload), digest, 0);
            for (int i = 0; i < 32; ++i) { hash[i*2] = hex[digest[i] >> 4]; hash[i*2+1] = hex[digest[i]&15]; }
            hash[64] = 0;
            ok = !strcmp(hash, s_peer->upload_hash) && passport_avatar_install(s_peer->upload, sizeof(s_peer->upload), s_peer->upload_stage);
        }
        reply("AVATAR_END", ok);
    } else { s_peer->upload_active = false; output("@AP ERROR invalid_avatar\n"); }
}

static bool json_integer(const cJSON *o, double maximum, int64_t *out)
{
    if (!cJSON_IsNumber(o) || !isfinite(o->valuedouble) || o->valuedouble < 0 ||
        o->valuedouble > maximum || floor(o->valuedouble) != o->valuedouble) return false;
    *out = (int64_t)o->valuedouble;
    return true;
}

static void codex(const char *encoded)
{
    unsigned char decoded[640]; size_t size = 0;
    bool ok = mbedtls_base64_decode(decoded, sizeof(decoded)-1, &size,
        (const unsigned char *)encoded, strlen(encoded)) == 0 && ap_wire_flat_object(decoded, size);
    if (!ok) { reply("CODEX", false); return; }
    decoded[size] = 0;
    cJSON *json = cJSON_ParseWithOpts((char *)decoded, NULL, true);
    passport_codex_snapshot_t snapshot = {0};
    cJSON *source = cJSON_GetObjectItemCaseSensitive(json, "source");
    ok = cJSON_IsObject(json) && cJSON_GetArraySize(json) == 9 && cJSON_IsString(source) &&
         strlen(source->valuestring) < sizeof(snapshot.source) &&
         json_integer(cJSON_GetObjectItemCaseSensitive(json, "observed_utc_ms"), 4102444800000.0, &snapshot.observed_utc_ms) &&
         json_integer(cJSON_GetObjectItemCaseSensitive(json, "expires_utc_ms"), 4102444980000.0, &snapshot.expires_utc_ms);
    if (ok) strcpy(snapshot.source, source->valuestring);
    for (unsigned i = 0; ok && i < 2; i++) {
        char key[32]; int64_t duration = 0;
        snprintf(key, sizeof(key), "w%u_used_percent", i+1);
        cJSON *used = cJSON_GetObjectItemCaseSensitive(json, key);
        ok = cJSON_IsNull(used) || (cJSON_IsNumber(used) && isfinite(used->valuedouble) &&
             used->valuedouble >= 0 && used->valuedouble <= 100);
        snapshot.window[i].used_known = cJSON_IsNumber(used);
        snapshot.window[i].used_percent = snapshot.window[i].used_known ? used->valuedouble : 0;
        snprintf(key, sizeof(key), "w%u_duration_min", i+1);
        cJSON *length = cJSON_GetObjectItemCaseSensitive(json, key);
        ok = ok && (cJSON_IsNull(length) || json_integer(length, 5256000, &duration));
        snapshot.window[i].duration_min = (int)duration;
        snprintf(key, sizeof(key), "w%u_reset_s", i+1);
        cJSON *reset = cJSON_GetObjectItemCaseSensitive(json, key);
        ok = ok && (cJSON_IsNull(reset) || json_integer(reset, 4102444800.0, &snapshot.window[i].reset_s));
    }
    cJSON_Delete(json);
    reply("CODEX", ok && passport_set_codex(&snapshot));
}

static void cursor(const char *encoded) {
    unsigned char decoded[513]; size_t size = 0;
    bool ok = mbedtls_base64_decode(decoded, sizeof(decoded)-1, &size,
        (const unsigned char *)encoded, strlen(encoded)) == 0 && ap_wire_flat_object(decoded, size);
    if (!ok) { reply("CURSOR", false); return; }
    decoded[size] = 0;
    cJSON *json = cJSON_ParseWithOpts((char *)decoded, NULL, true);
    passport_cursor_snapshot_t snapshot = {0};
    cJSON *source = cJSON_GetObjectItemCaseSensitive(json, "source");
    cJSON *plan = cJSON_GetObjectItemCaseSensitive(json, "plan");
    ok = cJSON_IsObject(json) && cJSON_GetArraySize(json) == 7 && cJSON_IsString(source) &&
        strlen(source->valuestring) < sizeof(snapshot.source) && cJSON_IsString(plan) &&
        strlen(plan->valuestring) < sizeof(snapshot.plan) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(json, "observed_utc_ms"), 4102444800000.0, &snapshot.observed_utc_ms) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(json, "expires_utc_ms"), 4102445100000.0, &snapshot.expires_utc_ms) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(json, "reset_at_ms"), 4102444800000.0, &snapshot.reset_at_ms);
    if (ok) {
        strcpy(snapshot.source, source->valuestring);
        strcpy(snapshot.plan, plan->valuestring);
    }
    const char *keys[] = {"cursor_used_percent", "other_used_percent"};
    for (unsigned i = 0; ok && i < 2; ++i) {
        cJSON *used = cJSON_GetObjectItemCaseSensitive(json, keys[i]);
        ok = cJSON_IsNull(used) || (cJSON_IsNumber(used) && isfinite(used->valuedouble) &&
                                  used->valuedouble >= 0 && used->valuedouble <= 100);
        snapshot.used_known[i] = cJSON_IsNumber(used);
        snapshot.used_percent[i] = snapshot.used_known[i] ? used->valuedouble : 0;
    }
    cJSON_Delete(json);
    reply("CURSOR", ok && passport_set_cursor(&snapshot));
}

static const char *json_text(const cJSON *object, const char *key) {
    cJSON *value = cJSON_GetObjectItemCaseSensitive(object, key);
    return cJSON_IsString(value) ? value->valuestring : NULL;
}

static void provider(const char *encoded) {
    unsigned char decoded[700]; size_t size = 0;
    bool ok = mbedtls_base64_decode(decoded, sizeof(decoded)-1, &size,
        (const unsigned char *)encoded, strlen(encoded)) == 0 && ap_wire_flat_object(decoded, size);
    if (!ok) { reply("PROVIDER", false); return; }
    decoded[size] = 0;
    cJSON *json = cJSON_ParseWithOpts((char *)decoded, NULL, true);
    passport_provider_snapshot_t snapshot;
    passport_provider_snapshot_init(&snapshot, PASSPORT_PROVIDER_CODEX);
    int64_t update = 0, metric = 0, active = 0;
    const char *model = json_text(json, "model");
    ok = cJSON_IsObject(json) && cJSON_GetArraySize(json) == 13 && model && strlen(model) < sizeof(snapshot.model) &&
        passport_provider_parse_name(json_text(json, "provider"), &snapshot.provider) &&
        passport_provider_parse_state(json_text(json, "state"), &snapshot.state) &&
        passport_provider_parse_metric_kind(json_text(json, "metric_kind"), &snapshot.metric_kind) &&
        passport_provider_parse_metric_status(json_text(json, "metric_status"), &snapshot.metric_status) &&
        passport_provider_parse_source(json_text(json, "source"), &snapshot.source) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(json, "update_id"), 9007199254740991.0, &update) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(json, "state_at_ms"), 4102444800000.0, &snapshot.state_at_ms) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(json, "state_until_ms"), 4102444800000.0, &snapshot.state_until_ms) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(json, "metric_at_ms"), 4102444800000.0, &snapshot.metric_at_ms) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(json, "last_activity_ms"), 4102444800000.0, &snapshot.last_activity_ms);
    cJSON *value = cJSON_GetObjectItemCaseSensitive(json, "metric_value");
    cJSON *sessions = cJSON_GetObjectItemCaseSensitive(json, "active_sessions");
    ok = ok && (cJSON_IsNull(value) || json_integer(value, 9007199254740991.0, &metric)) &&
        (cJSON_IsNull(sessions) || json_integer(sessions, INT32_MAX, &active));
    if (ok) {
        strcpy(snapshot.model, model);
        snapshot.update_id = (uint64_t)update;
        snapshot.metric_value = (uint64_t)metric;
        snapshot.metric_value_known = cJSON_IsNumber(value);
        snapshot.active_sessions = (int32_t)active;
        snapshot.active_sessions_known = cJSON_IsNumber(sessions);
    }
    cJSON_Delete(json);
    reply("PROVIDER", ok && passport_set_provider(&snapshot));
}

static void command(const char *line)
{
    if (!strncmp(line, "@AP PROVIDER ", 13)) { provider(line + 13); return; }
    if (!strcmp(line, "@AP PAIR")) { reply("PAIR", !s_peer->ble && passport_request_pairing()); return; }
    if (!strncmp(line, "@AP CODEX ", 10)) { codex(line + 10); return; }
    if (!strncmp(line, "@AP CURSOR ", 11)) { cursor(line + 11); return; }
    if (!strncmp(line, "@AP THRESHOLDS ", 15)) {
        char *end; errno = 0;
        uint64_t first = strtoull(line + 15, &end, 10);
        bool ok = line[15] >= '0' && line[15] <= '9' && !errno && *end == ' ';
        uint64_t final = 0;
        if (ok) {
            const char *p = end + 1;
            errno = 0; final = strtoull(p, &end, 10);
            ok = *p >= '0' && *p <= '9' && !errno && *end == 0 &&
                 first > 0 && first < final && final <= UINT64_C(9007199254740991);
        }
        reply("THRESHOLDS", ok && passport_set_thresholds(first, final)); return;
    }
    if (!strncmp(line, "@AP BADGE ", 10)) { badge(line + 10); return; }
    if (!strncmp(line, "@AP AVATAR ", 11)) {
        if (s_peer->ble) output("@AP ERROR avatar_requires_usb\n");
        else avatar(line + 11);
        return;
    }
    if (!strncmp(line, "@AP FEATURES ", 13)) {
        unsigned all, mask; char extra;
        bool ok = sscanf(line + 13, "%u %u%c", &all, &mask, &extra) == 2 && all <= 1 && mask <= 255 &&
                  passport_set_features(all != 0, (uint8_t)mask);
        reply("FEATURES", ok); return;
    }
    if (!strncmp(line, "@AP TOKENS ", 11)) {
        if (!strcmp(line + 11, "UNKNOWN")) {
            reply("TOKENS", passport_set_tokens(0, false)); return;
        }
        char *end; errno = 0;
        uint64_t count = strtoull(line + 11, &end, 10);
        bool ok = line[11] >= '0' && line[11] <= '9' && !errno && *end == 0 &&
                  count <= UINT64_C(9007199254740991) && passport_set_tokens(count, true);
        reply("TOKENS", ok); return;
    }
    ap_wire_command_t c;
    if (!ap_wire_parse(line, &c)) { output("@AP ERROR invalid_command\n"); return; }
    if (c.kind == AP_WIRE_TIME) reply("TIME", passport_set_time(c.utc_ms, c.offset_min));
    else if (c.kind == AP_WIRE_SCREEN) reply("SCREEN", passport_set_screen(c.screen_on));
    else status();
}

static void consume(command_peer_t *peer, const uint8_t *data, size_t size) {
    s_peer = peer;
    for (size_t i=0;i<size;i++) {
        unsigned char b = data[i];
        if (b == '\n') {
            if (peer->discard) { output("@AP ERROR invalid_line\n"); peer->upload_active = false; }
            else if (peer->line_used) {
                peer->line[peer->line_used] = 0;
                command(peer->line);
            }
            peer->line_used = 0;
            peer->discard = false;
        } else if (b == '\r') { /* CRLF */ }
        else if (b < 32 || b > 126 || peer->line_used >= sizeof(peer->line)-1) peer->discard = true;
        else if (!peer->discard) peer->line[peer->line_used++] = (char)b;
    }
}

static bool bluetooth_received(const uint8_t *data, size_t size, uint32_t session, void *ctx) {
    (void)ctx;
    if (size > BLE_RX_MAX || (size && !data) || !s_ble_packets) return false;
    ble_packet_t packet = {.session=session,.size=(uint16_t)size};
    if (size) memcpy(packet.data, data, size);
    return xQueueSend(s_ble_packets, &packet, 0) == pdTRUE;
}

static bool bluetooth_pairing(bool visible, uint32_t passkey, void *ctx) {
    (void)ctx;
    return passport_set_pairing_display(visible, passkey);
}

static void usb_task(void *arg) {
    (void)arg;
    for (;;) {
        unsigned char bytes[64];
        int n = usb_serial_jtag_read_bytes(bytes, sizeof(bytes), pdMS_TO_TICKS(2));
        if (n > 0) consume(&s_usb, bytes, n);
        ble_packet_t packet;
        for (unsigned i=0; i<8 && xQueueReceive(s_ble_packets, &packet, 0)==pdTRUE; i++) {
            if (packet.session != s_bluetooth.session || packet.size==0) {
                memset(&s_bluetooth, 0, sizeof(s_bluetooth));
                s_bluetooth.ble = true;
                s_bluetooth.session = packet.session;
            }
            if (packet.size) consume(&s_bluetooth, packet.data, packet.size);
        }
    }
}

bool passport_usb_start(void) {
    usb_serial_jtag_driver_config_t cfg = {.tx_buffer_size = 2048, .rx_buffer_size = 2048};
    if (!usb_serial_jtag_is_driver_installed() && usb_serial_jtag_driver_install(&cfg) != ESP_OK)
        return false;
    usb_serial_jtag_vfs_use_driver();
    s_ble_packets = xQueueCreate(8, sizeof(ble_packet_t));
    if (!s_ble_packets) return false;
    passport_ble_set_pairing_callback(bluetooth_pairing, NULL);
    passport_ble_start(bluetooth_received, NULL);
    /* Requests can arrive during startup. Start the command worker after
     * NimBLE initialization so an early USB PAIR cannot race a stopped host. */
    if (xTaskCreate(usb_task, "passport_links", 8192, NULL, 3, NULL) != pdPASS) {
        /* The live BLE callback still owns this queue. Keep it valid and
         * reject incoming traffic by filling/closing that BLE session. */
        passport_ble_disconnect();
        return false;
    }
    return true;
}
