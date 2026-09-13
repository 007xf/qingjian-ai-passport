import AppKit
import SwiftUI
import UniformTypeIdentifiers
import CoreText
import Darwin

private let appTitle = "青笺"
private let ink = Color(red: 0.94, green: 0.96, blue: 0.99)
private let accent = Color(red: 82.0/255, green: 201.0/255, blue: 186.0/255)
private let muted = Color(red: 0.64, green: 0.69, blue: 0.76)
private let paper = Color(red: 17.0/255, green: 19.0/255, blue: 24.0/255)
private let panel = Color(red: 25.0/255, green: 29.0/255, blue: 37.0/255)
private let field = Color(red: 32.0/255, green: 38.0/255, blue: 48.0/255)

struct PassportFeature: Identifiable {
    let id: Int
    let name: String
    let symbol: String
}

private let featureRegistry = [
    PassportFeature(id: 0, name: "Codex", symbol: "terminal"),
    PassportFeature(id: 1, name: "Cursor", symbol: "cursorarrow"),
    PassportFeature(id: 2, name: "Gemini", symbol: "sparkles"),
    PassportFeature(id: 4, name: "智能体", symbol: "person.crop.square"),
    PassportFeature(id: 7, name: "Dino", symbol: "gamecontroller")
]

private let features = featureRegistry.filter { BadgeFeatureSelection.selectableIDs.contains($0.id) }

private func tokenText(_ value: Int64) -> String {
    if value >= 100_000_000 { return String(format: "%.2f 亿", Double(value) / 100_000_000) }
    if value >= 10_000 { return String(format: "%.1f 万", Double(value) / 10_000) }
    return value.formatted()
}

private func exactTokenText(_ value: Int64) -> String {
    value.formatted(.number.grouping(.automatic).locale(Locale(identifier: "en_US")))
}

private func dateText(_ timestamp: TimeInterval?, seconds: Bool = false) -> String {
    guard let timestamp, timestamp > 0 else { return "尚未同步" }
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "zh_CN")
    formatter.dateFormat = seconds ? "MM-dd HH:mm:ss" : "MM-dd HH:mm"
    return formatter.string(from: Date(timeIntervalSince1970: timestamp))
}

// Mirrors passport_provider_model.c. A malformed or future snapshot must never
// become a working label or a made-up count in the Mac editor.
struct ProviderDashboardSnapshot {
    static let providers = ["codex", "cursor", "gemini"]
    static let minimumTime: Int64 = 1_704_067_200_000
    static let maximumTime: Int64 = 4_102_444_800_000
    static let maximumInteger: Int64 = 9_007_199_254_740_991
    let provider: String
    let state: String
    let stateAt: Int64
    let stateUntil: Int64
    let metricStatus: String
    let metricAt: Int64

    private static func unsigned(_ raw: Any?) -> Int64? {
        guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let value = number.doubleValue
        guard value.isFinite, value >= 0, value <= Double(maximumInteger), value.rounded(.towardZero) == value else { return nil }
        return Int64(value)
    }

    private static func validTime(_ value: Int64) -> Bool {
        value == 0 || (minimumTime..<maximumTime).contains(value)
    }

    init?(_ raw: [String: Any]) {
        guard let provider = raw["provider"] as? String, Self.providers.contains(provider),
              Self.unsigned(raw["update_id"]) != nil,
              let state = raw["state"] as? String, ["unknown", "working", "idle", "waiting", "error"].contains(state),
              let at = Self.unsigned(raw["state_at_ms"]), let until = Self.unsigned(raw["state_until_ms"]),
              let metricAt = Self.unsigned(raw["metric_at_ms"]), let lastAt = Self.unsigned(raw["last_activity_ms"]),
              [at, until, metricAt, lastAt].allSatisfy(Self.validTime),
              let kind = raw["metric_kind"] as? String, ["none", "requests", "tokens"].contains(kind),
              let metricStatus = raw["metric_status"] as? String, ["ready", "no_records", "partial", "unavailable"].contains(metricStatus),
              let source = raw["source"] as? String,
              ["none", "hooks", "cursor_code_tracking", "gemini_cli", "codex_local"].contains(source),
              let model = raw["model"] as? String, model.utf8.count <= 32,
              model.utf8.allSatisfy({ (32...126).contains($0) }),
              metricAt <= lastAt, at <= lastAt else { return nil }
        let sessionsKnown = raw["active_sessions"] != nil && !(raw["active_sessions"] is NSNull)
        let metricKnown = raw["metric_value"] != nil && !(raw["metric_value"] is NSNull)
        if sessionsKnown && Self.unsigned(raw["active_sessions"]) == nil { return nil }
        if at == 0 {
            guard until == 0, state == "unknown", !sessionsKnown else { return nil }
        } else {
            guard at < until, until <= at + 180_000, source != "none" else { return nil }
        }
        if kind == "requests" && provider != "cursor" || kind == "tokens" && provider == "cursor" { return nil }
        if source == "cursor_code_tracking" && provider != "cursor" || source == "gemini_cli" && provider != "gemini" || source == "codex_local" && provider != "codex" { return nil }
        if metricStatus == "ready" {
            guard metricKnown, Self.unsigned(raw["metric_value"]) != nil,
                  kind != "none", metricAt > 0, source != "none" else { return nil }
        } else if metricKnown { return nil }
        self.provider = provider; self.state = state; stateAt = at; stateUntil = until
        self.metricStatus = metricStatus; self.metricAt = metricAt
    }

    static func parse(_ value: Any?) -> [String: Self] {
        guard let rows = value as? [[String: Any]], rows.count <= providers.count else { return [:] }
        var result: [String: Self] = [:]
        for row in rows {
            guard let snapshot = Self(row), result[snapshot.provider] == nil else { return [:] }
            result[snapshot.provider] = snapshot
        }
        return result
    }

    func activityLabel(now: Int64) -> String {
        guard (Self.minimumTime..<Self.maximumTime).contains(now) else { return "时间待确认" }
        if stateAt == 0 { return "等待活动事件" }
        if now < stateAt { return "时间待确认" }
        if now >= stateUntil { return "状态过期" }
        switch state {
        case "working": return "工作中"
        case "idle": return "空闲"
        case "waiting": return "等待输入"
        case "error": return "发生错误"
        default: return "等待活动事件"
        }
    }

    func metricLabel(now: Int64) -> String {
        let label = provider == "cursor" ? "本地代码活动" : provider == "gemini" ? "CLI 用量" : "本机用量"
        switch metricStatus {
        case "ready":
            guard (Self.minimumTime..<Self.maximumTime).contains(now), now >= metricAt else { return "\(label) · 时间待确认" }
            return "\(label)已接入"
        case "no_records": return "\(label) · 无记录"
        case "partial": return "\(label) · 记录不完整"
        default: return "\(label) · 未接入"
        }
    }
}

struct SourceSetupStatus: Identifiable {
    let id: String
    let configured: Bool
    let intact: Bool
    let observed: Bool
    let freshness: String
    let lastEvent: TimeInterval?
    let appInstalled: Bool
    let cliAvailable: Bool
    let status: String
    let errorCode: String?

    init?(_ raw: [String: Any]) {
        guard let id = raw["provider"] as? String, ["cursor", "gemini"].contains(id),
              let status = raw["status"] as? String else { return nil }
        self.id = id; self.status = status
        errorCode = raw["error_code"] as? String
        configured = raw["configured"] as? Bool ?? false
        intact = raw["receiver_intact"] as? Bool == true && raw["launcher_intact"] as? Bool == true && raw["runtime_intact"] as? Bool == true
        observed = raw["events_observed"] as? Bool ?? false
        freshness = raw["event_freshness"] as? String ?? "none"
        lastEvent = (raw["last_event_at_ms"] as? NSNumber).flatMap { $0.doubleValue > 0 ? $0.doubleValue / 1000 : nil }
        appInstalled = raw["app_installed"] as? Bool ?? false
        cliAvailable = raw["cli_available"] as? Bool ?? false
    }

    var configurationLabel: String {
        if status == "unsupported" { return "暂无稳定接口" }
        if status == "error" { return "需要检查" }
        if !configured { return "尚未配置" }
        if !intact { return "需要修复" }
        return "已配置"
    }

    var eventLabel: String {
        if errorCode == "hooks_disabled" { return "原设置已关闭活动回调，青笺保留该选择。" }
        if errorCode == "owned_hook_modified" { return "检测到自定义回调改动，修复时不会覆盖。" }
        if status == "error" { return "配置或活动记录需要检查，暂不视为已接通。" }
        if !configured || !intact { return "配置完成后，新的活动会同步到工牌。" }
        if !observed { return "尚未收到活动；运行一次新的 Agent 任务后重新检测。" }
        let stamp = dateText(lastEvent, seconds: true)
        return freshness == "fresh" ? "已收到活动 · \(stamp)" : "最近活动 \(stamp) · 等待新事件"
    }
}

