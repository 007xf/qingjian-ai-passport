<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# 资源目录（Assets）

本目录集中存放可复用的资源（字库、图片、音乐等），按资源类型分子目录管理。每个资源放在其类型对应的子目录，并记录放置路径、命名方式、集成方式与来源/许可。二进制资源（字体、图片、音频）不属于纯 markdown 文档，请勿与文档混放。涉及版权/授权的资源需注明来源与许可。

## 字库（fonts）

可复用的字库文件与生成的字库源码放在 `fonts/`。

- 命名要能反映字族、字重、字级与格式。
- 记录来源、许可、字符范围、转换命令与目标放置路径。
- 添加字库前评估 Flash 与内部 RAM 影响；ESP32-C3 无 PSRAM。
- 不提交许可不允许分发的字库。

## 图片（images）

可复用的源图与生成的显示资产放在 `images/`。

- 使用描述性命名，并记录尺寸、像素格式、转换步骤与目标路径。
- 优先采用适合 240 × 320 RGB565 显示的格式，并纳入 Flash 与内部 RAM 考量。
- 许可允许时保留可编辑源文件，并记录来源与许可。
- 图片中不得包含设备二维码秘密、凭证或个人数据。

## 音乐与音效（music）

可复用的音乐与音效源码放在 `music/`。

- 记录来源、许可、采样率、位深、声道、转换命令与目标路径。
- 与当前 BSP 音频路径匹配时优先采用 16 kHz、16 位单声道 PCM。
- 嵌入音频前评估 Flash 与内部 RAM 成本；长录音应流式或分块。
- 无再分发许可不提交媒体文件。

## Passport 内置素材

`badge-layout.json` 是设备与 Mac 预览共用的布局契约，定义 240×320 徽章的像素位置、颜色、真实字库行高、对齐、截断和标题文字。修改后运行 `python3 tools/generate_badge_layout.py` 生成 `main/passport_layout.h`；静态检查会拒绝过期的头文件。Mac App 打包同一份 JSON，并使用相同的字体轮廓。

青子围巾、红裙、红发三张 PNG 保存在 `images/`，采用全身自然站姿和已选定的较早圆眼脸。`main/passport_avatar_data.c` 由 `tools/generate_builtin_avatars.py` 从未改动的原图直接转换为原生 160×160 RGB565 显示像素。内置图直接从 Flash 按 1 倍显示，自定义上传仍兼容 128×128 编解码；[素材来源](../docs/development/passport-os/README.zh_CN.md#图像与来源)已记录。

`main/passport_font_14.c` 与 `main/passport_font_20.c` 使用 Noto Sans SC 字重400、2bpp，包含 GB2312 与可打印 ASCII，共7,541字符；字符清单和 OFL 许可证位于 `fonts/`。
