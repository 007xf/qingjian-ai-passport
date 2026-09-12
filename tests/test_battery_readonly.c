/* Execute the production driver against a register-level I2C fake.
 * No USB or ESP-IDF runtime is used. Include the implementation so each
 * independent scenario can reset its private attachment state. */
#include <assert.h>
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
    write_calls++;
    registers[out[0]] = out[1];
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

int main(void)
{
    test_preserves_existing_profile();
    test_inactive_and_missing_profile();
    test_transport_failures_and_retry();
    test_soc_boundaries_and_read_errors();
    test_readonly_rejects_later_gauge_state_loss();
    puts("Battery read-only driver host tests: PASS");
    return 0;
}
