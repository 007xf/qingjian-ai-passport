<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# 构建青笺 Mac 应用

此目录包含原生编辑器源码。应用面向 Apple Silicon（arm64）和 macOS 14
及以上系统，当前安装包不支持 Intel Mac。现版本在 macOS 27 上完成过测试；
最低部署版本不代表已经在每个较旧系统版本上完成实机验证。

## 准备条件

- 一台 Apple Silicon Mac，已安装 Xcode Command Line Tools 和 Swift 编译器。
- 一份已发布的青笺应用压缩包，SHA-256 与发布说明中的校验值一致，并已解压。
  该应用提供内置 Python、依赖库、Montserrat 字体及图标。
- 完整源码仓库，包括 `assets`、`tools` 和 `macos` 目录。

脚本不会联网下载、安装或执行替代 Python，也不使用 Homebrew 或系统 Python。
必须明确指定运行时模板。编译前会检查模板签名完整性、依赖导入和 Montserrat
字体校验值。这些检查用于验证完整性及依赖可用性，不能替代下载后核对发布包
SHA-256 的步骤。

## 构建

在仓库根目录执行：

```bash
QINGJIAN_RUNTIME_TEMPLATE="/path/to/青笺.app" ./macos/build.sh
```

默认输出为 `dist/青笺.app`。也可以指定另一个输出目录：

```bash
QINGJIAN_RUNTIME_TEMPLATE="/path/to/青笺.app" \
QINGJIAN_OUTPUT_DIR="/path/to/new-build" \
./macos/build.sh
```

目标应用必须尚不存在。脚本拒绝覆盖已有或正在运行的应用，也拒绝覆盖模板或
写入模板内部。重复构建时请使用新的输出目录。构建临时目录会在成功或失败后
清理，源码和模板保持不变。

应用使用本目录的 Swift 文件及 `Info.plist` 编译；后端模块和布局读取仓库源码。
角色原图原样复制，设备预览使用共享 RGB565 转换生成 160 × 160 图像。
中文字体来自 `assets/fonts`，模板中的其他字体及其许可说明一并保留。
不会复制个人草稿、账号凭据、用量历史或蓝牙配对信息。

脚本先逐个签名所有 Mach-O 库及扩展，再签名整个应用，其中包括隐藏的
`PIL/.dylibs` 文件。随后会验证签名，从新应用的内置运行时导入后端依赖，
加载字体并检查三张预览图的尺寸。构建过程不会连接工牌、登录账号或操作图形界面。

## 分发与首次启动

此构建使用本地临时签名，没有 Developer ID 签名，也没有 Apple 公证。
因此不能宣称下载后无需 Gatekeeper 检查即可直接打开。脚本不会修改 Gatekeeper、
隔离属性或 macOS 隐私设置。正式签名分发需要另行完成 Developer ID 签名及公证流程。

在另一台 Mac 上，应用将设置保存到该 Mac 当前用户的
`~/Library/Application Support/AI Passport`，并读取该用户已存在且受支持的 AI
应用数据。安装青笺不会替用户登录 Codex、Cursor 或 Gemini。
用户需要自行选择工牌连接，自定义图片上传必须使用 USB。
数据源配置验收及工牌实机测试与本地构建成功分别记录。
