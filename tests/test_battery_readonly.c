/* Execute the production driver against a register-level I2C fake.
 * No USB or ESP-IDF runtime is used. Include the implementation so each
 * independent scenario can reset its private attachment state. */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "bsp_i2c.h"
#include "freertos/task.h"

static uint8_t registers[256];
static int fail_read_register;
static esp_err_t bus_result;
static esp_err_t add_result;
static unsigned bus_calls, add_calls, remove_calls, read_calls, write_calls;
static unsigned delay_calls;
static uint8_t write_registers[256], write_values[256];
static bool corrupt_profile_after_wake;
static int fail_write_call;
static int fake_bus, fake_device;

const char *esp_err_to_name(esp_err_t error)
{
    (void)error;
    return "fake error";
}

esp_err_t bsp_i2c_init(void)
{
    bus_calls++;
    return bus_result;
}

i2c_master_bus_handle_t bsp_i2c_bus(void)
{
    return &fake_bus;
}

esp_err_t i2c_master_bus_add_device(i2c_master_bus_handle_t bus,
                                  const i2c_device_config_t *config,
                                  i2c_master_dev_handle_t *device)
{
    assert(bus == &fake_bus);
    assert(config->device_address == 0x63);
    assert(config->dev_addr_length == I2C_ADDR_BIT_LEN_7);
    assert(config->scl_speed_hz == 100000);
    add_calls++;
    if (add_result == ESP_OK) *device = &fake_device;
    return add_result;
}

esp_err_t i2c_master_bus_rm_device(i2c_master_dev_handle_t device)
{
    assert(device == &fake_device);
    remove_calls++;
    return ESP_OK;
}

esp_err_t i2c_master_transmit_receive(i2c_master_dev_handle_t device,
                                    const uint8_t *out, size_t out_size,
                                    uint8_t *in, size_t in_size, int timeout_ms)
{
    assert(device == &fake_device);
    assert(out_size == 1);
    assert(timeout_ms == 100);
    assert((size_t)out[0] + in_size <= sizeof(registers));
    read_calls++;
    if (out[0] == fail_read_register) return ESP_FAIL;
    memcpy(in, registers + out[0], in_size);
    return ESP_OK;
}

esp_err_t i2c_master_transmit(i2c_master_dev_handle_t device,
                            const uint8_t *out, size_t out_size, int timeout_ms)
{
    assert(device == &fake_device);
    assert(out_size == 2);
    assert(timeout_ms == 100);
    assert(write_calls < sizeof(write_registers));
    write_registers[write_calls] = out[0];
    write_values[write_calls] = out[1];
    write_calls++;
    if ((int)write_calls == fail_write_call) return ESP_FAIL;
    registers[out[0]] = out[1];
    if (corrupt_profile_after_wake && out[0] == 0x08 && out[1] == 0)
        registers[0x10] ^= 1;
    return ESP_OK;
}

void vTaskDelay(TickType_t ticks)
{
    (void)ticks;
    delay_calls++;
}

#include "../components/bsp/src/bsp_battery.c"

static void reset_gauge(void)
{
    s_dev = NULL;
    s_readonly = false;
    s_profile_verified = false;
    s_blank_init_failed = false;
    s_diagnostics = (bsp_battery_diagnostics_t){.version=-1, .mode=-1, .profile_flag=-1, .profile_matches=-1, .profile_blank=-1};
    memset(registers, 0, sizeof(registers));
    /* Deliberately not the firmware's built-in cell profile. */
    memset(registers + 0x10, 0xA5, 80);
    registers[0x00] = 0x6F;
    registers[0x08] = 0x00;
    registers[0x0B] = 0x85; /* update flag plus existing alert threshold */
    registers[0x04] = 62;
    registers[0x05] = 128;
    registers[0x02] = 0x2E;
    registers[0x03] = 0xE0; /* 12000 * 312.5 uV = 3750 mV */
    fail_read_register = -1;
    bus_result = add_result = ESP_OK;
    bus_calls = add_calls = remove_calls = read_calls = write_calls = 0;
    delay_calls = 0;
    corrupt_profile_after_wake = false;
    fail_write_call = 0;
}