// Update only a previously installed mapping. No hooks/configuration are
// created here. Directory-relative POSIX operations avoid following symlinks;
// the interpreter path is written as data for the launcher's quoted argument.
enum ActivityRuntimeMapping {
    @discardableResult
    static func refresh(supportDirectory: URL, runtimeURL: URL) -> Bool {
        let runtimePath = runtimeURL.path
        guard runtimePath.hasPrefix("/"), !runtimePath.contains("\n"),
              FileManager.default.isExecutableFile(atPath: runtimePath),
              let bytes = (runtimePath + "\n").data(using: .utf8), bytes.count <= 4096 else { return false }
        let directory = Darwin.open(supportDirectory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard directory >= 0 else { return false }
        defer { Darwin.close(directory) }
        var directoryInfo = stat()
        guard fstat(directory, &directoryInfo) == 0, directoryInfo.st_uid == getuid(),
              directoryInfo.st_mode & 0o022 == 0 else { return false }
        let original = openat(directory, "runtime.path", O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard original >= 0 else { return false }
        defer { Darwin.close(original) }
        var originalInfo = stat()
        guard fstat(original, &originalInfo) == 0, originalInfo.st_uid == getuid(),
              originalInfo.st_mode & S_IFMT == S_IFREG, originalInfo.st_nlink == 1,
              originalInfo.st_mode & 0o077 == 0 else { return false }
        var previous = [UInt8](repeating: 0, count: 4097)
        let count = Darwin.read(original, &previous, previous.count)
        guard count >= 0, count <= 4096 else { return false }
        if Data(previous.prefix(count)) == bytes { return true }
        let temporary = ".runtime-\(UUID().uuidString).tmp"
        let output = openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard output >= 0 else { return false }
        defer { Darwin.close(output); unlinkat(directory, temporary, 0) }
        let wrote = bytes.withUnsafeBytes { raw -> Bool in
            guard let base = raw.baseAddress else { return false }
            var offset = 0
            while offset < bytes.count {
                let amount = Darwin.write(output, base.advanced(by: offset), bytes.count - offset)
                if amount < 0 && errno == EINTR { continue }
                guard amount > 0 else { return false }
                offset += amount
            }
            return true
        }
        guard wrote else { return false }
        var current = stat()
        guard fstatat(directory, "runtime.path", &current, AT_SYMLINK_NOFOLLOW) == 0,
              current.st_dev == originalInfo.st_dev, current.st_ino == originalInfo.st_ino,
              current.st_mode & S_IFMT == S_IFREG else { return false }
        return renameat(directory, temporary, directory, "runtime.path") == 0
    }
}

final class PassportModel: ObservableObject {
    @Published var config = BadgeConfig()
    @Published var connected = false
    @Published var busy = false
    @Published var operation = ""
    @Published var statusText = "连接 USB 后即可同步"
    @Published var lastError: String? = nil
    @Published var battery: Int? = nil
    @Published var batteryStale = false
    @Published var backgroundEnabled = false
    @Published var backgroundRunning = false
    @Published var backgroundKeepAwake = false
    @Published var backgroundSyncInterval = 60
    @Published var awakeAssertionActive = false
    @Published var backgroundNotice = "后台同步尚未开启"
    @Published var screenOn = true
    @Published var deviceTime: TimeInterval? = nil
    @Published var lastSync: TimeInterval? = nil
    @Published var cycleTokens: Int64 = 0
    @Published var codexCycleTokens: Int64? = nil
    @Published var cursorMonthTokens: Int64? = nil
    @Published var cursorMonthReset: TimeInterval? = nil
    @Published var growthNotice = "等待读取 Codex 与 Cursor"
    @Published var codexQuotaError: String? = nil
    @Published var lifetimeTokens: Int64 = 0
    @Published var stage = 0
    @Published var tokensKnown = false
    @Published var tokensStale = false
    @Published var codexDashboardAvailable = false
    @Published var codexQuotaReady = false
    @Published var codexQuota: CodexQuotaSnapshot? = nil
    @Published var cursorQuota: CursorQuotaSnapshot? = nil
    @Published var cursorQuotaStatus = "尚未读取"
    @Published var dinoAvailable = false
    @Published private(set) var providerDashboardAvailable: Bool? = nil
    @Published private(set) var providerSnapshots: [String: ProviderDashboardSnapshot] = [:]
    @Published var deviceOffsetMinutes = TimeZone.current.secondsFromGMT() / 60
    @Published var cycleStart: TimeInterval? = nil
    @Published var weeklyReset: TimeInterval? = nil
    @Published var sourceLabel = "等待读取本机 Codex 用量"
    @Published var cycleLabel = "按每周额度重置分段"
    @Published var previewStage = 0
    @Published var devicePixelPreview = false
    @Published var threshold1Text = "77777777"
    @Published var threshold2Text = "555555555"
    @Published var showResetConfirmation = false
    @Published var hasUnsavedChanges = false
    @Published var lastUpload: TimeInterval? = nil
    @Published var avatarSyncState = ""
    @Published var avatarPendingUSBStages: [Int] = []
    @Published private(set) var avatarPreviews: [Int: NSImage] = [:]
    @Published private(set) var pendingAvatarPreviews = Set<Int>()
    @Published private(set) var connection = ConnectionSettings()
    @Published var showConnectionSettings = false
    @Published var showSourcesSettings = false
    @Published var showBackgroundSettings = false
    @Published private(set) var sourceSetup: [String: SourceSetupStatus] = [:]
    @Published private(set) var sourceSetupNotice: String? = nil
    @Published private(set) var bluetoothDevices: [BluetoothDevice] = []
    @Published private(set) var didScanBluetooth = false
    @Published private(set) var connectionNotice: String? = nil

    @Published private(set) var offline = false
    private let bridgeQueue = DispatchQueue(label: "ai.passport.bridge", qos: .utility)
    private let previewQueue = DispatchQueue(label: "ai.passport.avatar-preview", qos: .userInitiated)
    private var timer: Timer?
    private var loadedSavedConfig = false
    private var loadedDeviceConfig = false
    private var checkedBackgroundStartup = false
    private var backgroundPollInFlight = false
    private var lastBackgroundSnapshot: Data?
    private let storage: URL
    private let state: URL

    init() {
        offline = CommandLine.arguments.contains("--offline") || ProcessInfo.processInfo.environment["AI_PASSPORT_OFFLINE"] == "1" || Bundle.main.object(forInfoDictionaryKey: "QingJianPreviewOnly") as? Bool == true
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        if let index = CommandLine.arguments.firstIndex(of: "--profile-dir"), CommandLine.arguments.indices.contains(index + 1) {
            storage = URL(fileURLWithPath: CommandLine.arguments[index + 1], isDirectory: true)
        } else {
            storage = support.appendingPathComponent("AI Passport", isDirectory: true)
        }
        state = storage.appendingPathComponent("Bridge", isDirectory: true)
        if let runtime = Bundle.main.resourceURL?.appendingPathComponent("Runtime/bin/python3") {
            ActivityRuntimeMapping.refresh(supportDirectory: state.appendingPathComponent("08 hooks", isDirectory: true), runtimeURL: runtime)
        }
        if let data = try? Data(contentsOf: storage.appendingPathComponent("connection.json")),
           let saved = try? JSONDecoder().decode(ConnectionSettings.self, from: data) {
            connection = saved
        }
        statusText = "连接\(connection.transport.title)后即可同步"
        if let data = try? Data(contentsOf: storage.appendingPathComponent("badge.json")),
           let saved = try? JSONDecoder().decode(BadgeConfig.self, from: data) {
            config = saved
            loadedSavedConfig = true
            let applied = (try? Data(contentsOf: state.appendingPathComponent("04 device config.json")))
                .flatMap { try? JSONDecoder().decode(BadgeConfig.self, from: $0) }
            hasUnsavedChanges = applied != saved
        } else if let data = try? Data(contentsOf: Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("02 个人预设.json")),
                  let preset = try? JSONDecoder().decode(BadgeConfig.self, from: data) {
            config = preset
            hasUnsavedChanges = true
        }
        threshold1Text = String(config.threshold1)
        threshold2Text = String(config.threshold2)
        for value in 0..<3 {
            if let source = config.avatar_paths[String(value)] { renderAvatarPreview(for: value, source: source) }
        }
        if offline {
            statusText = "离线预览 · 可编辑并保存草稿，连接 USB 后可更新图片"
        } else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in self?.refreshBackgroundStatus() }
            startAutomaticSync()
        }
    }

    deinit { timer?.invalidate() }

