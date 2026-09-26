// The Campus Captions front end: a menu bar item and a real window.
//
// The operator UI itself is the same HTML the phone and wall viewers use -
// 6,700 lines of it, driven by the relay's websockets - so this hosts it in a
// WKWebView rather than reimplementing it. A web view in a window is not on its
// own a Mac app, though, so everything around it is done deliberately: a real
// menu bar with working Edit commands routed into the web view, window frames
// that survive a relaunch, standard shortcuts, the system About panel, and a
// status item that says what the engine is actually doing.
//
// The engine - relay plus capture client - is a separate process (macapp/
// engine.py, frozen into Contents/Resources/engine). It prints one JSON line
// when it is listening and this reads the port and operator token from that.
// Keeping it out of process means a Python traceback cannot take the menu bar
// with it, and the shell can show the crash instead of vanishing.

import AppKit
import CryptoKit
import WebKit

// MARK: - Engine

/// The relay + capture process, and what it told us on the way up.
final class Engine {
    private var process: Process?
    private(set) var port = 8000
    private(set) var token = ""
    private(set) var movedFrom: Int?

    var isRunning: Bool { process?.isRunning ?? false }

    /// Called on the main queue once the engine reports a port, or with a
    /// message if it died first.
    func start(onReady: @escaping (String?) -> Void) {
        guard let binary = Bundle.main.url(forResource: "engine", withExtension: nil)?
            .appendingPathComponent("Campus Captions Engine"),
              FileManager.default.isExecutableFile(atPath: binary.path) else {
            onReady("The engine is missing from this copy of the app. "
                    + "Reinstall Campus Captions.")
            return
        }

        let task = Process()
        task.executableURL = binary
        let out = Pipe()
        task.standardOutput = out
        // stderr is left attached so a Python traceback still reaches the
        // system log, where the Logs folder menu item can find it.
        do { try task.run() } catch {
            onReady("The engine would not start: \(error.localizedDescription)")
            return
        }
        process = task

        // readabilityHandler rather than a blocking read(upToCount:) on a
        // background queue: the blocking form never returns for this child even
        // though the same process writes to a shell pipe immediately, and the
        // symptom is silent - the menu bar appears and no window ever does.
        let handle = out.fileHandleForReading
        var buffer = Data()
        var settled = false
        let finish: (String?) -> Void = { [weak self] failure in
            guard !settled else { return }
            settled = true
            handle.readabilityHandler = nil
            DispatchQueue.main.async {
                if let failure = failure { onReady(failure) }
                else { self?.accept(buffer, onReady) }
            }
        }

        handle.readabilityHandler = { h in
            let chunk = h.availableData
            if chunk.isEmpty {                      // EOF: it died first
                finish("The engine stopped before it was ready. "
                       + "Check Help ▸ Show Logs in Finder for what it said.")
                return
            }
            buffer.append(chunk)
            if let newline = buffer.firstIndex(of: 0x0A) {
                buffer = buffer[..<newline]
                finish(nil)
            }
        }

        // A bound on how long "starting" is allowed to last. Without it a
        // wedged engine leaves the app looking like it launched and did
        // nothing, which is the hardest kind of failure to report.
        DispatchQueue.main.asyncAfter(deadline: .now() + Engine.readyTimeout) {
            finish("The engine did not finish starting within "
                   + "\(Int(Engine.readyTimeout)) seconds. "
                   + "Check Help ▸ Show Logs in Finder.")
        }
    }

    static let readyTimeout: TimeInterval = 60

    private func accept(_ line: Data, _ onReady: (String?) -> Void) {
        guard let json = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else {
            onReady("The engine sent something unreadable on startup.")
            return
        }
        if let error = json["error"] as? String {
            onReady(error == "no free port"
                    ? "Port \(port) and the twenty above it are all in use on this Mac. "
                      + "Close whatever is using them and open Campus Captions again."
                    : error)
            return
        }
        port = json["port"] as? Int ?? port
        token = json["token"] as? String ?? ""
        movedFrom = json["moved_from"] as? Int
        onReady(nil)
    }