static void assert_no_mutation(const uint8_t before[256])
{
    assert(write_calls == 0);
    assert(delay_calls == 0);
    assert(memcmp(registers, before, sizeof(registers)) == 0);
}

static void test_preserves_existing_profile(void)
{
    reset_gauge();
    uint8_t before[256];
    memcpy(before, registers, sizeof(before));
    assert(bsp_battery_init_readonly() == ESP_OK);
    assert(bsp_battery_soc() == 62);
    assert(bsp_battery_mv() == 3750);
    assert(bsp_battery_init_readonly() == ESP_OK);
    assert(bus_calls == 1 && add_calls == 1 && remove_calls == 0);
    assert_no_mutation(before);
}

static void test_inactive_and_missing_profile(void)
{
    const uint8_t modes[] = { 0x30, 0xF0 };
    for (size_t i = 0; i < sizeof(modes); i++) {
        reset_gauge();
        registers[0x08] = modes[i];
        uint8_t before[256];
        memcpy(before, registers, sizeof(before));
        assert(bsp_battery_init_readonly() == ESP_ERR_INVALID_STATE);
        assert(remove_calls == 1 && s_dev == NULL);
        assert(bsp_battery_soc() == -1 && bsp_battery_mv() == -1);
        assert_no_mutation(before);
    }
    reset_gauge();
    registers[0x0B] = 5;
    uint8_t before[256];
    memcpy(before, registers, sizeof(before));
    assert(bsp_battery_init_readonly() == ESP_ERR_INVALID_STATE);
    assert(remove_calls == 1 && s_dev == NULL);
    assert_no_mutation(before);
}

static void test_transport_failures_and_retry(void)
{
    reset_gauge();
    bus_result = ESP_ERR_TIMEOUT;
    assert(bsp_battery_init_readonly() == ESP_ERR_TIMEOUT);
    assert(add_calls == 0 && write_calls == 0 && s_dev == NULL);

    reset_gauge();
    add_result = ESP_ERR_TIMEOUT;
    assert(bsp_battery_init_readonly() == ESP_ERR_TIMEOUT);
    assert(remove_calls == 0 && read_calls == 0 && write_calls == 0);

    const int failing_registers[] = { 0x00, 0x08, 0x0B };
    for (size_t i = 0; i < sizeof(failing_registers) / sizeof(failing_registers[0]); i++) {
        reset_gauge();
        fail_read_register = failing_registers[i];
        uint8_t before[256];
        memcpy(before, registers, sizeof(before));
        esp_err_t expected = fail_read_register == 0 ? ESP_ERR_NOT_FOUND : ESP_FAIL;
        assert(bsp_battery_init_readonly() == expected);
        assert(remove_calls == 1 && s_dev == NULL);
        assert(bsp_battery_soc() == -1);
        fail_read_register = -1;
        assert(bsp_battery_init_readonly() == ESP_OK);
        assert(add_calls == 2 && bsp_battery_soc() == 62);
        assert_no_mutation(before);
    }
}

static void test_soc_boundaries_and_read_errors(void)
{
    reset_gauge();
    assert(bsp_battery_init_readonly() == ESP_OK);
    registers[0x04] = 0;
    assert(bsp_battery_soc() == 0);
    registers[0x04] = 100;
    assert(bsp_battery_soc() == 100);
    registers[0x04] = 101;
    assert(bsp_battery_soc() == -1);
    registers[0x04] = 255;
    assert(bsp_battery_soc() == -1);
    registers[0x04] = 50;
    fail_read_register = 0x04;
    assert(bsp_battery_soc() == -1);
    fail_read_register = 0x02;
    assert(bsp_battery_mv() == -1);
    fail_read_register = -1;
    assert(bsp_battery_soc() == 50);
    assert(write_calls == 0 && delay_calls == 0);
}