    private func startAutomaticSync() {
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            guard let self, !self.offline, !self.busy else { return }
            self.refreshBackgroundStatus()
        }
    }

    private func applyBackground(_ reply: [String: Any]) {
        apply(reply)
        backgroundEnabled = reply["enabled"] as? Bool ?? backgroundEnabled
        backgroundRunning = reply["running"] as? Bool ?? false
        backgroundKeepAwake = reply["keep_awake"] as? Bool ?? backgroundKeepAwake
        if let value = integer(reply["sync_interval_seconds"]), [60, 3600].contains(value) {
            backgroundSyncInterval = Int(value)
        }
        awakeAssertionActive = reply["awake_assertion_active"] as? Bool ?? false
        connected = reply["connected"] as? Bool ?? false
        batteryStale = reply["battery_stale"] as? Bool ?? !connected
        if let at = (reply["last_success_at"] as? NSNumber)?.doubleValue, at > 0 { lastSync = at }
        if let error = reply["error"] as? String, !error.isEmpty {
            backgroundNotice = error
        } else if connected {
            backgroundNotice = "后台已连接，关闭窗口后继续同步"
        } else if backgroundEnabled && backgroundRunning {
            backgroundNotice = "正在等待设备，断线后自动重连"
        } else {
            backgroundNotice = backgroundEnabled ? "后台服务未运行" : "后台同步已关闭"
        }
        statusText = backgroundNotice
    }

    func refreshBackgroundStatus() {
        guard !busy, !backgroundPollInFlight else { return }
        run(["service-status"], label: "读取后台状态", quiet: true, affectsConnection: false) { [weak self] reply in
            guard let self else { return }
            var comparable = reply
            comparable.removeValue(forKey: "status_age_seconds")
            let fingerprint = try? JSONSerialization.data(withJSONObject: comparable, options: [.sortedKeys])
            if fingerprint != self.lastBackgroundSnapshot {
                self.lastBackgroundSnapshot = fingerprint
                self.applyBackground(reply)
            }
            if !self.checkedBackgroundStartup {
                self.checkedBackgroundStartup = true
                if reply["configured"] as? Bool == false && self.connection.canConnect {
                    self.enableBackground()
                }
            }
        }
    }

    func enableBackground(thenSync: Bool = false) {
        guard !busy else { return }
        let command = ["service-enable", "--sync-interval", String(backgroundSyncInterval)] + (backgroundKeepAwake ? ["--keep-awake"] : [])
        run(command, label: "正在开启后台同步", affectsConnection: false) { [weak self] reply in
            guard let self else { return }
            self.backgroundEnabled = true
            self.applyBackground(reply)
            if thenSync { self.sync() }
        }
    }

    func disableBackground() {
        guard !busy else { return }
        run(["service-disable"], label: "正在关闭后台同步", affectsConnection: false) { [weak self] reply in
            self?.applyBackground(reply)
            self?.backgroundEnabled = false
        }
    }

    func changeKeepAwake(_ enabled: Bool) {
        backgroundKeepAwake = enabled
        if backgroundEnabled { enableBackground() }
    }

    func changeSyncInterval(_ interval: Int) {
        guard [60, 3600].contains(interval) else { return }
        backgroundSyncInterval = interval
        if backgroundEnabled { enableBackground() }
    }

    func connectDevice() {
        guard !busy else { return }
        guard connection.canConnect else {
            showConnectionSettings = true
            lastError = "请先搜索并选择一块蓝牙工牌。"
            return
        }
        offline = false
        lastError = nil
        statusText = "正在连接\(connection.transport.title)工牌"
        startAutomaticSync()
        if backgroundEnabled { sync() }
        else { enableBackground(thenSync: true) }
    }

    var connectionLabel: String {
        if offline { return "离线预览" }
        return connected ? "\(connection.transport.title)已连接" : "等待\(connection.transport.title)"
    }

    func connect(using choice: ConnectionSettings) {
        guard !busy, choice.canConnect else { return }
        do {
            try choice.save(to: storage.appendingPathComponent("connection.json"))
            if choice != connection { connected = false; providerDashboardAvailable = nil }
            connection = choice
            showConnectionSettings = false
            connectDevice()
        } catch { lastError = "无法保存连接设置：\(error.localizedDescription)" }
    }

    func scanBluetooth() {
        guard !busy else { return }
        connectionNotice = nil
        run(["ble-scan"], label: "正在搜索附近工牌", affectsConnection: false) { [weak self] reply in
            guard let self else { return }
            self.bluetoothDevices = BluetoothDevice.discovered(from: reply)
            self.didScanBluetooth = true
            self.connectionNotice = self.bluetoothDevices.isEmpty ? "未发现工牌。请开启工牌蓝牙，并靠近 Mac 后重试。" : "请选择要连接的工牌。"
        }
    }

    func openBluetoothPairing() {
        guard !offline, !busy, connected, connection.transport == .usb else { return }
        run(["pair"], label: "正在开启蓝牙配对", affectsConnection: false) { [weak self] reply in
            self?.apply(reply)
            self?.connectionNotice = "已请求开启 120 秒配对窗口。选择蓝牙工牌后，在系统弹窗中输入工牌显示的六码。"
        }
    }

    func openSourceSettings() {
        guard !busy else { return }
        showSourcesSettings = true
        refreshSources()
    }

    private func applyCursorQuota(_ raw: Any?) {
        let object = raw as? [String: Any] ?? [:]
        cursorQuota = CursorQuotaSnapshot(object)
        switch object["error_code"] as? String {
        case "not_logged_in", "auth_missing", "authentication_required", "not_signed_in", "sign_in_required", "cursor_not_found": cursorQuotaStatus = "请先打开并登录 Cursor"
        case "invalid_response", "quota_unavailable", "invalid_billing_cycle": cursorQuotaStatus = "Cursor 接口暂不兼容，请更新青笺后重试"
        case "tls_failed", "network_unavailable", "service_unavailable": cursorQuotaStatus = "连接 Cursor 服务失败，请检查网络后重试"
        default: cursorQuotaStatus = cursorQuota == nil ? "用量暂不可用，请检查 Cursor 登录状态后重试" : "等待更新"
        }
    }

    func refreshCursorUsage() {
        guard !busy else { return }
        run(["cursor-usage"], label: "正在读取 Cursor 用量", affectsConnection: false) { [weak self] reply in
            self?.applyCursorQuota(reply["cursor_quota"])
        }
    }

    func refreshCodexUsage() {
        run(["tokens"], label: "正在读取 Codex 用量", affectsConnection: false) { [weak self] reply in
            self?.applyUsage(reply["usage"] as? [String: Any] ?? reply)
        }
    }

    private func applySources(_ reply: [String: Any]) {
        let rows = reply["sources"] as? [[String: Any]] ?? []
        sourceSetup = Dictionary(rows.compactMap(SourceSetupStatus.init).map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
    }

    func refreshSources() {
        guard !busy else { return }
        sourceSetupNotice = nil
        run(["sources-status"], label: "正在检测数据源", affectsConnection: false) { [weak self] reply in
            self?.applySources(reply)
        }
    }

    func configureSource(_ provider: String) {
        guard !busy, ["cursor", "gemini"].contains(provider) else { return }
        sourceSetupNotice = nil
        run(["sources-configure", "--provider", provider], label: "正在配置活动同步", affectsConnection: false) { [weak self] reply in
            self?.applySources(reply)
            let name = provider == "cursor" ? "Cursor" : "Gemini CLI"
            let changed = reply["changed"] as? [String: Any] ?? [:]
            let count = (changed["providers"] as? [String] ?? []).count + (changed["support_files"] as? [String] ?? []).count
            self?.sourceSetupNotice = count == 0 ? "\(name) 配置完整，无需更改。新的任务事件到达后可重新检测。" :
                "\(name) 活动配置已完成，原设置已备份。新的任务事件到达后才能验证实际同步。"
        }
    }

    var currentStageTitle: String {
        stageName(stage)
    }

    func stageName(_ value: Int) -> String { value >= 2 ? "二阶形态" : value == 1 ? "一阶形态" : "初始形态" }

    func image(for value: Int, pixels: Bool = false) -> NSImage? {
        if let path = config.avatar_paths[String(value)] {
            return pixels ? avatarPreviews[value] : NSImage(contentsOfFile: path)
        }
        let name = pixels ? "stage-\(value)" : "stage-\(value)-original"
        return Bundle.main.path(forResource: name, ofType: "png", inDirectory: "Images").flatMap(NSImage.init(contentsOfFile:))
    }

    private func renderAvatarPreview(for value: Int, source: String) {
        avatarPreviews.removeValue(forKey: value)
        pendingAvatarPreviews.insert(value)
        let resources = Bundle.main.resourceURL!
        let python = resources.appendingPathComponent("Runtime/bin/python3")
        let bridge = resources.appendingPathComponent("Backend/passport_bridge.py")
        let cache = storage.appendingPathComponent("Preview", isDirectory: true)
        let destination = cache.appendingPathComponent("stage-\(value)-\(UUID().uuidString).png")
        previewQueue.async { [weak self] in
            var failure: String?
            do {
                try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                let process = Process()
                let pipe = Pipe()
                process.executableURL = python
                process.arguments = [bridge.path, "preview-avatar", "--source", source, "--output", destination.path]
                var environment = ProcessInfo.processInfo.environment
                environment.removeValue(forKey: "PYTHONHOME")
                environment.removeValue(forKey: "PYTHONPATH")
                environment["PYTHONDONTWRITEBYTECODE"] = "1"
                environment["PYTHONNOUSERSITE"] = "1"
                process.environment = environment
                process.standardOutput = pipe; process.standardError = pipe
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let result = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                if process.terminationStatus != 0 || result?["ok"] as? Bool != true || result?["format"] as? String != "rgb565" {
                    failure = result?["error"] as? String ?? "设备头像预览生成失败，请重新选择图片。"
                }
            } catch { failure = "设备头像预览生成失败：\(error.localizedDescription)" }
            let finalFailure = failure
            DispatchQueue.main.async { [weak self] in
                guard let self, self.config.avatar_paths[String(value)] == source else { return }
                self.pendingAvatarPreviews.remove(value)
                if let finalFailure { self.lastError = finalFailure }
                else if let image = NSImage(contentsOf: destination) { self.avatarPreviews[value] = image }
                else { self.lastError = "设备头像预览文件无法读取。" }
            }
        }
    }

    var selectedFeatures: [PassportFeature] {
        features.filter { config.feature_all || config.feature_mask & (1 << $0.id) != 0 }
    }

    var validationError: String? {
        guard let first = Int64(threshold1Text), let second = Int64(threshold2Text), first > 0, second > first, second <= 9_007_199_254_740_991 else { return "成长阈值须为整数，并满足 0 < 一阶 < 二阶（最大 9007199254740991）。" }
        if config.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "请填写名牌姓名。" }
        if config.name.utf8.count > 24 { return "姓名最多 24 个 UTF-8 字节（通常 8 个汉字）。" }
        if config.title.utf8.count > 48 { return "头衔最多 48 个 UTF-8 字节（通常 16 个汉字）。" }
        if config.intro.utf8.count > 120 { return "介绍最多 120 个 UTF-8 字节（通常 40 个汉字）。" }
        return nil
    }

    func change() { hasUnsavedChanges = true }

    func toggleFeature(_ id: Int, _ enabled: Bool) {
        guard BadgeFeatureSelection.selectableIDs.contains(id) else { return }
        config.feature_mask = BadgeFeatureSelection.normalized(config.feature_mask)
        if enabled { config.feature_mask |= (1 << id) }
        else { config.feature_mask &= ~(1 << id) }
        change()
    }

    func chooseAvatar(for value: Int) {
        let panel = NSOpenPanel()
        panel.title = "选择\(stageName(value))图片"
        panel.message = "图片会等比完整显示。三种形态可分别编辑并保存草稿，图片更新需要 USB。"
        panel.allowedContentTypes = [.png, .jpeg, .heic, .tiff, .webP]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url, let image = NSImage(contentsOf: url) else { return }
        do {
            try FileManager.default.createDirectory(at: storage, withIntermediateDirectories: true)
            guard let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
                  let png = bitmap.representation(using: .png, properties: [:]) else {
                lastError = "无法读取这张图片，请尝试 PNG 或 JPEG。"; return
            }
            let copied = storage.appendingPathComponent("stage-\(value)-\(UUID().uuidString).png")
            try png.write(to: copied, options: .atomic)
            config.avatar_paths[String(value)] = copied.path
            previewStage = value
            renderAvatarPreview(for: value, source: copied.path)
            change()
        } catch { lastError = "头像保存失败：\(error.localizedDescription)" }
    }

    func useStageAvatar(_ value: Int) {
        config.avatar_paths.removeValue(forKey: String(value))
        avatarPreviews.removeValue(forKey: value)
        pendingAvatarPreviews.remove(value)
        previewStage = value
        change()
    }

    func updateThresholds() {
        if let first = Int64(threshold1Text) { config.threshold1 = first }
        if let second = Int64(threshold2Text) { config.threshold2 = second }
        if config.threshold1 > 0 && config.threshold2 > config.threshold1 {
            stage = cycleTokens >= config.threshold2 ? 2 : cycleTokens >= config.threshold1 ? 1 : 0
        }
        change()
    }

    func sync(automatic: Bool = false) {
        guard !offline, !busy else { return }
        guard connection.canConnect else {
            statusText = "请选择要连接的蓝牙工牌"
            if !automatic { showConnectionSettings = true }
            return
        }
        run(["sync"], label: "正在同步", quiet: automatic) { [weak self] reply in
            self?.apply(reply)
            self?.lastSync = Date().timeIntervalSince1970
            if let self {
                self.statusText = self.avatarSyncState == "usb_required"
                    ? "时间与用量已同步 · 当前形态图片待 USB 更新"
                    : "\(self.connection.transport.title)已连接 · 每分钟自动同步"
            }
        }
    }

    @discardableResult
    private func writeDraft() throws -> URL {
        try FileManager.default.createDirectory(at: storage, withIntermediateDirectories: true)
        let url = storage.appendingPathComponent("badge.json")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(config).write(to: url, options: .atomic)
        loadedSavedConfig = true
        return url
    }

    func saveDraft() {
        guard !busy else { return }
        if let error = validationError { lastError = error; return }
        do {
            try writeDraft()
            lastError = nil
            statusText = "草稿已保存在此 Mac，尚未写入设备；图片请连接 USB 更新"
        } catch { lastError = "无法保存草稿：\(error.localizedDescription)" }
    }

    func upload() {
        guard !offline, !busy else { return }
        if let error = validationError { lastError = error; return }
        do {
            let url = try writeDraft()
            let uploadedConfig = config
            run(["upload", "--config", url.path], label: connection.transport == .bluetooth ? "正在更新文字与设置" : "正在更新名牌") { [weak self] reply in
                self?.apply(reply)
                self?.hasUnsavedChanges = self?.config != uploadedConfig
                self?.lastUpload = Date().timeIntervalSince1970
                self?.lastSync = Date().timeIntervalSince1970
                if let self {
                    if self.connection.transport == .bluetooth {
                        self.statusText = "文字与设置已写入 · 图片仅支持 USB 更新"
                    } else if self.avatarSyncState == "cycle_unknown" {
                        self.statusText = "文字与设置已写入 · 用量未知，当前形态图片尚未更新"
                    } else {
                        self.statusText = "资料及当前形态已写入设备"
                    }
                }
            }
        } catch { lastError = "无法保存名牌：\(error.localizedDescription)" }
    }

    func resetCycle() {
        guard !offline, !busy else { return }
        run(["reset-cycle", "--reason", "manual_reset_card"], label: "正在开始新周期") { [weak self] reply in
            self?.applyUsage(reply)
            self?.statusText = "已记录重置卡，开始新的用量周期"
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { self?.sync() }
        }
    }

    private func apply(_ reply: [String: Any]) {
        if let device = reply["status"] as? [String: Any] {
            connected = true
            if let value = integer(device["battery_soc"]), (0...100).contains(value) { battery = Int(value); batteryStale = false }
            else { batteryStale = battery != nil }
            screenOn = device["screen_on"] as? Bool ?? true
            if let ms = integer(device["utc_ms"]), ms > 0 { deviceTime = Double(ms) / 1000 }
            if let known = device["tokens_known"] as? Bool { tokensKnown = known }
            if let value = integer(device["stage"]), (0...2).contains(value) { stage = Int(value) }
            tokensStale = device["tokens_stale"] as? Bool ?? false
            codexDashboardAvailable = device["codex_dashboard_supported"] as? Bool ?? false
            providerDashboardAvailable = device["provider_dashboard_supported"] as? Bool ?? false
            dinoAvailable = integer(device["dino_state"]) != nil
            if let offset = integer(device["offset_min"]) { deviceOffsetMinutes = Int(offset) }
            if config.adoptInitialDeviceConfig(device, hasSavedConfig: loadedSavedConfig, didLoadDevice: loadedDeviceConfig, hasUnsavedChanges: hasUnsavedChanges) {
                threshold1Text = String(config.threshold1)
                threshold2Text = String(config.threshold2)
                loadedDeviceConfig = true
            }
        }
        if let usage = reply["usage"] as? [String: Any] { applyUsage(usage) }
        if let service = reply["service"] as? [String: Any] {
            connected = service["connected"] as? Bool ?? connected
            batteryStale = service["battery_stale"] as? Bool ?? !connected
            backgroundRunning = service["running"] as? Bool ?? backgroundRunning
        }
    }

    private func applyUsage(_ usage: [String: Any]) {
        if let raw = usage["codex_quota"] as? [String: Any] { codexQuota = CodexQuotaSnapshot(raw) }
        if usage.keys.contains("cursor_quota") { applyCursorQuota(usage["cursor_quota"]) }
        // Some local operations (for example reset-cycle) return no providers.
        // Keep their last observation only until its own expiry, never renew it.
        if usage.keys.contains("provider_snapshots") {
            providerSnapshots = ProviderDashboardSnapshot.parse(usage["provider_snapshots"])
        }
        avatarSyncState = usage["avatar_sync"] as? String ?? ""
        avatarPendingUSBStages = (usage["avatar_pending_usb_stages"] as? [Int] ?? []).filter { (0...2).contains($0) }
        codexQuotaReady = usage["codex_quota_ready"] as? Bool ?? false
        codexQuotaError = usage["quota_error"] as? String
        let growthSources = usage["growth_sources"] as? [String: Any]
        let codexTokens = growthSources?["codex"] as? [String: Any]
        codexCycleTokens = integer(codexTokens?["cycle_tokens"] ?? usage["cycle_tokens"])
        if let cursor = usage["cursor_quota"] as? [String: Any], let tokens = cursor["token_usage"] as? [String: Any] {
            cursorMonthTokens = integer(tokens["cycle_tokens"])
            cursorMonthReset = (tokens["reset_at_ms"] as? NSNumber).map { $0.doubleValue / 1000 }
        }
        if let value = integer(usage["growth_tokens"]) { cycleTokens = value; tokensKnown = true }
        else { cycleTokens = 0; tokensKnown = false }
        tokensStale = usage["growth_status"] as? String == "stale"
        growthNotice = usage["growth_ready"] as? Bool == true ? "Codex 当前周期 + Cursor 本月" :
            tokensKnown ? "含上次读数 · 等待完整刷新" : "等待两项真实 Token 计数"
        if let value = integer(usage["lifetime_tokens"]) { lifetimeTokens = value }
        if usage["growth_ready"] as? Bool == true {
            stage = cycleTokens >= config.threshold2 ? 2 : cycleTokens >= config.threshold1 ? 1 : 0
        }
        if let value = integer(usage["cycle_started_at"]) { cycleStart = Double(value) }
        if let value = integer(usage["weekly_resets_at"]) { weeklyReset = Double(value) }
        let verified = usage["account_verified"] as? Bool ?? false
        sourceLabel = verified ? "Codex 账号已核验 · Cursor 月度明细" : "Codex 等待核验 · Cursor 月度明细"
        if let coverage = usage["coverage"] as? [String: Any], coverage["cycle_scan_incomplete"] as? Bool == true {
            sourceLabel += " · 本周期仍在索引"
        }
        let reset = usage["reset_source"] as? String ?? ""
        cycleLabel = reset.contains("manual") ? "Codex 始于重置卡记录；Cursor 保持月周期" : "Codex 周期与 Cursor 月周期分别重置后相加"
    }

    private func run(_ command: [String], label: String, quiet: Bool = false, affectsConnection: Bool = true, success: @escaping ([String: Any]) -> Void) {
        guard !busy else { return }
        let silentPoll = quiet && command.first == "service-status"
        guard !silentPoll || !backgroundPollInFlight else { return }
        let transportCommand: [String]
        do { transportCommand = try connection.bridgeArguments(for: command) }
        catch { lastError = error.localizedDescription; showConnectionSettings = true; return }
        if silentPoll { backgroundPollInFlight = true }
        else { busy = true; operation = label }
        if !quiet { lastError = nil }
        let resources = Bundle.main.resourceURL!
        let python = resources.appendingPathComponent("Runtime/bin/python3").path
        let script = resources.appendingPathComponent("Backend/passport_bridge.py").path
        let statePath = state.path
        let routed = backgroundEnabled && ["sync", "status", "upload", "screen", "pair", "reset-cycle", "tokens", "cursor-usage"].contains(command.first ?? "")
        bridgeQueue.async { [weak self] in
            var result: [String: Any] = [:]
            var failure: String?
            do {
                let process = Process()
                let pipe = Pipe()
                process.executableURL = URL(fileURLWithPath: python)
                process.arguments = [script, "--state-dir", statePath] + (routed ? ["--via-service"] : []) + transportCommand
                var environment = ProcessInfo.processInfo.environment
                environment.removeValue(forKey: "PYTHONHOME")
                environment.removeValue(forKey: "PYTHONPATH")
                environment["PYTHONUNBUFFERED"] = "1"
                environment["PYTHONDONTWRITEBYTECODE"] = "1"
                environment["PYTHONNOUSERSITE"] = "1"
                environment["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"
                process.environment = environment
                process.standardOutput = pipe
                process.standardError = pipe
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let text = String(data: data, encoding: .utf8) ?? ""
                if let decoded = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    result = decoded
                } else if let line = text.split(separator: "\n").last(where: { $0.hasPrefix("{") }),
                          let encoded = String(line).data(using: .utf8),
                          let decoded = try? JSONSerialization.jsonObject(with: encoded) as? [String: Any] {
                    result = decoded
                }
                if process.terminationStatus != 0 || result["ok"] as? Bool != true {
                    failure = result["error"] as? String ?? (text.isEmpty ? "设备没有返回有效结果。" : String(text.suffix(600)))
                }
            } catch { failure = "无法启动设备工具：\(error.localizedDescription)" }
            let finalResult = result
            let finalFailure = failure
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                if silentPoll { self.backgroundPollInFlight = false }
                else { self.busy = false; self.operation = "" }
                // A poll started before a user action must not overwrite its
                // progress or re-enable controls while that action is running.
                if silentPoll && self.busy { return }
                if let failure = finalFailure {
                    if affectsConnection {
                        self.connected = false
                        self.statusText = "\(self.connection.transport.title)工牌未连接"
                    }
                    if !quiet || self.lastError == nil { self.lastError = failure }
                } else {
                    if !silentPoll { self.lastError = nil }
                    success(finalResult)
                }
            }
        }
    }
}

