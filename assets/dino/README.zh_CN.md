<p align="right"><a href="README.md">English</a> · <strong>简体中文</strong></p>

# Chromium 经典小恐龙素材

`chromium/` 中未经修改的 1× 图集和参考源码来自 Chromium `98.0.4758.55` 标签。上游 BSD 许可保存在 `chromium/LICENSE`；分发包含这些素材的二进制时，应同时附上该文件。本项目不代表 Chromium 或 Google 的认可。

- [原始精灵图集](https://chromium.googlesource.com/chromium/src/+/refs/tags/98.0.4758.55/components/neterror/resources/images/default_100_percent/offline/100-offline-sprite.png)
- [精灵坐标](https://chromium.googlesource.com/chromium/src/+/refs/tags/98.0.4758.55/components/neterror/resources/offline-sprite-definitions.js)
- [动画与绘制定义](https://chromium.googlesource.com/chromium/src/+/refs/tags/98.0.4758.55/components/neterror/resources/offline.js)
- [BSD 许可](https://chromium.googlesource.com/chromium/src/+/refs/tags/98.0.4758.55/LICENSE)

原图 SHA-256 为 `04d05978fdb111358073ab0524e5c1fafc0826615c206987618416b8bd8a4747`。`tools/generate_dino_sprites.py` 校验该哈希，并生成 `main/` 下的精灵编号、像素碰撞掩码与 LVGL 图片头文件。`--check` 只检查可复现性，不改文件；`--preview-dir` 可导出精确像素的夜间预览。转换器需要 Pillow。

原图 RGB 通道反色，alpha 原样保留：白色背景边缘变为不透明黑色，灰色身体变为浅灰色。灰度帧使用 AL88，大仙人掌底边的微弱颜色用 ARGB8888 保留。图片按 4 字节对齐后放在 Flash 中，不缩放，不创建可变画布，也不分配运行时整张图片解码缓冲。当前 LVGL 配置支持这两种格式，行对齐为 1，图片缓存关闭。

直立帧保持 44×47。Chromium 将下蹲逻辑高度定义为 25，但实际绘制带上方空白的 59×47 原图格；这里保留完整原图格，避免裁掉像素。跑步、下蹲和翼龙的原始动画分别为每秒 12/8/6 帧。屏幕绘制限制为每秒最多 25 次，不承诺浏览器的显示帧率。

216×142 的游戏区域采用 132 像素地面基线、每秒 140–190 像素移动速度及有补帧上限的 60Hz 模拟。碰撞采用 Chromium 98 的固定身体框，避免振翅尖端对下蹲产生误判；生成的像素掩码仅保留用于素材校验。独立宿主测试覆盖原图一致性、颜色与透明度、掩码、动画选择、移动、起跳时机、暂停与重开、按键释放、最高分保存信号和时间跳变。