static void test_readonly_rejects_later_gauge_state_loss(void)
{
    reset_gauge();
    assert(bsp_battery_init_readonly() == ESP_OK);
    assert(bsp_battery_soc() == 62);
    registers[0x08] = 0xF0;
    assert(bsp_battery_soc() == -1); /* An in-range stored SOC is not live. */
    registers[0x08] = 0x00;
    registers[0x0B] = 5;
    assert(bsp_battery_soc() == -1);
    registers[0x0B] = 0x85;
    fail_read_register = 0x08;
    assert(bsp_battery_soc() == -1);
    fail_read_register = 0x0B;
    assert(bsp_battery_soc() == -1);
    fail_read_register = -1;
    assert(bsp_battery_soc() == 62);
    assert(write_calls == 0 && delay_calls == 0);
}

static void test_sampling_recovers_boot_failure_without_writes(void)
{
    reset_gauge();
    uint8_t before[256];
    memcpy(before, registers, sizeof(before));
    fail_read_register = 0;
    int soc = 99, mv = 9999;
    assert(bsp_battery_sample_readonly(&soc, &mv) == ESP_ERR_NOT_FOUND);
    assert(soc == -1 && mv == -1);
    bsp_battery_diagnostics_t diagnostics;
    bsp_battery_get_diagnostics(&diagnostics);
    assert(!diagnostics.attached && diagnostics.mode == -1 && diagnostics.profile_flag == -1);
    /* Same operation as the worker's next scheduled sample; no reboot needed. */
    fail_read_register = -1;
    assert(bsp_battery_sample_readonly(&soc, &mv) == ESP_OK);
    assert(soc == 62 && mv == 3750 && add_calls == 2);
    bsp_battery_get_diagnostics(&diagnostics);
    assert(diagnostics.attached && diagnostics.version == 0x6F);
    assert(diagnostics.mode == 0 && diagnostics.profile_flag == 1 && diagnostics.error == ESP_OK);
    assert_no_mutation(before);
}

static void test_sampling_distinguishes_sleep_and_missing_profile(void)
{
    reset_gauge();
    registers[0x08] = 0xF0;
    uint8_t before[256];
    memcpy(before, registers, sizeof(before));
    int soc, mv;
    assert(bsp_battery_sample_readonly(&soc, &mv) == ESP_ERR_INVALID_STATE);
    bsp_battery_diagnostics_t diagnostics;
    bsp_battery_get_diagnostics(&diagnostics);
    assert(soc == -1 && mv == -1 && !diagnostics.attached);
    assert(diagnostics.mode == 0xF0 && diagnostics.profile_flag == 1);
    assert_no_mutation(before);
    registers[0x08] = 0;
    registers[0x0B] = 5;
    assert(bsp_battery_sample_readonly(&soc, &mv) == ESP_ERR_INVALID_STATE);
    bsp_battery_get_diagnostics(&diagnostics);
    assert(diagnostics.mode == 0 && diagnostics.profile_flag == 0);
    registers[0x0B] = 0x85;
    assert(bsp_battery_sample_readonly(&soc, &mv) == ESP_OK);
    assert(soc == 62 && mv == 3750 && write_calls == 0 && delay_calls == 0);
}

static void known_reset_profile(void)
{
    reset_gauge();
    registers[0x00] = 0xA0;
    registers[0x08] = 0xF0;
    registers[0x0B] = 0x14; /* Manufacturer's reset defaults, no host marker. */
    memcpy(registers + 0x10, s_battery_profile, sizeof(s_battery_profile));
}