private func deviceColor(_ key: String, _ layout: DeviceBadgeLayout) -> NSColor {
    let hex = layout.colors[key] ?? "#FF00FF"
    let rgb = UInt32(hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")), radix: 16) ?? 0xFF00FF
    return NSColor(srgbRed: CGFloat((rgb >> 16) & 255) / 255, green: CGFloat((rgb >> 8) & 255) / 255, blue: CGFloat(rgb & 255) / 255, alpha: 1)
}

final class DeviceTextCanvas: NSView {
    var text = ""
    var element: DeviceBadgeLayout.Element?
    var textColor = NSColor.white
    override var isFlipped: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        guard !text.isEmpty, let element, let size = element.font_size,
              let postscript = element.font_postscript, let lineHeight = element.line_height,
              let baseline = element.baseline_from_bottom else { return }
        guard let font = NSFont(name: postscript, size: size) else {
            NSAttributedString(string: "字体未加载", attributes: [.font: NSFont.systemFont(ofSize: 12), .foregroundColor: NSColor.systemRed]).draw(in: bounds)
            return
        }
        var attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: textColor, .ligature: 0]
        if element.kerning == false { attributes[.kern] = 0.0 }
        if let spacing = element.letter_spacing, spacing != 0 {
            attributes[NSAttributedString.Key(rawValue: kCTTrackingAttributeName as String)] = spacing
        }
        let attributed = NSAttributedString(string: text, attributes: attributes)
        let typesetter = CTTypesetterCreateWithAttributedString(attributed)
        let truncation = CTLineCreateWithAttributedString(NSAttributedString(string: "...", attributes: attributes))
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        context.saveGState()
        context.textMatrix = .identity
        context.translateBy(x: 0, y: bounds.height)
        context.scaleBy(x: 1, y: -1)
        var start = 0
        for index in 0..<(element.max_lines ?? 1) {
            guard start < attributed.length else { break }
            let count = CTTypesetterSuggestLineBreak(typesetter, start, bounds.width)
            guard count > 0 else { break }
            let last = index == (element.max_lines ?? 1) - 1
            var line = CTTypesetterCreateLine(typesetter, CFRange(location: start, length: count))
            if last && start + count < attributed.length && element.overflow == "ellipsis" {
                let remaining = CTTypesetterCreateLine(typesetter, CFRange(location: start, length: attributed.length - start))
                line = CTLineCreateTruncatedLine(remaining, bounds.width, .end, truncation) ?? line
            }
            let width = CTLineGetTypographicBounds(line, nil, nil, nil)
            let x = element.align == "center" ? (bounds.width - width) / 2 : element.align == "right" ? bounds.width - width : 0
            context.textPosition = CGPoint(x: max(0, x), y: bounds.height - (CGFloat(index + 1) * lineHeight - baseline))
            CTLineDraw(line, context)
            start += count
        }
        context.restoreGState()
    }
}

