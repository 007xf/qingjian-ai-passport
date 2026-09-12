#include "bsp_display.h"
#include "bsp_i2c.h"
#include "passport.h"
#include "passport_usb.h"
#include "esp_log.h"

void app_main(void) {
    if (bsp_i2c_init() != ESP_OK || bsp_display_init() != ESP_OK || !bsp_lvgl_init()) {
        ESP_LOGE("main", "Display initialization failed; stopping startup.");
        return;
    }
    if (!passport_start()) {
        ESP_LOGE("main", "Passport application failed to start.");
        return;
    }
    if (!passport_usb_start()) ESP_LOGW("main", "USB editing interface unavailable.");
}
