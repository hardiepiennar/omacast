import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

// Omacast keeps the panel declarative: discovery and casting are owned by the
// unprivileged controller, while privileged networking stays in the packaged
// session helper.
Panel {
  id: root
  moduleName: "hardie.omarchy-cast"
  ipcTarget: "hardie.omarchy-cast"
  manageIpc: false

  readonly property string controllerPath: decodeURIComponent(String(Qt.resolvedUrl("../bin/omacast")).replace("file://", ""))
  readonly property string packagePath: decodeURIComponent(String(Qt.resolvedUrl("../packaging/arch")).replace("file://", ""))
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color muted: Qt.darker(foreground, 1.5)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property int maxControllerResponseChars: 262144
  readonly property int maxReceivers: 64
  readonly property int maxIssues: 16
  readonly property int maxWarnings: 8

  property var doctor: ({})
  property var launchPlan: ({})
  property var receivers: []
  property var session: ({ phase: "idle" })
  property string receiverId: ""
  property string receiverName: ""
  property string pendingReceiverId: ""
  property int receiverCursor: -1
  property bool keyboardCursor: false
  property string message: "Put your TV in Display Mirroring, then choose it below."
  property bool scanRunning: false
  property bool connectRunning: false
  property bool startPending: false
  property bool cancelRequested: false
  property bool stopAwaitingIdle: false
  property bool recoverRunning: false
  property bool autoScanDone: false
  readonly property int startStateTimeoutMs: 45000
  property bool doctorComplete: false
  property bool nerdMode: false

  readonly property string phase: String(session.phase || "idle")
  readonly property bool sessionActive: ["checking", "discovering", "preparing", "connecting", "streaming", "stopping", "recovering"].indexOf(phase) >= 0
  readonly property bool streaming: phase === "streaming"
  readonly property bool needsRecovery: phase === "error"
  readonly property bool sessionBusy: sessionActive || connectRunning || startPending
  readonly property bool visuallyBusy: scanRunning || sessionBusy
  readonly property color connectingColor: Qt.hsla(0.10, 0.72, 0.62, 1.0)
  readonly property color connectedColor: Qt.hsla(0.36, 0.55, 0.55, 1.0)
  readonly property color iconColor: streaming ? connectedColor
    : needsRecovery ? Color.urgent
    : visuallyBusy ? connectingColor
    : foreground
  // Material Design's cast glyphs: a display with wireless waves, matching
  // Omarchy's single-glyph Bluetooth and Agents panel treatment.
  readonly property string icon: needsRecovery ? "󰀦" : streaming ? "󰄙" : "󰄘"

  function parseJson(text, fallback) {
    if (typeof text !== "string" || text.length > maxControllerResponseChars) return fallback
    try {
      var value = JSON.parse(text)
      return jsonShapeWithinBudget(value) ? value : fallback
    } catch (error) { return fallback }
  }

  function jsonShapeWithinBudget(value) {
    var pending = [{ value: value, depth: 0 }]
    var nodes = 0
    while (pending.length) {
      var current = pending.pop()
      if (++nodes > 4096 || current.depth > 12) return false
      var item = current.value
      if (item === null || typeof item === "boolean") continue
      if (typeof item === "number") {
        if (!isFinite(item)) return false
        continue
      }
      if (typeof item === "string") {
        if (item.length > 8192) return false
        continue
      }
      if (Array.isArray(item)) {
        if (item.length > 128) return false
        for (var index = 0; index < item.length; index++)
          pending.push({ value: item[index], depth: current.depth + 1 })
        continue
      }
      if (!item || typeof item !== "object") return false
      var keys = Object.keys(item)
      if (keys.length > 128) return false
      for (var keyIndex = 0; keyIndex < keys.length; keyIndex++) {
        if (keys[keyIndex].length > 128) return false
        pending.push({ value: item[keys[keyIndex]], depth: current.depth + 1 })
      }
    }
    return true
  }

  function parseJsonObject(text, fallback) {
    var value = parseJson(text, fallback)
    return value && typeof value === "object" && !Array.isArray(value) ? value : fallback
  }

  function boundedText(value, fallback, limit) {
    var result = typeof value === "string" ? value : fallback
    result = result.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "�")
    return result.length <= limit ? result : result.slice(0, Math.max(0, limit - 1)) + "…"
  }

  function boundedStrings(items, maximum, limit) {
    if (!Array.isArray(items)) return []
    var result = []
    for (var i = 0; i < items.length && result.length < maximum; i++)
      if (typeof items[i] === "string") result.push(boundedText(items[i], "", limit))
    return result
  }

  function normalizeIssues(items) {
    if (!Array.isArray(items)) return []
    var result = []
    for (var i = 0; i < items.length && result.length < maxIssues; i++) {
      var issue = items[i]
      if (!issue || typeof issue !== "object") continue
      result.push({
        code: boundedText(issue.code, "unknown", 80),
        name: boundedText(issue.name, "Casting requirement", 120),
        scope: boundedText(issue.scope, "host", 32),
        message: boundedText(issue.message, "Casting requirement unavailable", 240)
      })
    }
    return result
  }

  function normalizeReceivers(items) {
    if (!Array.isArray(items)) return []
    var result = []
    var ids = ({})
    for (var i = 0; i < items.length && result.length < maxReceivers; i++) {
      var item = items[i]
      if (!item || typeof item !== "object" || typeof item.id !== "string" || typeof item.name !== "string") continue
      var id = boundedText(item.id, "", 128).toUpperCase()
      var name = boundedText(item.name, "", 120)
      var kind = item.kind === "fire-tv" ? "fire-tv" : item.kind === "wfd-display" ? "wfd-display" : ""
      var validAddress = /^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(id)
        && (parseInt(id.slice(0, 2), 16) & 1) === 0
        && id !== "00:00:00:00:00:00" && id !== "FF:FF:FF:FF:FF:FF"
      if (!validAddress || !name || !kind || ids[id]) continue
      ids[id] = true
      result.push({ id: id, name: name, kind: kind })
    }
    return result
  }

  function normalizeDoctor(value) {
    if (!value || typeof value !== "object" || value.schemaVersion !== 1) return ({})
    var sourceMonitors = Array.isArray(value.monitors) ? value.monitors : []
    var monitors = []
    for (var i = 0; i < sourceMonitors.length && monitors.length < 16; i++) {
      var output = sourceMonitors[i]
      if (!output || typeof output !== "object" || typeof output.name !== "string") continue
      monitors.push({ name: boundedText(output.name, "Display", 128), focused: output.focused === true })
    }
    var readiness = value.readiness && typeof value.readiness === "object" ? value.readiness : {}
    return {
      schemaVersion: 1,
      monitors: monitors,
      readiness: {
        ready: readiness.ready === true,
        setupRequired: readiness.setupRequired === true,
        summary: boundedText(readiness.summary, "Casting support check failed", 240),
        issues: normalizeIssues(readiness.issues)
      }
    }
  }

  function finiteNumber(value) {
    return typeof value === "number" && isFinite(value) ? value : 0
  }

  function normalizeProcess(value) {
    value = value && typeof value === "object" ? value : {}
    return { pid: finiteNumber(value.pid), cpuPercent: finiteNumber(value.cpuPercent), cpuDelayMsPerSec: finiteNumber(value.cpuDelayMsPerSec) }
  }

  function normalizePacket(value) {
    value = value && typeof value === "object" ? value : {}
    return { packets: finiteNumber(value.packets), p95GapMs: finiteNumber(value.p95GapMs) }
  }

  function normalizeTelemetry(value) {
    value = value && typeof value === "object" ? value : {}
    var negotiated = value.negotiated && typeof value.negotiated === "object" ? value.negotiated : {}
    var output = value.output && typeof value.output === "object" ? value.output : {}
    var processes = value.processes && typeof value.processes === "object" ? value.processes : {}
    var transport = value.transport && typeof value.transport === "object" ? value.transport : {}
    var radio = value.radio && typeof value.radio === "object" ? value.radio : {}
    var timing = value.packetTiming && typeof value.packetTiming === "object" ? value.packetTiming : {}
    var maxima = value.maxima && typeof value.maxima === "object" ? value.maxima : {}
    var health = value.health && typeof value.health === "object" ? value.health : {}
    return {
      sampledAt: boundedText(value.sampledAt, "", 64),
      negotiated: { mode: boundedText(negotiated.mode, "", 64), fps: finiteNumber(negotiated.fps) },
      output: {
        measuredFps: finiteNumber(output.measuredFps), reportedFps: finiteNumber(output.reportedFps),
        realtimeRatio: finiteNumber(output.realtimeRatio), dropFrames: finiteNumber(output.dropFrames),
        dupFrames: finiteNumber(output.dupFrames)
      },
      processes: { capture: normalizeProcess(processes.capture), mux: normalizeProcess(processes.mux) },
      transport: {
        interface: boundedText(transport.interface, "", 64), txMbps: finiteNumber(transport.txMbps),
        sendQueueBytes: finiteNumber(transport.sendQueueBytes), txErrors: finiteNumber(transport.txErrors),
        txDropped: finiteNumber(transport.txDropped)
      },
      radio: {
        signalDbm: radio.signalDbm === undefined ? undefined : finiteNumber(radio.signalDbm),
        txBitrateMbps: finiteNumber(radio.txBitrateMbps), retryDelta: finiteNumber(radio.retryDelta),
        failureDelta: finiteNumber(radio.failureDelta), beaconLossDelta: finiteNumber(radio.beaconLossDelta)
      },
      packetTiming: { video: normalizePacket(timing.video), audio: normalizePacket(timing.audio), avSkewMs: finiteNumber(timing.avSkewMs) },
      maxima: { sendQueueBytes: finiteNumber(maxima.sendQueueBytes), cpuDelayMsPerSec: finiteNumber(maxima.cpuDelayMsPerSec) },
      health: { status: boundedText(health.status, "", 32), issues: boundedStrings(health.issues, maxIssues, 240) }
    }
  }

  function normalizeSession(value) {
    value = value && typeof value === "object" ? value : {}
    var allowed = ["idle", "checking", "discovering", "preparing", "connecting", "streaming", "stopping", "error", "recovering"]
    var valuePhase = typeof value.phase === "string" && allowed.indexOf(value.phase) >= 0 ? value.phase : "error"
    var error = value.error && typeof value.error === "object" ? value.error : {}
    return {
      schemaVersion: value.schemaVersion === 1 ? 1 : 0,
      phase: valuePhase,
      sessionId: boundedText(value.sessionId, "", 64),
      startedAt: boundedText(value.startedAt, "", 64),
      error: { code: boundedText(error.code, "runtime-state-invalid", 80), message: boundedText(error.message, "Casting state could not be read", 512) },
      telemetry: normalizeTelemetry(value.telemetry)
    }
  }

  function sessionLabel() {
    return phase.charAt(0).toUpperCase() + phase.slice(1)
  }

  function iconTooltip() {
    if (needsRecovery) return "Omacast · Recovery required"
    if (streaming) return "Omacast · Casting to display"
    if (scanRunning) return "Omacast · Looking for displays"
    if (phase === "preparing") return "Omacast · Preparing network"
    if (phase === "connecting") return "Omacast · Connecting to display"
    if (phase === "stopping" || phase === "recovering") return "Omacast · Restoring network"
    if (sessionBusy) return "Omacast · " + sessionLabel()
    if (!doctorComplete) return "Omacast · Checking system"
    if (!systemReady()) return setupRequired() ? "Omacast · Companion update required" : "Omacast · System not ready"
    return "Omacast · Ready to cast"
  }

  function heroMeta() {
    if (scanRunning) return "LOOKING FOR DISPLAYS"
    if (needsRecovery) return "CAST NEEDS ATTENTION"
    if (connectRunning || startPending) return "STARTING CAST"
    if (sessionActive) return sessionLabel().toUpperCase()
    if (!doctorComplete) return "CHECKING SYSTEM"
    if (!systemReady()) return setupRequired() ? "SETUP REQUIRED" : "SYSTEM NOT READY"
    return "READY TO CAST"
  }

  function monitor() {
    var monitors = doctor.monitors || []
    for (var i = 0; i < monitors.length; i++) if (monitors[i].focused) return monitors[i]
    return monitors.length ? monitors[0] : null
  }

  function sourceSummary() {
    var output = monitor()
    if (!output) return "Desktop source unavailable"
    return output.name + " · mirror · 720p60 with audio"
  }

  function heroTitle() {
    return "Omacast"
  }

  function readinessSummary() {
    if (!doctorComplete) return "Checking casting support…"
    return boundedText((doctor.readiness || {}).summary, "Casting support check failed", 240)
  }

  function systemReady() {
    return doctorComplete && (doctor.readiness || {}).ready === true
  }

  function setupRequired() {
    return doctorComplete && (doctor.readiness || {}).setupRequired === true
  }

  function readinessDetail() {
    if (!doctorComplete) return "Omacast is checking the engine, network, display, and audio path."
    if (setupRequired()) return "Install the measured engine and guarded networking helper before scanning."
    var issues = (doctor.readiness || {}).issues || []
    var messages = []
    for (var i = 0; i < issues.length && i < 3; i++) messages.push(String(issues[i].message || issues[i].name || "Casting requirement unavailable"))
    return messages.length ? messages.join(" · ") : "Casting support could not be verified."
  }

  function recoveryGuidance() {
    var code = String(session.error && session.error.code || "")
    if (code === "authorization-cancelled") return "Nothing was changed. Restore this session, then check that the current companion package is installed."
    if (code === "authorization-timeout") return "Restore this session, then update the companion package before retrying."
    if (code === "guard-setup-failed") return "Restore this session, run Check again, and update the companion if setup is still unavailable."
    if (code === "dhcp-failed") return "The direct Wi-Fi link formed but received no address. Restore it, return the TV to Display Mirroring, and retry."
    if (code === "p2p-negotiation-failed") return "The direct Wi-Fi link could not form. Restore it, confirm Display Mirroring is open, and retry nearby."
    if (code === "receiver-negotiation-failed" || code === "receiver-negotiation-timeout") return "The TV did not finish Miracast setup. Restore this session, reopen Display Mirroring, and retry."
    if (code === "capture-failed") return "Desktop capture or encoding stopped. Restore this session, keep the selected display active, and run Check again."
    if (code === "engine-exited") return "The casting engine exited unexpectedly. Restore this session and check the bounded session log before retrying."
    return "The failed cast needs a clean local reset. Restore its session state, then try again."
  }

  function setupCommand() {
    return "cd " + Util.shellQuote(packagePath) + " && makepkg -si"
  }

  function openSetupTerminal() {
    var command = setupCommand()
    Quickshell.execDetached(["bash", "-c", "printf %s " + Util.shellQuote(command) + " | wl-copy"])
    Quickshell.execDetached(["omarchy", "launch", "terminal"])
    message = "Setup command copied. Paste it in the terminal, approve the package install, then press R here."
  }

  function maybeAutoScan() {
    if (root.opened && !sessionBusy && phase === "idle" && doctorComplete && systemReady() && !autoScanDone) {
      autoScanDone = true
      startScan()
    }
  }

  function applyDoctor(text) {
    var result = normalizeDoctor(parseJsonObject(text, {}))
    doctor = result
    doctorComplete = result.schemaVersion === 1 && result.readiness !== undefined
    if (!sessionBusy) {
      if (!doctorComplete) message = "Casting support check failed"
      else if (!systemReady()) message = readinessSummary()
    }
    requestPlan()
    maybeAutoScan()
  }

  function selectedReceiverStillExists(items) {
    for (var i = 0; i < items.length; i++) if (items[i].id === receiverId) return true
    return false
  }

  function receiverIndex(id) {
    for (var i = 0; i < receivers.length; i++) if (String(receivers[i].id) === String(id)) return i
    return -1
  }

  function moveReceiverCursor(delta) {
    if (!receivers.length || sessionBusy) return
    keyboardCursor = true
    receiverCursor = receiverCursor < 0
      ? (delta > 0 ? 0 : receivers.length - 1)
      : Math.max(0, Math.min(receivers.length - 1, receiverCursor + delta))
    receiverId = ""
    receiverName = ""
    message = "Press Enter to cast to " + String(receivers[receiverCursor].name || "this display") + "."
  }

  function activateKeyboardAction() {
    if (sessionBusy) return
    if (receiverCursor >= 0 && receiverCursor < receivers.length) {
      selectReceiver(receivers[receiverCursor])
      startConnect()
    } else if (!scanRunning) {
      startScan()
    }
  }

  function applyScan(text) {
    // A receiver normally stops advertising after it joins the P2P group. A
    // late scan must not clear the selected display or replace active-session
    // progress with a misleading empty-discovery message.
    if (sessionBusy) return
    var result = parseJsonObject(text, {})
    if (result.ok === false) {
      receivers = []
      receiverId = ""
      receiverName = ""
      receiverCursor = -1
      message = boundedText(result.error && result.error.message, "Could not scan for displays", 512)
      return
    }
    var items = normalizeReceivers(result.receivers)
    receivers = items
    if (!selectedReceiverStillExists(items)) {
      receiverId = ""
      receiverName = ""
    }
    receiverCursor = items.length ? 0 : -1
    keyboardCursor = items.length > 0
    message = items.length
      ? (items.length === 1 ? "Select " + items[0].name : "Choose a display")
      : "No displays found. Check that the TV is in Display Mirroring."
  }

  function selectReceiver(receiver) {
    receiverId = String(receiver.id || "")
    receiverName = String(receiver.name || "Miracast display")
    receiverCursor = receiverIndex(receiverId)
    message = receiverName + " selected"
    requestPlan()
  }

  function selectAndConnect(receiver) {
    if (sessionBusy) return
    selectReceiver(receiver)
    startConnect()
  }

  function requestPlan() {
    if (systemReady() && receiverId && !planProc.running) planProc.running = true
  }

  function startScan() {
    if (sessionBusy || needsRecovery) {
      message = "Stop the current cast before looking for another display."
      return
    }
    if (!systemReady()) {
      message = readinessSummary()
      if (!doctorProc.running) doctorProc.running = true
      return
    }
    if (!scanProc.running) {
      receiverId = ""
      receiverName = ""
      receiverCursor = -1
      message = "Looking for nearby displays…"
      scanProc.running = true
    }
  }

  function startConnect() {
    if (!receiverId) {
      message = "Choose a display first."
      return
    }
    if (!systemReady()) {
      message = readinessSummary()
      return
    }
    if (!connectProc.running) {
      pendingReceiverId = receiverId
      message = "Connecting to " + receiverName + "…"
      cancelRequested = false
      stopAwaitingIdle = false
      startPending = true
      connectProc.running = true
    }
  }

  function requestStop() {
    if (sessionBusy && !stopProc.running) {
      cancelRequested = true
      stopAwaitingIdle = true
      startPending = false
      startDeadline.stop()
      message = sessionActive ? "Stopping the cast safely…" : "Cancelling the connection…"
      stopProc.running = true
    }
  }

  function requestRecovery() {
    if (needsRecovery && !recoverProc.running) {
      message = "Restoring Omacast to a clean idle state…"
      recoverProc.running = true
    }
  }

  function updateSession(value) {
    session = normalizeSession(value)
    if (phase !== "idle") {
      startPending = false
      startDeadline.stop()
    }
    if (phase === "idle" && stopAwaitingIdle && !connectProc.running && !stopProc.running) {
      stopAwaitingIdle = false
      cancelRequested = false
      message = "Cast stopped"
    } else if (phase === "streaming") message = "Casting to " + (receiverName || "your display")
    else if (phase === "stopping") message = "Restoring your network…"
    else if (phase === "error") {
      stopAwaitingIdle = false
      cancelRequested = false
      var problem = session.error && session.error.message
      message = boundedText(problem, "The cast did not start cleanly", 512)
    }
    maybeAutoScan()
  }

  function qualityStatus() {
    var telemetry = session.telemetry || {}
    var negotiated = telemetry.negotiated || {}
    var output = telemetry.output || {}
    if (!negotiated.mode) return "Waiting for negotiation"
    return negotiated.mode + " · " + Number(output.measuredFps || 0).toFixed(2) + " fps"
  }

  function elapsedStatus() {
    var started = Date.parse(String(session.startedAt || ""))
    if (isNaN(started)) return "Starting"
    var seconds = Math.max(0, Math.floor((Date.now() - started) / 1000))
    var minutes = Math.floor(seconds / 60)
    return minutes + ":" + String(seconds % 60).padStart(2, "0") + " · " + sessionLabel()
  }

  function hasTelemetry() {
    return Boolean((session.telemetry || {}).sampledAt)
  }

  function pacingStatus() {
    if (!hasTelemetry()) return "Warming up"
    var output = (session.telemetry || {}).output || {}
    return Number(output.realtimeRatio || 0).toFixed(3) + "× · "
      + Number(output.dropFrames || 0) + " / " + Number(output.dupFrames || 0)
  }

  function pipelineStatus() {
    var processes = (session.telemetry || {}).processes || {}
    var capture = processes.capture || {}
    var mux = processes.mux || {}
    if (!capture.pid && !mux.pid) return "Warming up"
    return Number(capture.cpuPercent || 0).toFixed(0) + "% / "
      + Number(mux.cpuPercent || 0).toFixed(0) + "%"
  }

  function transportStatus() {
    var telemetry = session.telemetry || {}
    var transport = telemetry.transport || {}
    if (!hasTelemetry() || !transport.interface) return "Warming up"
    return Number(transport.txMbps || 0).toFixed(2) + " Mbps · "
      + (Number(transport.sendQueueBytes || 0) / 1024).toFixed(1) + " KiB q"
  }

  function radioStatus() {
    var radio = (session.telemetry || {}).radio || {}
    if (radio.signalDbm === undefined) return "Warming up"
    var value = radio.signalDbm + " dBm · " + Number(radio.txBitrateMbps || 0).toFixed(0) + "M"
    return value + (Number(radio.retryDelta || 0) ? " · " + Number(radio.retryDelta) + " retry" : "")
  }

  function healthStatus() {
    var health = (session.telemetry || {}).health || {}
    var issues = health.issues || []
    if (!health.status) return "Warming up"
    var label = String(health.status)
    label = label.charAt(0).toUpperCase() + label.slice(1)
    return label + (issues.length ? " · " + issues.length + (issues.length === 1 ? " flag" : " flags") : "")
  }

  function cadenceStatus() {
    if (!hasTelemetry()) return "Warming up"
    var output = (session.telemetry || {}).output || {}
    return Number(output.measuredFps || 0).toFixed(1) + " / "
      + Number(output.reportedFps || 0).toFixed(1) + " fps"
  }

  function schedulerStatus() {
    var processes = (session.telemetry || {}).processes || {}
    var capture = processes.capture || {}
    var mux = processes.mux || {}
    if (!capture.pid && !mux.pid) return "Warming up"
    return Number(capture.cpuDelayMsPerSec || 0).toFixed(1) + " / "
      + Number(mux.cpuDelayMsPerSec || 0).toFixed(1) + " ms/s"
  }

  function packetTimingStatus() {
    if (!hasTelemetry()) return "Warming up"
    var timing = (session.telemetry || {}).packetTiming || {}
    var video = timing.video || {}
    var audio = timing.audio || {}
    if (!Number(video.packets || 0) && !Number(audio.packets || 0))
      return "Off · stream-safe"
    return Number(video.p95GapMs || 0).toFixed(1) + " / "
      + Number(audio.p95GapMs || 0).toFixed(1) + " ms p95"
  }

  function packetProbeActive() {
    var timing = (session.telemetry || {}).packetTiming || {}
    return Number((timing.video || {}).packets || 0) > 0 || Number((timing.audio || {}).packets || 0) > 0
  }

  function avStatus() {
    if (!hasTelemetry()) return "Warming up"
    var timing = (session.telemetry || {}).packetTiming || {}
    if (!Number((timing.video || {}).packets || 0) && !Number((timing.audio || {}).packets || 0))
      return "Unavailable"
    return Number(timing.avSkewMs || 0).toFixed(1) + " ms V−A"
  }

  function maximaStatus() {
    if (!hasTelemetry()) return "Warming up"
    var maxima = (session.telemetry || {}).maxima || {}
    return (Number(maxima.sendQueueBytes || 0) / 1024).toFixed(1) + " KiB q · "
      + Number(maxima.cpuDelayMsPerSec || 0).toFixed(1) + " ms/s"
  }

  function signalColor(level) {
    if (!hasTelemetry()) return muted
    if (level === 2) return Color.urgent
    if (level === 1) return connectingColor
    return connectedColor
  }

  function cadenceColor() {
    var output = (session.telemetry || {}).output || {}
    var target = Number(((session.telemetry || {}).negotiated || {}).fps || 60)
    var actual = Number(output.measuredFps || 0)
    if (actual <= 0) return muted
    return signalColor(actual < target * 0.90 ? 2 : actual < target * 0.98 ? 1 : 0)
  }

  function pacingColor() {
    var output = (session.telemetry || {}).output || {}
    var drift = Math.abs(Number(output.realtimeRatio || 0) - 1)
    var frames = Number(output.dropFrames || 0) + Number(output.dupFrames || 0)
    if (Number(output.realtimeRatio || 0) <= 0) return muted
    return signalColor(drift > 0.05 || frames > 5 ? 2 : drift > 0.02 || frames > 0 ? 1 : 0)
  }

  function pipelineColor() {
    var processes = (session.telemetry || {}).processes || {}
    if (!(processes.capture || {}).pid && !(processes.mux || {}).pid) return muted
    var highest = Math.max(Number((processes.capture || {}).cpuPercent || 0), Number((processes.mux || {}).cpuPercent || 0))
    return signalColor(highest > 90 ? 2 : highest > 70 ? 1 : 0)
  }

  function schedulerColor() {
    var processes = (session.telemetry || {}).processes || {}
    if (!(processes.capture || {}).pid && !(processes.mux || {}).pid) return muted
    var highest = Math.max(Number((processes.capture || {}).cpuDelayMsPerSec || 0), Number((processes.mux || {}).cpuDelayMsPerSec || 0))
    return signalColor(highest > 200 ? 2 : highest > 100 ? 1 : 0)
  }

  function transportColor() {
    var transport = (session.telemetry || {}).transport || {}
    if (!transport.interface) return muted
    var queue = Number(transport.sendQueueBytes || 0)
    var errors = Number(transport.txErrors || 0) + Number(transport.txDropped || 0)
    return signalColor(errors > 0 || queue > 262144 ? 2 : queue > 65536 ? 1 : 0)
  }

  function radioColor() {
    var radio = (session.telemetry || {}).radio || {}
    if (radio.signalDbm === undefined) return muted
    var signal = Number(radio.signalDbm || -100)
    var failures = Number(radio.failureDelta || 0) + Number(radio.beaconLossDelta || 0)
    var retries = Number(radio.retryDelta || 0)
    return signalColor(failures > 0 || signal < -75 ? 2 : retries > 0 || signal < -65 ? 1 : 0)
  }

  function maximaColor() {
    var maxima = (session.telemetry || {}).maxima || {}
    var queue = Number(maxima.sendQueueBytes || 0)
    var delay = Number(maxima.cpuDelayMsPerSec || 0)
    return signalColor(queue > 262144 || delay > 250 ? 2 : queue > 65536 || delay > 150 ? 1 : 0)
  }

  function packetColor() {
    var timing = (session.telemetry || {}).packetTiming || {}
    var gap = Math.max(Number((timing.video || {}).p95GapMs || 0), Number((timing.audio || {}).p95GapMs || 0))
    return signalColor(gap > 50 ? 2 : gap > 25 ? 1 : 0)
  }

  function avColor() {
    var skew = Math.abs(Number(((session.telemetry || {}).packetTiming || {}).avSkewMs || 0))
    return signalColor(skew > 100 ? 2 : skew > 50 ? 1 : 0)
  }

  function showPanel() {
    root.controller.show()
    refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }
  // Omarchy's stable shell-level summon route calls open() on the currently
  // mounted bar widget, avoiding stale per-plugin IPC handlers after updates.
  function open() { showPanel() }
  function close() { root.controller.hide() }
  function toggle() { if (root.opened) root.close(); else root.open() }
  function refresh() {
    if (!doctorProc.running) doctorProc.running = true
    if (!statusProc.running) statusProc.running = true
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  Component.onCompleted: refresh()
  onOpenedChanged: if (opened) {
    autoScanDone = false
    doctorComplete = false
    refresh()
  }
  onReceiverIdChanged: if (systemReady()) requestPlan()
  Process {
    id: doctorProc
    command: [root.controllerPath, "doctor"]
    stdout: BoundedCollector { id: doctorOutput }
    stderr: DiscardCollector {}
    onRunningChanged: if (running) doctorOutput.reset()
    onExited: function(exitCode) {
      if (exitCode === 0 && !doctorOutput.overflow) {
        root.applyDoctor(doctorOutput.output)
      } else {
        root.doctorComplete = false
        root.message = "Casting support check failed"
      }
    }
  }
  Process {
    id: statusProc
    command: [root.controllerPath, "status"]
    stdout: BoundedCollector { id: statusOutput }
    stderr: DiscardCollector {}
    onRunningChanged: if (running) statusOutput.reset()
    onExited: root.updateSession(root.parseJsonObject(statusOutput.output, { phase: "error", error: { code: "runtime-state-invalid", message: "Casting state could not be read" } }))
  }
  Process {
    id: planProc
    command: [root.controllerPath, "plan", "--peer", root.receiverId, "--mode", "mirror", "--profile", "safe"]
    stdout: BoundedCollector { id: planOutput }
    stderr: DiscardCollector {}
    onRunningChanged: if (running) planOutput.reset()
    onExited: {
      var result = root.parseJsonObject(planOutput.output, {})
      root.launchPlan = { warnings: root.boundedStrings(result.warnings, root.maxWarnings, 240) }
    }
  }
  Process {
    id: scanProc
    command: [root.controllerPath, "scan", "--timeout", "8"]
    stdout: BoundedCollector { id: scanOutput }
    stderr: DiscardCollector {}
    onRunningChanged: {
      root.scanRunning = running
      if (running) scanOutput.reset()
    }
    onExited: root.applyScan(scanOutput.output)
  }
  Process {
    id: connectProc
    command: [root.controllerPath, "start", "--peer", root.pendingReceiverId, "--mode", "mirror", "--profile", "safe"]
    stdout: BoundedCollector { id: connectOutput }
    stderr: DiscardCollector {}
    onRunningChanged: {
      root.connectRunning = running
      if (running) connectOutput.reset()
    }
    onExited: function(exitCode) {
      var result = root.parseJsonObject(connectOutput.output, {})
      if (result.ok === true) {
        if (root.cancelRequested) {
          root.message = "Cancelling the connection…"
        } else {
          root.message = "Starting the guarded cast session…"
          startDeadline.restart()
        }
      } else {
        root.startPending = false
        root.message = root.boundedText(result.error && result.error.message, "Cast failed", 512)
      }
      if (exitCode !== 0 && !root.cancelRequested) {
        if (root.startPending) {
          root.startPending = false
          root.message = "The cast launcher did not start. Try again."
        }
      }
      if (root.cancelRequested && !stopProc.running) stopProc.running = true
      root.refresh()
    }
  }
  Process {
    id: stopProc
    command: [root.controllerPath, "stop"]
    stdout: BoundedCollector { id: stopOutput }
    stderr: DiscardCollector {}
    onRunningChanged: if (running) stopOutput.reset()
    onExited: function(exitCode) {
      var result = root.parseJsonObject(stopOutput.output, {})
      if (result.ok === true && result.phase === "idle") {
        root.stopAwaitingIdle = false
        root.cancelRequested = false
        root.message = "Connection cancelled"
      } else if (result.ok === true) {
        root.message = "Stopping the cast safely…"
      } else {
        root.stopAwaitingIdle = false
        root.cancelRequested = false
        root.message = root.boundedText(result.error && result.error.message, "Could not stop the cast", 512)
      }
      root.refresh()
    }
  }
  Process {
    id: recoverProc
    command: [root.controllerPath, "recover"]
    stdout: BoundedCollector { id: recoverOutput }
    stderr: DiscardCollector {}
    onRunningChanged: {
      root.recoverRunning = running
      if (running) recoverOutput.reset()
    }
    onExited: function(exitCode) {
      var result = root.parseJsonObject(recoverOutput.output, {})
      root.message = result.ok === true ? "Casting state restored" : root.boundedText(result.error && result.error.message, "Recovery failed", 512)
      root.refresh()
    }
  }

  // Keep the bar icon truthful even while the panel is closed. The fast timer
  // follows a known active session; this low-frequency idle heartbeat catches
  // state changes that happened between panel lifetimes or outside QML.
  Timer { interval: 1000; running: root.opened || root.sessionBusy; repeat: true; onTriggered: if (!statusProc.running) statusProc.running = true }
  Timer { interval: 3000; running: !root.opened && !root.sessionBusy; repeat: true; onTriggered: if (!statusProc.running) statusProc.running = true }
  Timer {
    id: startDeadline
    // The detached controller performs bounded host discovery before it can
    // publish `checking`. Allow that complete bounded stage, then cancel a
    // launcher that still owns no session instead of racing slower machines.
    interval: root.startStateTimeoutMs
    repeat: false
    onTriggered: {
      if (root.startPending) {
        root.message = "The cast did not enter a session. Cancelling it safely…"
        root.requestStop()
      }
    }
  }

  // Keep Omarchy's compositor-level idle monitor awake while the detached
  // service holds the system-level sleep inhibitor. Attaching this to the bar
  // keeps it effective even when the popover is closed.
  IdleInhibitor {
    window: root.bar || null
    enabled: root.sessionActive
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.icon
    foreground: root.iconColor
    activeColor: root.iconColor
    active: root.visuallyBusy || root.needsRecovery
    tooltipText: root.iconTooltip()
    onPressed: function(buttonCode) { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(390))
    contentHeight: panel.fittedContentHeight(
      content.implicitHeight,
      Style.space(root.nerdMode && root.sessionActive ? 650 : 570)
    )

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (dy !== 0) root.moveReceiverCursor(dy)
      }
      onActivateRequested: root.activateKeyboardAction()
      onTextKey: function(text) {
        if ((text === "n" || text === "N") && root.sessionActive) {
          root.nerdMode = !root.nerdMode
          return
        }
        if ((text === "q" || text === "Q") && root.sessionBusy) {
          root.requestStop()
          return
        }
        if ((text === "r" || text === "R") && !root.sessionActive)
          root.systemReady() ? root.startScan() : root.refresh()
      }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: content
          width: parent.width
          spacing: Style.space(14)

          PanelHero {
            width: parent.width
            title: root.heroTitle()
            meta: root.heroMeta()
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                text: root.icon
                textFormat: Text.PlainText
                color: root.iconColor
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
          }

          Text {
            width: parent.width
            text: root.message
            textFormat: Text.PlainText
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          PanelSeparator { foreground: root.foreground }

          Column {
            visible: !root.sessionActive && !root.needsRecovery && root.systemReady()
            width: parent.width
            spacing: Style.space(10)

            InfoPair { label: "Source"; value: root.sourceSummary() }
            InfoPair { label: "System"; value: root.readinessSummary() }

            Row {
              width: parent.width
              spacing: Style.space(8)
              PanelSectionHeader {
                text: "AVAILABLE DISPLAYS"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }
              Item { width: Math.max(0, parent.width - parent.children[0].implicitWidth - parent.children[2].implicitWidth - parent.spacing * 2); height: 1 }
              Button {
                text: root.scanRunning ? "Scanning…"
                  : "Rescan <span style=\"color:" + root.muted + "\">(R)</span>"
              enabled: !root.scanRunning && !root.sessionBusy
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.caption
                onClicked: root.startScan()
              }
            }

            Text {
              visible: root.receivers.length > 0
              width: parent.width
              text: "Click a TV, or use ↑/↓ and Enter · Esc close"
              textFormat: Text.PlainText
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              horizontalAlignment: Text.AlignHCenter
            }

            Repeater {
              model: root.receivers
              delegate: ReceiverButton {
                required property var modelData
                required property int index
                width: parent.width
                labelText: root.boundedText(modelData.name, "Miracast display", 120)
                tooltipText: String(modelData.kind) === "fire-tv"
                  ? "Fire TV · Miracast audio and video"
                  : "Miracast display · audio and video"
                selected: root.receiverId === String(modelData.id)
                hasCursor: root.keyboardCursor && root.receiverCursor === index
                enabled: !root.sessionBusy
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.selectAndConnect(modelData)
              }
            }

            Text {
              visible: !root.scanRunning && root.receivers.length === 0
              width: parent.width
              text: "Open Display Mirroring on the TV, then rescan."
              textFormat: Text.PlainText
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            BorderSurface {
              width: parent.width
              visible: (root.launchPlan.warnings || []).length > 0
              implicitHeight: warningText.implicitHeight + Style.space(16)
              color: Style.selectedFillFor(root.foreground, Color.accent)
              borderSpec: Border.flat(Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.18), 1)
              radius: Style.cornerRadius
              Text {
                id: warningText
                anchors.fill: parent
                anchors.margins: Style.space(8)
                text: root.launchPlan.warnings ? root.launchPlan.warnings.join("\n") : ""
                textFormat: Text.PlainText
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }

            Button {
              width: parent.width
              visible: root.sessionBusy
              text: "Cancel connection <span style=\"color:" + root.muted + "\">(Q)</span>"
              enabled: root.sessionBusy
              bordered: true
              selected: root.receiverId !== ""
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.requestStop()
            }
          }

          Column {
            visible: !root.sessionActive && !root.needsRecovery && !root.systemReady()
            width: parent.width
            spacing: Style.space(10)

            InfoPair { label: "System"; value: root.readinessSummary() }
            Text {
              width: parent.width
              text: root.readinessDetail()
              textFormat: Text.PlainText
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
            Button {
              width: parent.width
              text: root.doctorComplete && root.setupRequired() ? "Open setup terminal" : (doctorProc.running ? "Checking…" : "Check again")
              enabled: !doctorProc.running
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.setupRequired() ? root.openSetupTerminal() : root.refresh()
            }
          }

          Column {
            visible: root.needsRecovery
            width: parent.width
            spacing: Style.space(9)

            PanelSectionHeader { text: "SESSION RECOVERY"; foreground: root.foreground; fontFamily: root.fontFamily }
            Text {
              width: parent.width
              text: root.recoveryGuidance()
              textFormat: Text.PlainText
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
            Button {
              width: parent.width
              text: root.recoverRunning ? "Restoring…" : "Restore casting state"
              enabled: !root.recoverRunning
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.requestRecovery()
            }
          }

          Column {
            visible: root.sessionActive
            width: parent.width
            spacing: Style.space(9)

            PanelSectionHeader { text: "LIVE CAST"; foreground: root.foreground; fontFamily: root.fontFamily }
            InfoPair { label: "Display"; value: root.receiverName || "Connected Miracast display" }
            InfoPair { label: "Session"; value: root.elapsedStatus() }
            InfoPair { label: "Quality"; value: root.qualityStatus() }
            InfoPair { label: "Health"; value: root.healthStatus() }

            Button {
              width: parent.width
              text: (root.nerdMode ? "Hide Nerd Mode" : "Show Nerd Mode")
                + " <span style=\"color:" + root.muted + "\">(N)</span>"
              tooltipText: "Advanced live stats · no extra packet tracing"
              bordered: true
              selected: root.nerdMode
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.nerdMode = !root.nerdMode
            }

            Column {
              visible: root.nerdMode
              width: parent.width
              spacing: Style.space(7)

              PanelSectionHeader { text: "NERD MODE"; foreground: root.foreground; fontFamily: root.fontFamily }
              Grid {
                width: parent.width
                columns: 2
                columnSpacing: Style.space(8)
                rowSpacing: Style.space(8)
                NerdMetric { width: (parent.width - parent.columnSpacing) / 2; label: "FPS · ACTUAL / MUX"; value: root.cadenceStatus(); valueColor: root.cadenceColor() }
                NerdMetric { width: (parent.width - parent.columnSpacing) / 2; label: "REALTIME · DROP / DUP"; value: root.pacingStatus(); valueColor: root.pacingColor() }
                NerdMetric { width: (parent.width - parent.columnSpacing) / 2; label: "CPU · CAP / MUX"; value: root.pipelineStatus(); valueColor: root.pipelineColor() }
                NerdMetric { width: (parent.width - parent.columnSpacing) / 2; label: "DELAY · CAP / MUX"; value: root.schedulerStatus(); valueColor: root.schedulerColor() }
                NerdMetric { width: (parent.width - parent.columnSpacing) / 2; label: "RTP · RATE / QUEUE"; value: root.transportStatus(); valueColor: root.transportColor() }
                NerdMetric { width: (parent.width - parent.columnSpacing) / 2; label: "WI-FI · SIGNAL / LINK"; value: root.radioStatus(); valueColor: root.radioColor() }
                NerdMetric { width: (parent.width - parent.columnSpacing) / 2; label: "SESSION PEAKS"; value: root.maximaStatus(); valueColor: root.maximaColor() }
                NerdMetric { visible: root.packetProbeActive(); width: (parent.width - parent.columnSpacing) / 2; label: "PACKET GAP · V / A"; value: root.packetTimingStatus(); valueColor: root.packetColor() }
                NerdMetric { visible: root.packetProbeActive(); width: (parent.width - parent.columnSpacing) / 2; label: "A/V SKEW"; value: root.avStatus(); valueColor: root.avColor() }
              }
            }

            Button {
              width: parent.width
              text: root.phase === "stopping" ? "Stopping…"
                : "Stop casting <span style=\"color:" + root.muted + "\">(Q)</span>"
              enabled: root.phase !== "stopping"
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.requestStop()
            }
          }
        }
      }
    }
  }

  component InfoPair: Row {
    property string label: ""
    property string value: ""
    width: parent.width
    spacing: Style.space(8)
    Text { text: parent.label; textFormat: Text.PlainText; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall }
    Item { width: Math.max(0, parent.width - parent.children[0].implicitWidth - parent.children[2].implicitWidth - parent.spacing * 2); height: 1 }
    Text { text: parent.value; textFormat: Text.PlainText; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; elide: Text.ElideRight; width: Math.min(implicitWidth, Style.space(245)) }
  }

  // StdioCollector has no size ceiling and buffers the complete stream. An
  // empty-marker SplitParser emits each available chunk without retaining it;
  // this component keeps only the controller contract's bounded response.
  component BoundedCollector: SplitParser {
    property string output: ""
    property bool overflow: false
    splitMarker: ""

    function reset() {
      output = ""
      overflow = false
    }

    onRead: function(data) {
      if (overflow) return
      var remaining = root.maxControllerResponseChars - output.length
      if (data.length > remaining) {
        output = ""
        overflow = true
      } else {
        output += data
      }
    }
  }

  component DiscardCollector: SplitParser {
    splitMarker: ""
    onRead: function(data) {}
  }

  component ReceiverButton: Button {
    property string labelText: ""
    implicitHeight: receiverLabel.implicitHeight + verticalPadding * 2 + Style.normalBorderWidth * 2
    Text {
      id: receiverLabel
      anchors.centerIn: parent
      width: Math.max(0, parent.width - parent.horizontalPadding * 2)
      text: parent.labelText
      textFormat: Text.PlainText
      color: parent.foreground
      font.family: parent.fontFamily
      font.pixelSize: parent.fontSize
      font.bold: parent.selected
      horizontalAlignment: Text.AlignHCenter
      elide: Text.ElideRight
    }
  }

  component NerdMetric: BorderSurface {
    property string label: ""
    property string value: ""
    property color valueColor: root.foreground
    implicitHeight: metricColumn.implicitHeight + Style.space(16)
    color: Style.selectedFillFor(root.foreground, Color.accent)
    borderSpec: Border.flat(Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12), 1)
    radius: Style.cornerRadius
    Column {
      id: metricColumn
      anchors.fill: parent
      anchors.margins: Style.space(8)
      spacing: Style.space(3)
      Text { text: parent.parent.label; textFormat: Text.PlainText; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
      Text { width: parent.width; text: parent.parent.value; textFormat: Text.PlainText; color: parent.parent.valueColor; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; elide: Text.ElideRight }
    }
  }
}