static void test_known_profile_wakes_without_profile_or_alert_writes(void)
{
    known_reset_profile();
    uint8_t before[256];
    memcpy(before, registers, sizeof(before));
    int soc, mv;
    assert(bsp_battery_sample_preserving_profile(&soc, &mv) == ESP_OK);
    assert(soc == 62 && mv == 3750);
    assert(write_calls == 2 && delay_calls == 2);
    assert(write_registers[0] == 0x08 && write_values[0] == 0x30);
    assert(write_registers[1] == 0x08 && write_values[1] == 0x00);
    before[0x08] = 0;
    assert(memcmp(before, registers, sizeof(before)) == 0);
    bsp_battery_diagnostics_t diagnostics;
    bsp_battery_get_diagnostics(&diagnostics);
    assert(diagnostics.profile_matches == 1 && diagnostics.profile_fingerprint != 0);
    assert(diagnostics.profile_flag == 0 && diagnostics.mode == 0 && diagnostics.wake_count == 1);
    assert(bsp_battery_sample_preserving_profile(&soc, &mv) == ESP_OK);
    assert(write_calls == 2 && delay_calls == 2); /* No repeated restart once active. */
    registers[0x02] = registers[0x03] = 0;
    assert(bsp_battery_sample_preserving_profile(&soc, &mv) == ESP_FAIL);
    assert(soc == -1 && mv == -1); /* Reset ADC defaults are not measured 0%. */
}

static void test_unknown_profile_or_register_state_never_wakes(void)
{
    for (unsigned scenario = 0; scenario < 4; ++scenario) {
        known_reset_profile();
        if (scenario == 0) registers[0x5F] ^= 1; /* Check every byte, including the last. */
        if (scenario == 1) fail_read_register = 0x20;
        if (scenario == 2) registers[0x00] = 0xA1;
        if (scenario == 3) registers[0x08] = 0x10;
        uint8_t before[256];
        memcpy(before, registers, sizeof(before));
        int soc, mv;
        assert(bsp_battery_sample_preserving_profile(&soc, &mv) != ESP_OK);
        assert(soc == -1 && mv == -1);
        assert_no_mutation(before);
    }
}

static void test_wake_requires_readback_and_preserves_flagged_foreign_profile(void)
{
    for (unsigned scenario = 0; scenario < 2; ++scenario) {
        known_reset_profile();
        if (scenario == 0) corrupt_profile_after_wake = true;
        if (scenario == 1) fail_write_call = 2;
        int soc, mv;
        assert(bsp_battery_sample_preserving_profile(&soc, &mv) == ESP_FAIL);
        assert(soc == -1 && mv == -1 && !s_dev);
        for (unsigned i = 0; i < write_calls; ++i) assert(write_registers[i] == 0x08);
    }
    reset_gauge(); /* Existing active, flagged foreign profile remains read-only. */
    uint8_t before[256];
    memcpy(before, registers, sizeof(before));
    int soc, mv;
    assert(bsp_battery_sample_preserving_profile(&soc, &mv) == ESP_OK);
    assert(soc == 62 && mv == 3750);
    assert_no_mutation(before);
}

static void blank_reset_profile(void)
{
    known_reset_profile();
    memset(registers + 0x10, 0, 80);
}

static void test_verified_blank_bank_initializes_once(void)
{
    blank_reset_profile();
    uint8_t before[256];
    memcpy(before, registers, sizeof(before));
    int soc, mv;
    assert(bsp_battery_sample_preserving_profile(&soc, &mv) == ESP_OK);
    assert(soc == 62 && mv == 3750);
    assert(write_calls == 85);
    unsigned profile_writes = 0, alert_writes = 0;
    for (unsigned i = 0; i < write_calls; ++i) {
        if (write_registers[i] >= 0x10 && write_registers[i] <= 0x5F) {
            assert(write_registers[i] == 0x10 + profile_writes);
            assert(write_values[i] == s_battery_profile[profile_writes]);
            profile_writes++;
        } else if (write_registers[i] == 0x0B) {
            alert_writes++;
            assert(write_values[i] == 0x94); /* Preserve prior low seven alert bits. */
        } else assert(write_registers[i] == 0x08);
    }
    assert(profile_writes == 80 && alert_writes == 1);
    before[0x08] = 0;
    before[0x0B] |= 0x80;
    memcpy(before + 0x10, s_battery_profile, 80);
    assert(memcmp(registers, before, sizeof(before)) == 0);
    bsp_battery_diagnostics_t diagnostics;
    bsp_battery_get_diagnostics(&diagnostics);
    assert(diagnostics.profile_init_count == 1 && diagnostics.wake_count == 1);
    assert(diagnostics.profile_matches == 1 && diagnostics.profile_blank == 0);
    assert(diagnostics.profile_fingerprint == UINT32_C(3969294757));
    for (unsigned i = 0; i < 5; ++i)
        assert(bsp_battery_sample_preserving_profile(&soc, &mv) == ESP_OK);
    assert(write_calls == 85);
}

