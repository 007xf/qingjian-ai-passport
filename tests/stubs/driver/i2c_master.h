#pragma once

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

typedef void *i2c_master_dev_handle_t;
typedef void *i2c_master_bus_handle_t;

#define I2C_ADDR_BIT_LEN_7 0

typedef struct {
    int dev_addr_length;
    uint16_t device_address;
    uint32_t scl_speed_hz;
} i2c_device_config_t;

esp_err_t i2c_master_bus_add_device(i2c_master_bus_handle_t bus,
                                  const i2c_device_config_t *config,
                                  i2c_master_dev_handle_t *device);
esp_err_t i2c_master_bus_rm_device(i2c_master_dev_handle_t device);
esp_err_t i2c_master_transmit_receive(i2c_master_dev_handle_t device,
                                    const uint8_t *out, size_t out_size,
                                    uint8_t *in, size_t in_size, int timeout_ms);
esp_err_t i2c_master_transmit(i2c_master_dev_handle_t device,
                            const uint8_t *out, size_t out_size, int timeout_ms);