struct DeviceTextBlock: NSViewRepresentable {
    let text: String
    let element: DeviceBadgeLayout.Element
    let color: NSColor
    func makeNSView(context: Context) -> DeviceTextCanvas { DeviceTextCanvas() }
    func updateNSView(_ view: DeviceTextCanvas, context: Context) {
        view.text = text; view.element = element; view.textColor = color; view.needsDisplay = true
    }
}

struct BadgePreview: View {
    @ObservedObject var model: PassportModel
    private let layout = DeviceBadgeLayout.bundled
    private var batterySymbol: String {
        guard let charge = model.battery else { return "battery.0percent" }
        switch charge {
        case 80...: return "battery.100percent"
        case 50..<80: return "battery.75percent"
        case 20..<50: return "battery.50percent"
        default: return "battery.25percent"
        }
    }
    private func timeText(_ date: Date, layout: DeviceBadgeLayout) -> String {
        guard let deviceTime = model.deviceTime else { return layout.labels["time_unknown"]! }
        let elapsed = model.lastSync.map { date.timeIntervalSince1970 - $0 } ?? 0
        let formatter = DateFormatter(); formatter.dateFormat = "HH:mm"
        formatter.timeZone = TimeZone(secondsFromGMT: model.deviceOffsetMinutes * 60)
        return formatter.string(from: Date(timeIntervalSince1970: deviceTime + elapsed))
    }
    private func textBlock(_ key: String, text: String, layout: DeviceBadgeLayout, color: String? = nil) -> some View {
        let element = layout.elements[key]!
        return DeviceTextBlock(text: text, element: element, color: deviceColor(color ?? element.color ?? "primary", layout))
            .frame(width: element.width, height: element.height).clipped().offset(x: element.x, y: element.y)
    }
    var body: some View {
        if let layout {
            ZStack(alignment: .topLeading) {
                Color(nsColor: deviceColor("background", layout))
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    textBlock("time", text: timeText(context.date, layout: layout), layout: layout)
                }
                let battery = layout.elements["battery"]!
                let percent = model.battery.map { "\($0)%" } ?? layout.labels["battery_unknown"]!
                let batteryColor = model.battery.map { $0 < 20 ? "warning" : "secondary" } ?? "secondary"
                let font = NSFont(name: battery.font_postscript!, size: battery.font_size!)
                let numberWidth = font.map { ceil((percent as NSString).size(withAttributes: [.font: $0]).width) } ?? 48
                HStack(spacing: 4) {
                    Image(systemName: batterySymbol).font(.system(size: battery.font_size!)).foregroundStyle(Color(nsColor: deviceColor(batteryColor, layout)))
                    DeviceTextBlock(text: percent, element: battery, color: deviceColor(batteryColor, layout)).frame(width: numberWidth, height: battery.height)
                }.frame(width: battery.width, height: battery.height, alignment: .trailing).offset(x: battery.x, y: battery.y)
                let avatar = layout.elements["avatar"]!
                Group {
                    if let image = model.image(for: model.previewStage, pixels: model.devicePixelPreview) {
                        Image(nsImage: image).resizable().interpolation(model.devicePixelPreview ? .none : .high).scaledToFit()
                    } else if model.pendingAvatarPreviews.contains(model.previewStage) {
                        ProgressView().controlSize(.small)
                    } else { Image(systemName: "photo").font(.system(size: 32)).foregroundStyle(muted) }
                }.frame(width: avatar.width, height: avatar.height).offset(x: avatar.x, y: avatar.y)
                textBlock("name", text: model.config.name, layout: layout)
                textBlock("title", text: model.config.title, layout: layout)
                textBlock("intro", text: model.config.intro, layout: layout)
                let separator = layout.elements["separator"]!
                Rectangle().fill(Color(nsColor: deviceColor(separator.color!, layout)))
                    .frame(width: separator.width, height: separator.height).offset(x: separator.x, y: separator.y)
                textBlock("token_caption", text: layout.labels[model.tokensStale ? "token_caption_stale" : "token_caption"]!, layout: layout)
                textBlock("token_value", text: model.tokensKnown ? String(model.cycleTokens) : layout.labels["tokens_unknown"]!, layout: layout, color: model.tokensStale ? "secondary" : "accent")
            }.frame(width: layout.width, height: layout.height).clipped()
        } else {
            Text("名牌布局资源不可用").font(.system(size: 12)).foregroundStyle(.red).frame(width: 240, height: 320).background(Color.black)
        }
    }
}

struct CyanButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(enabled ? Color.black.opacity(0.88) : muted)
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(enabled ? accent.opacity(configuration.isPressed ? 0.80 : 1) : field, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.white.opacity(0.08), lineWidth: 1))
    }
}

struct SectionLabel: View {
    let title: String
    let note: String
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.system(size: 15, weight: .semibold)).foregroundStyle(ink)
            Spacer()
            Text(note).font(.system(size: 11)).foregroundStyle(muted)
        }
    }
}

struct LabeledInput: View {
    let title: String
    let placeholder: String
    @Binding var text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(size: 11, weight: .medium)).foregroundStyle(muted)
            TextField(placeholder, text: $text).textFieldStyle(.plain).font(.system(size: 13)).foregroundStyle(ink).padding(.horizontal, 10).padding(.vertical, 9)
                .background(field, in: RoundedRectangle(cornerRadius: 7)).overlay(RoundedRectangle(cornerRadius: 7).stroke(ink.opacity(0.13)))
        }
    }
}

private final class ConnectionDraft: ObservableObject {
    @Published var choice = ConnectionSettings()
}

