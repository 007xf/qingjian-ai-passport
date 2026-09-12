[English](RELEASE-VALIDATION.md) · **简体中文**

# 发行验证 — 1.3.1，Mac 构建 10

- Build：PASS。ESP-IDF 5.5.3 完整构建和保护区布局校验通过，应用段 2,332,832 字节，低于 3 MB；Mac 优化构建和签名完整性通过。
- Host tests：PASS。App 内置运行环境通过 284 项 Python 测试，另有原生首启配置、Cursor 时间/数字边界，以及 C 模型、协议、游戏和布局测试。Dino 新增回归在旧代码失败，在修复代码及 Sanitizer 下通过。
- 迁移测试：已测范围 PASS。完整 App 移到另一个含中文与空格的路径，只保留系统 PATH，以空白 HOME 与独立空白 CODEX_HOME 运行；所有必要依赖正常导入，没有作者账号或用量，未替新用户安装活动回调。48 个可执行/依赖文件逐项签名，不依赖开发机缺失的外部库或作者配置。
- Device tests：前一功能版本 1.3.0 已在实物工牌验证并由用户验收，包括资料、阈值、时间、电量、USB/蓝牙、Cursor 配额、勾选过滤和恐龙持续下蹲。最高分已超过原来卡住的位置。1.3.1 固件只调整首次默认资料；已完成构建和主机验证，未为了发布而再刷写/复位设备。
- Unverified：未在另一台 Mac、较旧 macOS 或所有第三方账户类型上逐一实测；Intel 不支持。目标为 Apple 芯片/macOS14+，实测主机为 macOS27。当前无 Developer ID 签名及 Apple 公证。

公开固件来自源码构建，不是设备 Flash 转储。身份区、恢复数据、作者缓存、登录凭据、私人日志与备份均排除。精确文件以 Release SHA-256 为准，简明记录见 release-validation.json。