static void test_blank_initialization_requires_all_gates(void)
{
    for (unsigned scenario = 0; scenario < 6; ++scenario) {
        blank_reset_profile();
        if (scenario == 0) registers[0x00] = 0xA1;
        if (scenario == 1) registers[0x08] = 0x00;
        if (scenario == 2) registers[0x0B] |= 0x80;
        if (scenario == 3) registers[0x10] = 1;
        if (scenario == 4) registers[0x5F] = 1;
        if (scenario == 5) fail_read_register = 0x5F;
        uint8_t before[256];
        memcpy(before, registers, sizeof(before));
        int soc, mv;
        assert(bsp_battery_sample_preserving_profile(&soc, &mv) != ESP_OK);
        assert(soc == -1 && mv == -1 && s_diagnostics.profile_init_count == 0);
        assert_no_mutation(before);
    }
    blank_reset_profile();
    int soc, mv;
    assert(bsp_battery_sample_readonly(&soc, &mv) == ESP_ERR_INVALID_STATE);
    assert(write_calls == 0); /* Explicit read-only API never opts into initialization. */
}

static void test_failed_blank_initialization_never_rewrites_in_loop(void)
{
    const int failing_writes[] = {1, 4}; /* Still blank, then a partial profile. */
    for (unsigned scenario = 0; scenario < 2; ++scenario) {
        blank_reset_profile();
        fail_write_call = failing_writes[scenario];
        int soc, mv;
        assert(bsp_battery_sample_preserving_profile(&soc, &mv) == ESP_FAIL);
        assert(soc == -1 && mv == -1 && s_diagnostics.profile_init_count == 1);
        const unsigned writes_after_failure = write_calls;
        uint8_t after_failure[256];
        memcpy(after_failure, registers, sizeof(after_failure));
        fail_write_call = 0;
        for (unsigned retry = 0; retry < 10; ++retry)
            assert(bsp_battery_sample_preserving_profile(&soc, &mv) != ESP_OK);
        assert(write_calls == writes_after_failure);
        assert(memcmp(registers, after_failure, sizeof(registers)) == 0);
    }
    blank_reset_profile();
    corrupt_profile_after_wake = true;
    int soc, mv;
    assert(bsp_battery_sample_preserving_profile(&soc, &mv) == ESP_FAIL);
    const unsigned writes = write_calls;
    assert(registers[0x08] == 0 && (registers[0x0B] & 0x80));
    for (unsigned retry = 0; retry < 3; ++retry) {
        assert(bsp_battery_sample_preserving_profile(&soc, &mv) != ESP_OK);
        assert(soc == -1 && mv == -1);
    }
    assert(write_calls == writes); /* Set flag cannot hide failed profile readback. */

}

int main(void)
{
    test_preserves_existing_profile();
    test_verified_blank_bank_initializes_once();
    test_blank_initialization_requires_all_gates();
    test_failed_blank_initialization_never_rewrites_in_loop();
    test_known_profile_wakes_without_profile_or_alert_writes();
    test_unknown_profile_or_register_state_never_wakes();
    test_wake_requires_readback_and_preserves_flagged_foreign_profile();
    test_sampling_recovers_boot_failure_without_writes();
    test_sampling_distinguishes_sleep_and_missing_profile();
    test_inactive_and_missing_profile();
    test_transport_failures_and_retry();
    test_soc_boundaries_and_read_errors();
    test_readonly_rejects_later_gauge_state_loss();
    puts("Battery preserved-profile and guarded recovery host tests: PASS");
    return 0;
}