struct ConnectionSheet: View {
    @ObservedObject var model: PassportModel
    @StateObject private var draft = ConnectionDraft()

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("连接工牌").font(.system(size: 20, weight: .semibold))
                Spacer()
                Text(model.connectionLabel).font(.system(size: 11)).foregroundStyle(muted)
            }
            Picker("连接方式", selection: $draft.choice.transport) {
                ForEach(ConnectionTransport.allCases) { transport in Text(transport.title).tag(transport) }
            }.pickerStyle(.segmented).disabled(model.busy)

            if draft.choice.transport == .usb {
                Label("使用数据线将工牌连接到 Mac。", systemImage: "cable.connector")
                    .font(.system(size: 13)).foregroundStyle(ink)
                Text("USB 支持自定义图片更新。连接设置独立保存，不会改动当前名牌草稿。")
                    .font(.system(size: 11)).foregroundStyle(muted)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    Text("先在工牌「设置 → 蓝牙配对」开启配对；也可先通过 USB 连接，再点击下方「开启蓝牙配对」。")
                    Text("首次连接时，在系统弹窗中输入工牌显示的六码。")
                    Text("蓝牙同步文字、设置、时间与用量；自定义图片和恢复默认图片需要 USB。")
                }.font(.system(size: 12)).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Text("附近工牌").font(.system(size: 13, weight: .semibold))
                    Spacer()
                    if model.busy { ProgressView().controlSize(.small) }
                    Button("搜索设备") { model.scanBluetooth() }.buttonStyle(.bordered).disabled(model.busy)
                }
                if !model.bluetoothDevices.isEmpty {
                    ScrollView {
                        VStack(spacing: 7) {
                            ForEach(model.bluetoothDevices) { device in
                                Button {
                                    draft.choice.bluetoothIdentifier = device.identifier
                                    draft.choice.bluetoothName = device.name
                                } label: {
                                    HStack(spacing: 10) {
                                        Image(systemName: draft.choice.validBluetoothIdentifier == device.identifier ? "checkmark.circle.fill" : "circle")
                                            .foregroundStyle(draft.choice.validBluetoothIdentifier == device.identifier ? accent : muted)
                                        VStack(alignment: .leading, spacing: 4) {
                                            Text(device.name).font(.system(size: 12, weight: .medium)).foregroundStyle(ink)
                                            Text(device.identifier).font(.system(size: 9, design: .monospaced)).foregroundStyle(muted)
                                        }
                                        Spacer(minLength: 0)
                                        if let signal = device.rssi { Text("\(signal) dBm").font(.system(size: 10)).foregroundStyle(muted) }
                                    }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
                                        .background(field, in: RoundedRectangle(cornerRadius: 8))
                                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(draft.choice.validBluetoothIdentifier == device.identifier ? accent : ink.opacity(0.1)))
                                }.buttonStyle(.plain).disabled(model.busy)
                            }
                        }
                    }.frame(maxHeight: 190)
                } else if !model.didScanBluetooth {
                    Text("点击搜索后，手动选择要连接的工牌。").font(.system(size: 11)).foregroundStyle(muted)
                }
                if let selected = draft.choice.validBluetoothIdentifier {
                    Text("已选择：\(draft.choice.bluetoothName ?? "青笺工牌") · \(selected.prefix(8))")
                        .font(.system(size: 11)).foregroundStyle(accent)
                }
            }

            Divider()
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("首次蓝牙配对").font(.system(size: 12, weight: .medium))
                    Text("需 USB 已连接；配对窗口持续 120 秒。")
                        .font(.system(size: 10)).foregroundStyle(muted)
                }
                Spacer()
                Button("开启蓝牙配对") { model.openBluetoothPairing() }
                    .buttonStyle(.bordered)
                    .disabled(model.busy || model.offline || !model.connected || model.connection.transport != .usb)
            }
            if let notice = model.connectionNotice {
                Text(notice).font(.system(size: 11)).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true)
            }
            if let error = model.lastError {
                Text(error).font(.system(size: 11)).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button("取消") { model.showConnectionSettings = false }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("通过\(draft.choice.transport.title)连接") { model.connect(using: draft.choice) }
                    .buttonStyle(CyanButtonStyle()).disabled(model.busy || !draft.choice.canConnect)
                    .keyboardShortcut(.defaultAction)
            }
        }.padding(26).frame(width: 520).background(paper).foregroundStyle(ink).tint(accent).preferredColorScheme(.dark)
            .onAppear { draft.choice = model.connection }
    }
}

struct CursorUsageCard: View {
    @ObservedObject var model: PassportModel

    private func meter(_ title: String, used: Double?, fresh: Bool) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(title).font(.system(size: 12, weight: .medium))
                Spacer()
                Text(used.map { String(format: fresh ? "%.0f%% 已用" : "%.0f%% 已用 · 上次", $0) } ?? "额度未知")
                    .font(.system(size: 12, weight: .medium)).monospacedDigit().foregroundStyle(fresh ? ink : muted)
                    .help(used.map { String(format: "已用 %.2f%% · 剩余 %.2f%%", $0, 100 - $0) } ?? "暂无数据")
            }
            ProgressView(value: used ?? 0, total: 100).tint(fresh ? accent : .orange)
        }
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let quota = model.cursorQuota
            let fresh = quota?.fresh(at: context.date) == true
            let displayable = quota?.displayable(at: context.date) == true
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("Cursor 套餐用量").font(.system(size: 15, weight: .semibold))
                    Spacer()
                    if let quota { Text(quota.plan).font(.system(size: 11)).foregroundStyle(accent) }
                    Button("刷新用量") { model.refreshCursorUsage() }.buttonStyle(.bordered).controlSize(.small).disabled(model.busy)
                }
                meter("Cursor 模型", used: displayable ? quota?.cursorUsed : nil, fresh: fresh)
                meter("其他模型", used: displayable ? quota?.otherUsed : nil, fresh: fresh)
                HStack {
                    Text(quota.map { "重置 \(dateText($0.resetAt))" } ?? model.cursorQuotaStatus)
                    Spacer()
                    if let quota { Text("更新 \(dateText(quota.observedAt, seconds: true))") }
                }.font(.system(size: 10)).foregroundStyle(muted)
                if quota != nil && !fresh {
                    Text("保留上次读数 · 后台恢复连接后更新。成长使用真实 Token，不使用额度比例。").font(.system(size: 10)).foregroundStyle(.orange)
                }
            }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(panel, in: RoundedRectangle(cornerRadius: 12))
        }
    }
}

struct CodexUsageCard: View {
    @ObservedObject var model: PassportModel
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let quota = model.codexQuota
            let fresh = quota?.fresh(at: context.date) == true
            let displayable = quota?.displayable(at: context.date) == true
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Codex 剩余额度").font(.system(size: 15, weight: .semibold))
                    Spacer()
                    Button("刷新用量") { model.refreshCodexUsage() }.buttonStyle(.bordered).controlSize(.small).disabled(model.busy)
                }
                if let quota, !quota.windows.isEmpty {
                    ForEach(quota.windows) { window in
                        VStack(alignment: .leading, spacing: 7) {
                            let remaining = displayable ? window.used.map { 100 - $0 } : nil
                            HStack {
                                Text(window.title).font(.system(size: 12))
                                Spacer()
                                Text(remaining.map { String(format: fresh ? "%.0f%% 剩余" : "%.0f%% 剩余 · 上次", $0) } ?? "额度未知")
                                    .font(.system(size: 12, weight: .medium)).monospacedDigit()
                            }
                            ProgressView(value: remaining ?? 0, total: 100).tint(fresh ? accent : .orange)
                            if window.reset > 0 { Text("重置 \(dateText(window.reset))").font(.system(size: 10)).foregroundStyle(muted) }
                        }
                    }
                    Text("\(fresh ? "更新" : "上次更新") \(dateText(quota.observedAt, seconds: true))").font(.system(size: 10)).foregroundStyle(fresh ? muted : .orange)
                } else { Text("尚未取得额度，等待后台读取。").font(.system(size: 11)).foregroundStyle(muted) }
                if let error = model.codexQuotaError, !error.isEmpty {
                    Text(error).font(.system(size: 10)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                }
            }.padding(16).background(panel, in: RoundedRectangle(cornerRadius: 12))
        }
    }
}

struct BackgroundSyncSheet: View {
    @ObservedObject var model: PassportModel
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("后台同步").font(.system(size: 21, weight: .semibold))
            Text("退出青笺后，后台仍会读取用量、保持连接并在断线后自动重连。图片和资料仍由你在青笺中编辑上传。").font(.system(size: 12)).foregroundStyle(muted)
            HStack {
                Text(model.backgroundEnabled ? "已开启" : "未开启").font(.system(size: 15, weight: .semibold))
                Spacer()
                Button(model.backgroundEnabled ? "关闭后台同步" : "开启后台同步") {
                    if model.backgroundEnabled { model.disableBackground() } else { model.enableBackground() }
                }.buttonStyle(.bordered).disabled(model.busy)
            }
            Picker("自动同步频率", selection: Binding(get: { model.backgroundSyncInterval }, set: { model.changeSyncInterval($0) })) {
                Text("每分钟一次").tag(60)
                Text("每小时一次").tag(3600)
            }.pickerStyle(.segmented).disabled(model.busy)
            Text("只读取新增用量，静默更新；点击主界面的“同步”可立即刷新。").font(.system(size: 11)).foregroundStyle(muted)
            Toggle("熄屏继续同步", isOn: Binding(get: { model.backgroundKeepAwake }, set: { model.changeKeepAwake($0) }))
                .toggleStyle(.switch).disabled(model.busy)
            Text("工牌连接时允许显示器熄屏，并保持 Mac 唤醒。工牌离线超过 90 秒后允许 Mac 正常休眠；重新连接后恢复。手动睡眠或合盖仍可能断开。").font(.system(size: 11)).foregroundStyle(muted)
            if model.backgroundKeepAwake {
                Text(model.awakeAssertionActive ? "当前为连接保持唤醒，会额外耗电" : "当前允许 Mac 正常休眠").font(.system(size: 11)).foregroundStyle(muted)
            }
            Text(model.backgroundNotice).font(.system(size: 11)).foregroundStyle(model.connected ? accent : muted)
            if let error = model.lastError { Text(error).font(.system(size: 11)).foregroundStyle(.orange) }
            HStack { Spacer(); Button("完成") { model.showBackgroundSettings = false }.buttonStyle(CyanButtonStyle()).keyboardShortcut(.defaultAction) }
        }.padding(24).frame(width: 470).background(paper).foregroundStyle(ink).tint(accent).preferredColorScheme(.dark)
    }
}

struct SourcesSheet: View {
    @ObservedObject var model: PassportModel

