[English](RELEASE-VALIDATION.md) · **简体中文**

# 发行验证 — 1.4.0，Mac 构建 12

- Build：PASS。ESP-IDF 5.5.3 固件构建与保护区布局检查通过。应用段 2,336,816 字节，低于 3 MB；完整合并镜像 2,402,352 字节。Mac 原生编译与 App 签名完整性检查通过。
- Host tests：PASS。366 项 Python 测试覆盖桥接、后台独占连接与重试、独立 Token 周期、Cursor 整周期汇总、过期缓存及既有接入；另有原生配置、连接路由、额度时间与显示边界，以及 C 模型、协议、游戏与布局测试。
- Device tests：已测范围 PASS。1.4.0 应用固件已写入开发工牌，电量读数恢复，已有资料、功能选择、进化门槛与 Dino 最高分保留。最终 Mac 构建已通过加密蓝牙连接并读回电量及完整成长合计；用户确认新版蓝牙连接验证通过。
- 后台运行：本轮更新中已观察到退出编辑器后继续同步，也观察到工牌离线时后台释放闲置休眠请求、继续等待重连。这些检查与用户最后确认蓝牙分别记录，不代表长时间无线距离或耗电测试。
- 迁移测试：已测范围 PASS。本版 1.4.0 打包 App 在开发 Mac 上通过更换路径、空白用户目录及最小系统 PATH、内置依赖导入、9 个后台模块与源码对应、隐私检查和签名完整性验证；不能代替在另一台 Mac 上安装的实测。构建目标为 Apple 芯片/macOS 14+，更新后可能需要按系统正常流程重新授权蓝牙或后台项目。
- Unverified：未在另一台 Mac、所有较旧受支持 macOS 或所有第三方账户逐一实测；未完成长时间内存/耗电对照、实际续航提升测量、手动或合盖睡眠期间持续连接验收。开发主机为 macOS 27；Intel 与 iPhone 不支持。没有 Developer ID 签名及 Apple 公证。

## 文件身份

- 仅应用段：`qingjian-1.4.0-app.bin`，地址 `0x10000`，2,336,816 字节；SHA-256：`e50a93bb54d9cb33c8bdf383ff4ed15a5d8aec3619543b97db8a86531f6997a1`。
- 完整合并镜像：`qingjian-1.4.0-full.bin`，地址 `0x0`，2,402,352 字节；SHA-256：`0b09d4bd4782cd8c5192258156d09aab9ae14abd7d84ba55e419976cd7186dc2`。
- Mac 包：`qingjian-1.4.0-macos-arm64.zip`，48,980,555 字节；SHA-256：`d6892fc2e90bb11abb68d290e4ce6607fa0a8aa96c1324f2496558314cd01890`。请与同版 `SHA256SUMS.txt` 核对。

公开固件来自源码构建，不是设备 Flash 转储。身份区、恢复数据、作者缓存、登录凭据、私人日志与备份均排除。文件见 [GitHub 发行页](https://github.com/007xf/qingjian-ai-passport/releases/tag/v1.4.0)，简明记录见 `release-validation.json`。[社区玩法 328](https://ai-passport.folotoy.cn/plays/328/) 的此前已审核版本已经公开；新版本有独立审核状态，得到确认前不能写成已经审核通过。

刷固件或替换 App 前，必须先关闭后台同步，再退出编辑器。只退出编辑器不再释放设备连接。运行边界见[后台说明](qingjian-background-sync.zh_CN.md)与[免责声明](DISCLAIMER.zh_CN.md)。