    func stop() {
        guard let task = process, task.isRunning else { return }
        task.terminate()
        // Give the relay a moment to close its sockets; SIGKILL if it will not.
        let deadline = Date().addingTimeInterval(4)
        while task.isRunning && Date() < deadline { usleep(50_000) }
        if task.isRunning { kill(task.processIdentifier, SIGKILL) }
    }

    func url(_ path: String, signedIn: Bool = false) -> URL {
        var text = "http://127.0.0.1:\(port)\(path)"
        if signedIn && !token.isEmpty { text += "?token=\(token)" }
        return URL(string: text)!
    }
}

// MARK: - Window

/// One operator page in a real window.
///
/// Frames autosave per page name, so Status and Settings remember their own
/// size and position independently and both survive a relaunch - losing a
/// window layout on quit is one of the things that makes an app feel foreign.
final class PageWindowController: NSWindowController, NSWindowDelegate, WKNavigationDelegate {
    let path: String
    private let engine: Engine
    private var webView: WKWebView!

    init(path: String, title: String, engine: Engine, size: NSSize) {
        self.path = path
        self.engine = engine

        let window = NSWindow(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = title
        window.tabbingMode = .disallowed        // these are not documents
        window.isReleasedWhenClosed = false     // reopened from the menu
        window.minSize = NSSize(width: 720, height: 480)
        // Named per page, so Settings does not inherit the dashboard's frame.
        window.setFrameAutosaveName("cc.\(path.replacingOccurrences(of: "/", with: ""))")

        super.init(window: window)
        window.delegate = self

        let config = WKWebViewConfiguration()
        // The pages talk to 127.0.0.1 over plain HTTP; nothing else is reachable
        // from them, and the relay sets its own CSP.
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.allowsBackForwardNavigationGestures = false   // not a browser
        webView.setValue(false, forKey: "drawsBackground")
        window.contentView = webView
        if window.frameAutosaveName.isEmpty { window.center() }
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    func load(signedIn: Bool = true) {
        webView.load(URLRequest(url: engine.url(path, signedIn: signedIn)))
    }

    func reload() { webView.reload() }

    /// Anything that is not one of our own pages belongs in the user's browser,
    /// not in this window. An operator who clicks the GitHub link in About
    /// should not end up with the project's README where their dashboard was,
    /// and no way back.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel); return
        }
        if url.host == "127.0.0.1" || url.host == "localhost" || url.isFileURL {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        presentLoadFailure(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        presentLoadFailure(error)
    }

    private func presentLoadFailure(_ error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        guard let window = window else { return }
        let alert = NSAlert()
        alert.messageText = "Campus Captions could not load this page."
        alert.informativeText = "\(error.localizedDescription)\n\n"
            + "The engine may still be starting. Try View > Reload."
        alert.addButton(withTitle: "Reload")
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window) { [weak self] response in
            if response == .alertFirstButtonReturn { self?.reload() }
        }
    }
}

// MARK: - Menu bar

/// The real thing, not a decorative stub.
///
/// Edit is not hand-rolled: the items use the standard first-responder
/// selectors (`cut:`, `copy:`, `paste:`, `selectAll:`, and the Find family), so
/// AppKit routes them down the responder chain into whatever field inside the
/// web view has focus and validates them there. That is what makes Command-C
/// work on a selected API key the way it does everywhere else, and it is also
/// why these items grey out on their own when nothing is selected.
func buildMainMenu(target: AppDelegate) -> NSMenu {
    let main = NSMenu()
    let appName = "Campus Captions"

    func submenu(_ title: String, into parent: NSMenu) -> NSMenu {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let menu = NSMenu(title: title)
        item.submenu = menu
        parent.addItem(item)
        return menu
    }

    // --- App -------------------------------------------------------------
    let app = submenu(appName, into: main)
    app.addItem(withTitle: "About \(appName)",
                action: #selector(AppDelegate.showAbout(_:)), keyEquivalent: "")
        .target = target
    app.addItem(.separator())
    let settings = app.addItem(withTitle: "Settings…",
                               action: #selector(AppDelegate.openSettings(_:)),
                               keyEquivalent: ",")
    settings.target = target
    app.addItem(.separator())

    // Services is a system menu; an app that omits it is quietly refusing to
    // participate in something every other Mac app does.
    let services = NSMenu(title: "Services")
    let servicesItem = app.addItem(withTitle: "Services", action: nil, keyEquivalent: "")
    servicesItem.submenu = services
    NSApp.servicesMenu = services
    app.addItem(.separator())

    app.addItem(withTitle: "Hide \(appName)",
                action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
    let hideOthers = app.addItem(withTitle: "Hide Others",
                                 action: #selector(NSApplication.hideOtherApplications(_:)),
                                 keyEquivalent: "h")
    hideOthers.keyEquivalentModifierMask = [.command, .option]
    app.addItem(withTitle: "Show All",
                action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
    app.addItem(.separator())
    app.addItem(withTitle: "Quit \(appName)",
                action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

    // --- File ------------------------------------------------------------
    // Nothing here opens or saves - this is not a document app - so File holds
    // only what genuinely applies. An empty File menu full of disabled New and
    // Save items would be worse than a short one.
    let file = submenu("File", into: main)
    file.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)),
                 keyEquivalent: "w")
    let export = file.addItem(withTitle: "Export Transcript…",
                              action: #selector(AppDelegate.exportTranscript(_:)),
                              keyEquivalent: "e")
    export.target = target

    // --- Edit ------------------------------------------------------------
    let edit = submenu("Edit", into: main)
    edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
    let redo = edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
    redo.keyEquivalentModifierMask = [.command, .shift]
    edit.addItem(.separator())
    edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
    edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
    edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
    edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)),
                 keyEquivalent: "a")
    edit.addItem(.separator())
    // The web view implements the Find bar itself; these hand it the standard
    // shortcuts rather than inventing new ones.
    let find = NSMenu(title: "Find")
    let findItem = edit.addItem(withTitle: "Find", action: nil, keyEquivalent: "")
    findItem.submenu = find
    let findTag = find.addItem(withTitle: "Find…",
                               action: #selector(NSResponder.performTextFinderAction(_:)),
                               keyEquivalent: "f")
    findTag.tag = NSTextFinder.Action.showFindInterface.rawValue

    // --- View ------------------------------------------------------------
    let view = submenu("View", into: main)
    let reload = view.addItem(withTitle: "Reload",
                              action: #selector(AppDelegate.reloadPage(_:)), keyEquivalent: "r")
    reload.target = target
    view.addItem(.separator())
    view.addItem(withTitle: "Enter Full Screen",
                 action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        .keyEquivalentModifierMask = [.command, .control]

    // --- Window ----------------------------------------------------------
    let window = submenu("Window", into: main)
    window.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)),
                   keyEquivalent: "m")
    window.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)),
                   keyEquivalent: "")
    window.addItem(.separator())
    for (title, path, key) in AppDelegate.pages {
        let item = window.addItem(withTitle: title,
                                  action: #selector(AppDelegate.openPage(_:)), keyEquivalent: key)
        item.target = target
        item.representedObject = path
    }
    window.addItem(.separator())
    window.addItem(withTitle: "Bring All to Front",
                   action: #selector(NSApplication.arrangeInFront(_:)), keyEquivalent: "")
    NSApp.windowsMenu = window

    // --- Help ------------------------------------------------------------
    let help = submenu("Help", into: main)
    let guide = help.addItem(withTitle: "Campus Captions Setup Guide",
                             action: #selector(AppDelegate.openHelp(_:)), keyEquivalent: "?")
    guide.target = target
    let logs = help.addItem(withTitle: "Show Logs in Finder",
                            action: #selector(AppDelegate.showLogs(_:)), keyEquivalent: "")
    logs.target = target
    let config = help.addItem(withTitle: "Show Config Folder in Finder",
                              action: #selector(AppDelegate.showConfig(_:)), keyEquivalent: "")
    config.target = target
    NSApp.helpMenu = help

    return main
}

// MARK: - Activation

/// Per-tester activation.
///
/// The same code that gates update downloads (infra/update-gate) also unlocks
/// the app, because two credential systems for one set of testers is one more
/// than anybody will keep track of. The gate signs a short record with Ed25519
/// and this verifies it against a public key compiled in below, so a code
/// cannot be forged even by somebody who reads this source - only issued.
///
/// What this is honestly for: it stops a build being passed around. It is not
/// proof against someone who edits the bundle, because the check runs on their
/// machine. Signing the app with a Developer ID raises that bar properly -
/// under a hardened runtime an edited bundle will not launch at all.
///
/// The deliberate softness: once activated, a cached record keeps working for
/// GRACE_DAYS without a network. A venue whose wifi is down on a Sunday morning
/// must still be able to caption the service. Revocation therefore takes effect
/// within that window rather than instantly, which is the right trade.
enum Activation {
    static let graceDays = 30.0
    static let host = "updates.gulfvideo.io"

    // Ed25519 public key (base64 raw 32 bytes). The private half lives only in
    // the Worker's secrets.
    //
    // THIS IS THE DEVELOPMENT KEY. Its private half is a throwaway generated
    // while building this, so anyone holding it could sign their own
    // activation. macapp/build.sh refuses to build while it is still here -
    // run macapp/make-activation-keys.sh and paste the real public half in
    // before cutting a release. See docs/ACTIVATION.md.
    static let publicKey = "VNnDLSsu74JRfZLCsgOYANpbnqfLtXAvuGJsDLfGqRU="

    /// A message fit to put in front of an operator, which is all any caller
    /// here needs - nothing branches on the kind of failure.
    struct Failure: Error { let message: String }

    struct Record: Codable {
        let tester: String
        let expires: Double          // seconds since epoch
        let signature: String        // base64, over "tester|expires"
    }

    static var storeURL: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory,
                                               in: .userDomainMask)[0]
        return support.appendingPathComponent("Campus Captions/activation.json")
    }

    static func cached() -> Record? {
        guard let data = try? Data(contentsOf: storeURL),
              let record = try? JSONDecoder().decode(Record.self, from: data),
              verify(record) else { return nil }
        return record
    }

    /// Valid signature *and* not past its grace window.
    static func isActive() -> Bool {
        guard let record = cached() else { return false }
        return Date().timeIntervalSince1970 < record.expires
    }

    static func verify(_ record: Record) -> Bool {
        guard let keyData = Data(base64Encoded: publicKey),
              let sigData = Data(base64Encoded: record.signature),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: keyData) else {
            return false
        }
        let message = Data("\(record.tester)|\(Int(record.expires))".utf8)
        return key.isValidSignature(sigData, for: message)
    }

    /// Exchange a code for a signed record. Network failure is reported as
    /// such, so an offline first run says "no connection" rather than
    /// "wrong code" - telling somebody their code is bad when it is not is how
    /// a support call starts.
    static func activate(code: String, completion: @escaping (Result<Record, Failure>) -> Void) {
        guard var components = URLComponents(string: "https://\(host)/activate") else { return }
        components.queryItems = nil
        var request = URLRequest(url: components.url!)
        request.setValue("Bearer \(code)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 15

        URLSession.shared.dataTask(with: request) { data, response, error in
            let finish: (Result<Record, Failure>) -> Void = { result in
                DispatchQueue.main.async { completion(result) }
            }
            if error != nil {
                finish(.failure(Failure(message: "Could not reach \(host). Check this Mac's "
                    + "internet connection and try again.")))
                return
            }
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard status == 200, let data = data else {
                finish(.failure(Failure(message: status == 401
                    ? "That code was not accepted. It may have been replaced or "
                      + "revoked - ask for a current one."
                    : "The activation server answered \(status).")))
                return
            }
            guard let record = try? JSONDecoder().decode(Record.self, from: data),
                  verify(record) else {
                finish(.failure(Failure(message: "The activation reply could not be verified.")))
                return
            }
            try? FileManager.default.createDirectory(
                at: storeURL.deletingLastPathComponent(),
                withIntermediateDirectories: true)
            try? JSONEncoder().encode(record).write(to: storeURL, options: .atomic)
            // The same code unlocks updates, so hand it to the engine rather
            // than asking the operator to paste it a second time in Settings.
            saveUpdateToken(code)
            finish(.success(record))
        }.resume()
    }

    /// Mirror the code into .env as CAPTIONS_UPDATE_TOKEN, which is where
    /// relay/updater.py already looks.
    private static func saveUpdateToken(_ code: String) {
        let env = storeURL.deletingLastPathComponent().appendingPathComponent(".env")
        var lines = (try? String(contentsOf: env, encoding: .utf8))?
            .components(separatedBy: .newlines) ?? []
        lines.removeAll { $0.hasPrefix("CAPTIONS_UPDATE_TOKEN=") }
        lines.removeAll { $0.isEmpty }
        lines.append("CAPTIONS_UPDATE_TOKEN=\(code)")
        try? (lines.joined(separator: "\n") + "\n")
            .write(to: env, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600],
                                               ofItemAtPath: env.path)
    }
}

// MARK: - Activation sheet

/// Asks for the code. Plain, keyboard-first, and cancellable: Return activates,
/// Escape quits, and the field is focused on open so the operator can paste
/// straight into it.
final class ActivationWindowController: NSWindowController {
    private let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
    private let status = NSTextField(labelWithString: "")
    private let activate = NSButton(title: "Activate", target: nil, action: nil)
    private var onSuccess: (() -> Void)?

    convenience init(onSuccess: @escaping () -> Void) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 440, height: 210),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.title = "Activate Campus Captions"
        self.init(window: window)
        self.onSuccess = onSuccess

        let heading = NSTextField(labelWithString: "Enter your activation code")
        heading.font = .boldSystemFont(ofSize: 15)
        let blurb = NSTextField(wrappingLabelWithString:
            "Campus Captions is in limited beta. The code was sent to you with "
            + "the app; it also keeps this copy up to date.")
        blurb.font = .systemFont(ofSize: 11)
        blurb.textColor = .secondaryLabelColor

        field.placeholderString = "e.g. 3f9a2c8e1b7d4056"
        field.usesSingleLineMode = true
        status.font = .systemFont(ofSize: 11)
        status.textColor = .systemRed

        activate.target = self
        activate.action = #selector(submit)
        activate.keyEquivalent = "\r"            // Return is the default action
        let quit = NSButton(title: "Quit", target: NSApp,
                            action: #selector(NSApplication.terminate(_:)))
        quit.keyEquivalent = "\u{1b}"            // Escape

        let buttons = NSStackView(views: [quit, activate])
        buttons.orientation = .horizontal
        let stack = NSStackView(views: [heading, blurb, field, status, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 20, right: 20)
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView = stack
        NSLayoutConstraint.activate([
            field.widthAnchor.constraint(equalToConstant: 380),
            buttons.widthAnchor.constraint(equalToConstant: 380),
        ])
        window.center()
        window.makeFirstResponder(field)
    }

    @objc private func submit() {
        let code = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !code.isEmpty else { return }
        activate.isEnabled = false
        status.textColor = .secondaryLabelColor
        status.stringValue = "Checking…"
        Activation.activate(code: code) { [weak self] result in
            guard let self = self else { return }
            self.activate.isEnabled = true
            switch result {
            case .success:
                self.close()
                self.onSuccess?()
            case .failure(let failure):
                self.status.textColor = .systemRed
                self.status.stringValue = failure.message
            }
        }
    }
}

// MARK: - Application

@main
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuItemValidation {
    /// Title, path, Command-key. Drives both the Window menu and the status
    /// item, so the two can never drift apart.
    static let pages: [(String, String, String)] = [
        ("Dashboard", "/status", "1"),
        ("Settings", "/settings", "2"),
        ("Wall View", "/wall", "3"),
        ("Phone View", "/phone", "4"),
    ]

    private let engine = Engine()
    private var statusItem: NSStatusItem!
    private var controllers: [String: PageWindowController] = [:]
    private var activationWindow: ActivationWindowController?
    private var pollTimer: Timer?

    /// NSApplication holds its delegate weakly, and ARC will release a local
    /// right after its last use - which is the assignment. The delegate then
    /// dies during launch and every `[weak self]` callback after it silently
    /// does nothing: the menu bar appears, and no window ever opens. Hold it
    /// here so it lives as long as the app does.
    private static var shared: AppDelegate?

    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        shared = delegate
        app.delegate = delegate
        // .accessory, not .prohibited: the app has real windows and belongs in
        // Command-Tab and the Dock while one is open, but should not sit in the
        // Dock doing nothing when they are all closed.
        app.setActivationPolicy(.accessory)
        app.run()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = buildMainMenu(target: self)
        installStatusItem()

        guard Activation.isActive() else {
            NSApp.setActivationPolicy(.regular)
            let window = ActivationWindowController { [weak self] in
                NSApp.setActivationPolicy(.accessory)
                self?.startEngine()
            }
            activationWindow = window
            window.showWindow(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        startEngine()
    }

    private func startEngine() {
        setIcon("inactive")
        engine.start { [weak self] failure in
            guard let self = self else { return }
            if let failure = failure {
                self.setIcon("inactive")
                let alert = NSAlert()
                alert.messageText = "Campus Captions could not start."
                alert.informativeText = failure
                alert.alertStyle = .critical
                alert.runModal()
                return
            }
            if let moved = self.engine.movedFrom, moved != self.engine.port {
                let alert = NSAlert()
                alert.messageText = "Campus Captions is on port \(self.engine.port)."
                alert.informativeText =
                    "Port \(moved) is already used by another program on this Mac. "
                    + "The wall and phone URLs have \(self.engine.port) in them, so a "
                    + "ProPresenter prop or printed QR code made for port \(moved) will "
                    + "not connect until that program is closed — or make the change "
                    + "permanent under Settings ▸ Network."
                alert.runModal()
            }
            self.setIcon("idle")
            self.startPolling()
            self.openPath("/status")
        }
    }

    // MARK: Status item

    private func installStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.toolTip = "Campus Captions"
        let menu = NSMenu()
        for (title, path, key) in AppDelegate.pages {
            let item = menu.addItem(withTitle: title, action: #selector(openPage(_:)),
                                    keyEquivalent: key)
            item.target = self
            item.representedObject = path
        }
        menu.addItem(.separator())
        let about = menu.addItem(withTitle: "About Campus Captions",
                                 action: #selector(showAbout(_:)), keyEquivalent: "")
        about.target = self
        let logs = menu.addItem(withTitle: "Show Logs in Finder",
                                action: #selector(showLogs(_:)), keyEquivalent: "")
        logs.target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Campus Captions",
                     action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        statusItem.menu = menu
    }

    /// idle / streaming / inactive. Template images so the icon inverts with
    /// the menu bar instead of staying dark on a light bar.
    private func setIcon(_ state: String) {
        guard let button = statusItem.button else { return }
        let name = "menubar_\(state)"
        if let url = Bundle.main.url(forResource: name, withExtension: "png"),
           let image = NSImage(contentsOf: url) {
            image.isTemplate = true
            image.size = NSSize(width: 18, height: 18)
            button.image = image
        }
        button.toolTip = "Campus Captions — \(state)"
    }

    private func startPolling() {
        pollTimer?.invalidate()
        let timer = Timer(timeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.pollStatus()
        }
        // .common so the icon keeps updating while a menu is open or a window
        // is being dragged - a status light that freezes mid-service is worse
        // than none.
        RunLoop.main.add(timer, forMode: .common)
        pollTimer = timer
        pollStatus()
    }

    private func pollStatus() {
        var request = URLRequest(url: engine.url("/api/status"))
        request.setValue("Bearer \(engine.token)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            var state = "inactive"
            if let data = data,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                let source = json["source"] as? [String: Any] ?? [:]
                let asr = json["asr"] as? [String: Any] ?? [:]
                if source["error"] is String { state = "inactive" }
                else if asr["stream_open"] as? Bool == true { state = "streaming" }
                else { state = "idle" }
            }
            DispatchQueue.main.async { self?.setIcon(state) }
        }.resume()
    }

    // MARK: Windows

    private func openPath(_ path: String) {
        if let existing = controllers[path] {
            existing.showWindow(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let title = AppDelegate.pages.first { $0.1 == path }?.0 ?? "Campus Captions"
        let size = path == "/settings"
            ? NSSize(width: 1080, height: 820) : NSSize(width: 1180, height: 780)
        let controller = PageWindowController(path: path, title: title,
                                              engine: engine, size: size)
        // Operator surfaces need the token; the audience ones must not have it.
        controller.load(signedIn: path == "/status" || path == "/settings")
        controllers[path] = controller
        controller.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: Actions

    @objc func openPage(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        openPath(path)
    }

    @objc func openSettings(_ sender: Any?) { openPath("/settings") }

    @objc func reloadPage(_ sender: Any?) {
        (NSApp.keyWindow?.windowController as? PageWindowController)?.reload()
    }

    @objc func exportTranscript(_ sender: Any?) {
        // The relay already renders every format; this is the Mac half - a real
        // save panel, so the file lands where the operator chose rather than in
        // Downloads with a generated name.
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "captions.vtt"
        panel.allowedContentTypes = []
        panel.message = "Save the current session transcript."
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url, let self = self else { return }
            let ext = url.pathExtension.isEmpty ? "vtt" : url.pathExtension
            var request = URLRequest(url: self.engine.url("/api/transcript.\(ext)"))
            request.setValue("Bearer \(self.engine.token)", forHTTPHeaderField: "Authorization")
            URLSession.shared.dataTask(with: request) { data, _, _ in
                guard let data = data else { return }
                try? data.write(to: url, options: .atomic)
            }.resume()
        }
    }

    @objc func showAbout(_ sender: Any?) {
        // The system panel, not a hand-rolled alert: it picks up the icon,
        // version and copyright from Info.plist and behaves like every other
        // About box on the Mac.
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Campus Captions",
            NSApplication.AboutPanelOptionKey(rawValue: "Copyright"):
                "Live captioning for campus events.",
        ])
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func openHelp(_ sender: Any?) {
        NSWorkspace.shared.open(
            URL(string: "https://github.com/koakley3/campus-captions/blob/main/SETUP.md")!)
    }

    @objc func showLogs(_ sender: Any?) { reveal("Logs/Campus Captions", in: .libraryDirectory) }
    @objc func showConfig(_ sender: Any?) {
        reveal("Campus Captions", in: .applicationSupportDirectory)
    }

    private func reveal(_ path: String, in directory: FileManager.SearchPathDirectory) {
        let base = FileManager.default.urls(for: directory, in: .userDomainMask)[0]
        let url = base.appendingPathComponent(path)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }

    // MARK: Validation

    /// Menu items that cannot do anything right now say so by greying out,
    /// rather than being clickable and silently doing nothing.
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        switch item.action {
        case #selector(reloadPage(_:)):
            return NSApp.keyWindow?.windowController is PageWindowController
        case #selector(exportTranscript(_:)):
            return engine.isRunning
        case #selector(openPage(_:)), #selector(openSettings(_:)):
            return engine.isRunning
        default:
            return true
        }
    }

    // MARK: Lifecycle

    /// Closing the last window leaves the app running in the menu bar, which is
    /// the point of a menu bar app - the service keeps captioning.
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { false }

    func applicationWillTerminate(_ notification: Notification) {
        pollTimer?.invalidate()
        engine.stop()
    }
}