    private func card(_ id: String, title: String, subtitle: String, detail: String, configurable: Bool) -> some View {
        let state = model.sourceSetup[id]
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(title).font(.system(size: 16, weight: .semibold))
                Spacer()
                Text((id == "gemini" ? "CLI · " : "") + (state?.configurationLabel ?? "正在检测")).font(.system(size: 11, weight: .medium))
                    .foregroundStyle(state?.status == "error" ? .orange : state?.configured == true && state?.intact == true ? accent : muted)
            }
            Text(subtitle).font(.system(size: 12, weight: .medium)).foregroundStyle(ink)
            Text(detail).font(.system(size: 11)).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true)
            if configurable {
                HStack(alignment: .center, spacing: 12) {
                    Text(state?.eventLabel ?? "读取本机配置中…").font(.system(size: 10)).foregroundStyle(muted)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                    Button(state?.configured == true ? (id == "cursor" ? "检查 / 修复 Cursor" : "检查 / 修复 CLI") :
                            (id == "cursor" ? "配置 Cursor" : "配置 Gemini CLI")) {
                        model.configureSource(id)
                    }.buttonStyle(.bordered).controlSize(.small).disabled(model.busy || state == nil)
                }
            }
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(panel, in: RoundedRectangle(cornerRadius: 12))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("数据源接入").font(.system(size: 21, weight: .semibold))
                Spacer()
                if model.busy { ProgressView().controlSize(.small) }
            }
            Text("先连接应用的活动数据，再通过 USB 或蓝牙同步到工牌。")
                .font(.system(size: 12)).foregroundStyle(muted)
            card("cursor", title: "Cursor", subtitle: "桌面 Agent 与 CLI · 官方活动回调",
                 detail: "记录任务开始、工具活动和结束；套餐用量读取当前 Cursor 账号。", configurable: true)
            CursorUsageCard(model: model)
            card("gemini", title: "Gemini", subtitle: "Mac 客户端与 CLI 分开接入",
                 detail: "Gemini Mac 客户端暂未找到稳定的实时状态接口。下方只配置 Gemini CLI，需要 CLI 本身能够登录和运行；不会连接桌面版。", configurable: true)
            if let notice = model.sourceSetupNotice {
                Text(notice).font(.system(size: 11)).foregroundStyle(accent).fixedSize(horizontal: false, vertical: true)
            }
            if let error = model.lastError {
                Text(error).font(.system(size: 11)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            Text("活动回调仅保存匿名会话、状态、时间及模型名。配置完成和收到真实活动会分别显示。")
                .font(.system(size: 10)).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("重新检测") { model.refreshSources() }.buttonStyle(.bordered).disabled(model.busy)
                Spacer()
                Button("完成") { model.showSourcesSettings = false }.buttonStyle(CyanButtonStyle())
                    .keyboardShortcut(.defaultAction).disabled(model.busy)
            }
        }.padding(24).frame(width: 590).background(paper).foregroundStyle(ink).tint(accent).preferredColorScheme(.dark)
    }
}

struct ContentView: View {
    @StateObject var model = PassportModel()
    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(ink.opacity(0.06))
            HStack(alignment: .top, spacing: 0) {
                previewColumn
                Rectangle().fill(ink.opacity(0.09)).frame(width: 1)
                ScrollView {
                    VStack(alignment: .leading, spacing: 21) {
                        identityEditor
                        featuresEditor
                        if model.config.feature_all || model.config.feature_mask & 1 != 0 { CodexUsageCard(model: model) }
                        if model.config.feature_all || model.config.feature_mask & (1 << 1) != 0 {
                            CursorUsageCard(model: model)
                        }
                        usagePanel
                    }.padding(26)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            footer
        }
        .frame(minWidth: 930, idealWidth: 1040, maxWidth: .infinity, minHeight: 800, idealHeight: 860, maxHeight: .infinity)
        .background(paper).foregroundStyle(ink).tint(accent).preferredColorScheme(.dark)
        .sheet(isPresented: $model.showConnectionSettings) { ConnectionSheet(model: model) }
        .sheet(isPresented: $model.showSourcesSettings) { SourcesSheet(model: model) }
        .sheet(isPresented: $model.showBackgroundSettings) { BackgroundSyncSheet(model: model) }
        .confirmationDialog("已在 Codex 使用重置卡？", isPresented: $model.showResetConfirmation, titleVisibility: .visible) {
            Button("已使用，开始新周期", role: .destructive) { model.resetCycle() }
            Button("取消", role: .cancel) { }
        } message: {
            Text("这会从现在开始重新累计 Codex Token，Cursor 仍按原月度周期计数，再相加用于成长。已有记录会保留；此按钮不会兑换或消耗实际重置卡。")
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "person.text.rectangle.fill").font(.system(size: 24)).foregroundStyle(accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(appTitle).font(.system(size: 19, weight: .semibold))
                Text("随身名牌编辑器").font(.system(size: 11)).foregroundStyle(muted)
            }
            Spacer()
            HStack(spacing: 6) {
                Circle().fill(model.offline ? Color.orange : model.connected ? Color.green : Color.gray).frame(width: 6, height: 6)
                Text(model.connectionLabel).font(.system(size: 11, weight: .medium))
            }.padding(.horizontal, 10).padding(.vertical, 7).background(panel, in: Capsule())
            Button { model.showConnectionSettings = true } label: {
                Label("连接设置", systemImage: "slider.horizontal.3")
            }.buttonStyle(.bordered).controlSize(.regular).disabled(model.busy)
            Button { model.openSourceSettings() } label: {
                Label("数据源接入", systemImage: "link")
            }.buttonStyle(.bordered).disabled(model.busy)
            Button { model.showBackgroundSettings = true; model.refreshBackgroundStatus() } label: {
                Label("后台同步", systemImage: "arrow.triangle.2.circlepath")
            }.buttonStyle(.bordered).disabled(model.busy)
            Button { model.connectDevice() } label: {
                Label(model.offline ? "退出离线预览并连接" : model.connected ? "同步" : "连接设备", systemImage: model.connected ? "arrow.triangle.2.circlepath" : model.connection.transport == .bluetooth ? "antenna.radiowaves.left.and.right" : "cable.connector")
            }
                .buttonStyle(CyanButtonStyle()).controlSize(.regular).disabled(model.busy)
                .help("通过选定的连接方式同步时间与用量，保留当前未上传的编辑内容。")
        }.padding(.horizontal, 26).frame(height: 76)
    }

    private var previewColumn: some View {
        ScrollView {
        VStack(spacing: 0) {
            HStack {
                Text("布局预览").font(.system(size: 14, weight: .semibold))
                Spacer()
                Text("240 × 320").font(.system(size: 10, design: .monospaced)).foregroundStyle(muted)
            }.padding(.bottom, 19)
            Picker("画质", selection: $model.devicePixelPreview) {
                Text("清晰预览").tag(false); Text("设备像素").tag(true)
            }.pickerStyle(.segmented).padding(.bottom, 12)
            BadgePreview(model: model).padding(9).background(Color.black, in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.white.opacity(0.14), lineWidth: 1))
                .shadow(color: .black.opacity(0.13), radius: 14, y: 7)
            Picker("预览形态", selection: $model.previewStage) {
                Text("初始").tag(0); Text("一阶").tag(1); Text("二阶").tag(2)
            }.pickerStyle(.segmented).padding(.top, 17)
            HStack(spacing: 8) {
                ForEach(0..<3, id: \.self) { value in
                    VStack(spacing: 5) {
                        if let image = model.image(for: value) {
                            Image(nsImage: image).resizable().scaledToFit().frame(width: 60, height: 60)
                        }
                        Text(model.stageName(value)).font(.system(size: 10, weight: .medium))
                        Button("选择图片") { model.chooseAvatar(for: value) }.buttonStyle(.bordered).controlSize(.mini)
                        Button("恢复青子") { model.useStageAvatar(value) }.buttonStyle(.borderless).controlSize(.mini)
                            .disabled(model.config.avatar_paths[String(value)] == nil)
                    }.frame(maxWidth: .infinity).padding(.vertical, 7).background(panel, in: RoundedRectangle(cornerRadius: 8))
                }
            }.padding(.top, 12)
            Text("清晰预览使用原图；设备像素展示实际显示素材。\n三种形态可分别保存，图片仅通过 USB 更新；设备缓存一张自定义形态图。").font(.system(size: 10)).foregroundStyle(muted).multilineTextAlignment(.center).padding(.top, 10)
            if !model.avatarPendingUSBStages.isEmpty {
                Text("待 USB 更新：" + model.avatarPendingUSBStages.map { model.stageName($0) }.joined(separator: "、"))
                    .font(.system(size: 10)).foregroundStyle(accent).multilineTextAlignment(.center).padding(.top, 7)
            }
            Spacer(minLength: 16)
            VStack(alignment: .leading, spacing: 8) {
                deviceLine("电量", model.battery.map { "\($0)%\(model.batteryStale ? " · 上次" : "")" } ?? "—", "battery.75percent")
                deviceLine("设备时间", dateText(model.deviceTime), "clock")
                deviceLine("屏幕", model.connected ? (model.screenOn ? "已亮屏" : "已熄屏") : "—", "display")
                Text("快速连按3下 OK 熄屏，任意功能键唤醒。").font(.system(size: 10)).foregroundStyle(muted).padding(.top, 3)
            }.padding(13).frame(maxWidth: .infinity, alignment: .leading).background(panel, in: RoundedRectangle(cornerRadius: 9))
        }.padding(26)
        }.frame(width: 322).frame(maxHeight: .infinity)
    }

    private func deviceLine(_ label: String, _ value: String, _ symbol: String) -> some View {
        HStack {
            Image(systemName: symbol).frame(width: 14)
            Text(label)
            Spacer()
            Text(value).foregroundStyle(ink)
        }.font(.system(size: 11)).foregroundStyle(muted)
    }

    private var identityEditor: some View {
        VStack(alignment: .leading, spacing: 13) {
            SectionLabel(title: "名牌资料", note: "编辑后上传到设备")
            HStack(spacing: 12) {
                LabeledInput(title: "姓名", placeholder: "你的姓名", text: textBinding(\.name))
                LabeledInput(title: "头衔", placeholder: "你正在探索什么", text: textBinding(\.title))
            }
            LabeledInput(title: "一句介绍", placeholder: "写一句让人认识你的话", text: textBinding(\.intro))
            if let error = model.validationError { Text(error).font(.system(size: 11)).foregroundStyle(.red) }
        }
    }

    private func textBinding(_ keyPath: WritableKeyPath<BadgeConfig, String>) -> Binding<String> {
        Binding(get: { model.config[keyPath: keyPath] }, set: { model.config[keyPath: keyPath] = $0; model.change() })
    }

    private var featuresEditor: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel(title: "功能显示", note: "最多 \(features.count) 项")
            Toggle("全部显示", isOn: Binding(get: { model.config.feature_all }, set: { model.config.feature_all = $0; model.change() }))
                .toggleStyle(.switch).controlSize(.small).font(.system(size: 12))
            Text(model.config.feature_all ? "已选择全部 \(features.count) 项；关闭后可逐项设置。" : "勾选希望显示在名牌上的功能。")
                .font(.system(size: 11)).foregroundStyle(muted)
            TimelineView(.periodic(from: .now, by: 1)) { context in
            LazyVGrid(columns: [GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading)], alignment: .leading, spacing: 13) {
                ForEach(features) { feature in
                    VStack(alignment: .leading, spacing: 5) {
                        if model.config.feature_all {
                            HStack(spacing: 5) {
                                Image(systemName: "checkmark.square.fill").foregroundStyle(accent)
                                Text(feature.name).font(.system(size: 11)).foregroundStyle(ink)
                            }.accessibilityElement(children: .ignore).accessibilityLabel("\(feature.name)，已选择显示")
                        } else {
                            Toggle(isOn: Binding(get: { model.config.feature_mask & (1 << feature.id) != 0 }, set: { model.toggleFeature(feature.id, $0) })) {
                                Text(feature.name).font(.system(size: 11)).foregroundStyle(ink)
                            }.toggleStyle(.checkbox)
                        }
                        Text(featureStatus(feature.id, at: context.date)).font(.system(size: 10)).foregroundStyle(muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }.padding(13).background(panel, in: RoundedRectangle(cornerRadius: 8))
            }
            Text("勾选只控制显示，不代表功能已接入。当前状态显示在各选项下方。")
                .font(.system(size: 10)).foregroundStyle(muted).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func featureStatus(_ id: Int, at date: Date) -> String {
        let now = Int64(date.timeIntervalSince1970 * 1000)
        switch id {
        case 0:
            if model.codexDashboardAvailable { return model.codexQuotaReady ? "额度仪表盘已同步" : "额度仪表盘 · 额度未知" }
            return model.tokensKnown ? "名牌用量已同步 · 仪表盘待更新" : "名牌数据待同步 · 仪表盘待更新"
        case 1, 2, 4:
            if id == 1, let quota = model.cursorQuota, quota.fresh(at: date) {
                let cursor = quota.cursorUsed.map { String(format: "%.0f%%", $0) } ?? "未知"
                let other = quota.otherUsed.map { String(format: "%.0f%%", $0) } ?? "未知"
                return "Cursor 模型 \(cursor) 已用\n其他模型 \(other) 已用"
            }
            guard model.connected, !model.offline else { return "设备待连接" }
            guard model.providerDashboardAvailable == true else {
                return model.providerDashboardAvailable == false ? "等待固件更新" : "等待设备确认"
            }
            if id == 4 {
                let visible = model.config.feature_all ? BadgeFeatureSelection.selectableMask : BadgeFeatureSelection.normalized(model.config.feature_mask)
                let selected = [(0, "codex", "Codex"), (1, "cursor", "Cursor"), (2, "gemini", "Gemini")]
                    .filter { visible & (1 << $0.0) != 0 }
                if selected.isEmpty { return "未选择 AI 来源" }
                return selected.map { _, provider, title in
                    "\(title) · \(model.providerSnapshots[provider]?.activityLabel(now: now) ?? "等待活动事件")"
                }.joined(separator: "\n")
            }
            let provider = id == 1 ? "cursor" : "gemini"
            guard let snapshot = model.providerSnapshots[provider] else { return "等待活动事件\n\(id == 1 ? "本地代码活动" : "CLI 用量") · 未接入" }
            return "\(snapshot.activityLabel(now: now))\n\(snapshot.metricLabel(now: now))"
        case 7: return model.dinoAvailable ? "离线游戏" : "等待固件更新"
        default: return "暂不可用"
        }
    }

    private var usagePanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel(title: "成长用量", note: "Codex + Cursor")
            HStack(spacing: 12) {
                LabeledInput(title: "一阶阈值", placeholder: "77777777", text: thresholdBinding(first: true))
                LabeledInput(title: "二阶阈值", placeholder: "555555555", text: thresholdBinding(first: false))
            }
            HStack(alignment: .firstTextBaseline) {
                Text(model.tokensKnown ? tokenText(model.cycleTokens) : "—").font(.system(size: 33, weight: .semibold, design: .rounded)).monospacedDigit()
                    .help(model.tokensKnown ? exactTokenText(model.cycleTokens) + " Token" : "本周期用量未知")
                Text("TOKEN").font(.system(size: 10, weight: .semibold, design: .monospaced)).foregroundStyle(muted)
                Spacer()
                Text(model.currentStageTitle).font(.system(size: 11, weight: .medium)).foregroundStyle(accent)
            }
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(ink.opacity(0.09))
                    Capsule().fill(accent).frame(width: max(0, geometry.size.width * min(1, Double(model.cycleTokens) / Double(max(1, model.config.threshold2)))))
                }
            }.frame(height: 5)
            HStack {
                milestone("初始", "0", active: true)
                Spacer()
                milestone("一阶", exactTokenText(model.config.threshold1), active: model.stage >= 1)
                Spacer()
                milestone("二阶", exactTokenText(model.config.threshold2), active: model.stage >= 2)
            }
            Divider().padding(.vertical, 1)
            VStack(alignment: .leading, spacing: 5) {
                Text(model.growthNotice).font(.system(size: 11, weight: .medium)).foregroundStyle(model.tokensStale ? .orange : accent)
                Text("Codex 当前周期：\(model.codexCycleTokens.map(exactTokenText) ?? "未知") Token").font(.system(size: 10)).foregroundStyle(muted)
                Text("Cursor 本月：\(model.cursorMonthTokens.map(exactTokenText) ?? "未知") Token").font(.system(size: 10)).foregroundStyle(muted)
                Text(model.sourceLabel).font(.system(size: 10, weight: .medium))
                Text("\(model.cycleLabel) · 开始 \(dateText(model.cycleStart))").font(.system(size: 10)).foregroundStyle(muted)
                if model.weeklyReset != nil { Text("下次周额度重置：\(dateText(model.weeklyReset))").font(.system(size: 10)).foregroundStyle(muted) }
                if model.cursorMonthReset != nil { Text("Cursor 月周期重置：\(dateText(model.cursorMonthReset))").font(.system(size: 10)).foregroundStyle(muted) }
            }
            HStack {
                Text("每周期首次进入二阶时，名牌播放短暂变身。").font(.system(size: 10)).foregroundStyle(muted)
                Spacer()
                Button("我已使用重置卡") { model.showResetConfirmation = true }.buttonStyle(.borderless).font(.system(size: 11)).disabled(model.busy || model.offline)
            }
        }.padding(16).background(panel, in: RoundedRectangle(cornerRadius: 10))
    }

    private func milestone(_ name: String, _ value: String, active: Bool) -> some View {
        HStack(spacing: 4) {
            Circle().fill(active ? accent : ink.opacity(0.18)).frame(width: 5, height: 5)
            Text(name).font(.system(size: 10, weight: .medium))
            Text(value).font(.system(size: 9, design: .monospaced)).foregroundStyle(muted)
        }
    }

    private func thresholdBinding(first: Bool) -> Binding<String> {
        Binding(get: { first ? model.threshold1Text : model.threshold2Text }, set: { value in
            if first { model.threshold1Text = value } else { model.threshold2Text = value }
            model.updateThresholds()
        })
    }

    private var footer: some View {
        HStack(spacing: 12) {
            if model.busy { ProgressView().controlSize(.small) }
            VStack(alignment: .leading, spacing: 3) {
                Text(model.busy ? model.operation : model.lastError ?? model.statusText)
                    .font(.system(size: 11)).foregroundStyle(model.lastError == nil ? muted : .red).lineLimit(2).textSelection(.enabled)
                if model.lastSync != nil { Text("最近同步 \(dateText(model.lastSync, seconds: true))").font(.system(size: 9)).foregroundStyle(muted) }
            }
            Spacer()
            if model.hasUnsavedChanges { Text("有未上传的修改").font(.system(size: 10)).foregroundStyle(muted) }
            Button("保存草稿") { model.saveDraft() }
                .buttonStyle(.bordered).disabled(model.busy || model.validationError != nil)
            Button { model.upload() } label: {
                Label(model.connection.transport == .bluetooth ? "更新文字与设置" : "上传到名牌", systemImage: "arrow.up.to.line").font(.system(size: 12, weight: .semibold)).padding(.horizontal, 8).padding(.vertical, 3)
            }.buttonStyle(CyanButtonStyle()).disabled(model.busy || model.offline || model.validationError != nil)
        }.padding(.horizontal, 26).frame(minHeight: 65).background(panel).overlay(alignment: .top) { Rectangle().fill(ink.opacity(0.09)).frame(height: 1) }
    }
}

@main
struct PassportApp: App {
    @NSApplicationDelegateAdaptor(PassportAppDelegate.self) var delegate
    init() { BadgeFonts.register() }
    var body: some Scene {
        WindowGroup("青笺") {
            ContentView().onAppear { NSApp.activate(ignoringOtherApps: true) }
        }.windowStyle(.hiddenTitleBar).defaultSize(width: 1040, height: 860)
        .commands { CommandGroup(replacing: .newItem) { } }
    }
}

final class PassportAppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
