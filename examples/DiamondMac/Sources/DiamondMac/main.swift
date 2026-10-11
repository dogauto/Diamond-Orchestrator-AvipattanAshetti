import Foundation
import SwiftUI

struct ProviderStatus: Decodable, Identifiable {
    let provider: String
    let status: String
    let version: String?
    let auth_status: String

    var id: String { provider }
}

struct ActivityItem: Identifiable {
    let id = UUID()
    let provider: String
    let message: String
    let symbol: String
}

struct ProjectStatus: Decodable {
    let providers: [ProviderStatus]
    let workflows: [WorkflowStatus]
    let deliveries: [DeliveryStatus]
}

struct WorkflowStatus: Decodable, Identifiable {
    struct Spec: Decodable {
        let workflow_id: String
        let objective: String
    }

    let spec: Spec
    let status: String
    let revision: Int
    let detail: String

    var id: String { spec.workflow_id }
}

struct DeliveryStatus: Decodable, Identifiable {
    struct Spec: Decodable {
        let delivery_id: String
        let objective: String
    }

    let spec: Spec
    let status: String
    let revision: Int
    let detail: String

    var id: String { spec.delivery_id }
}

struct Envelope: Decodable {
    let ok: Bool
    let result: ProjectStatus?
    let error: ErrorPayload?
}

struct WorkflowEnvelope: Decodable {
    let ok: Bool
    let result: WorkflowResult?
    let error: ErrorPayload?
}

struct WorkflowResult: Decodable {
    let workflow: WorkflowStatus
}

struct WorkflowEvent: Identifiable {
    let id: String
    let revision: Int
    let eventType: String
    let timestamp: String
    let commandID: String
    let detail: String
}

struct ErrorPayload: Decodable {
    let code: String
    let message: String
}

@MainActor
final class DiamondClient: ObservableObject {
    @Published private(set) var providers: [ProviderStatus] = []
    @Published private(set) var workflows: [WorkflowStatus] = []
    @Published private(set) var deliveries: [DeliveryStatus] = []
    @Published private(set) var activity: [ActivityItem] = []
    @Published private(set) var message = "Connect the controller to inspect project health."
    @Published private(set) var commandOutput = ""
    @Published private(set) var commandMessage = "Try: status, doctor, workflows, or deliveries"
    @Published private(set) var isLoading = false
    @Published private(set) var workflowEvents: [WorkflowEvent] = []
    @Published var eventWorkflowID = ""
    @Published private(set) var eventMessage = "Choose a workflow or enter an ID to inspect its immutable log."
    @Published private(set) var eventError: String?

    private let projectRoot: URL

    init(projectRoot: URL? = nil) {
        self.projectRoot = projectRoot ?? Self.findProjectRoot()
    }

    func refresh() {
        guard !isLoading else { return }
        isLoading = true
        message = "Checking controller health..."
        activity = [ActivityItem(provider: "Diamond", message: "Checking configured provider routes", symbol: "arrow.triangle.2.circlepath")]

        Task {
            defer { isLoading = false }
            do {
                let response = try await run(["project", "status", "--json"])
                let envelope = try JSONDecoder().decode(Envelope.self, from: response)
                if let result = envelope.result {
                    providers = result.providers
                    workflows = result.workflows
                    deliveries = result.deliveries
                    activity = result.providers.map {
                        ActivityItem(
                            provider: $0.provider.capitalized,
                            message: $0.status == "available"
                                ? "Version probe returned \($0.version ?? "an available route")"
                                : "Route reported \($0.status)",
                            symbol: $0.status == "available" ? "checkmark.circle" : "exclamationmark.circle"
                        )
                    }
                    message = envelope.ok ? "Controller responded successfully." : "Controller reported a blocked state."
                } else {
                    message = envelope.error?.message ?? "Controller returned no status."
                }
            } catch {
                message = "Controller unavailable: \(error.localizedDescription)"
            }
        }
    }

    func reconcile(workflow: WorkflowStatus) {
        guard !isLoading else { return }
        isLoading = true
        message = "Reconciling \(workflow.id)..."
        Task {
            var refreshNeeded = false
            defer {
                isLoading = false
                if refreshNeeded { refresh() }
            }
            do {
                let response = try await run([
                    "workflow", "reconcile", workflow.id,
                    "--expected-revision", String(workflow.revision),
                    "--reason", "Explicitly reconciled from the macOS recovery console",
                    "--json"
                ])
                let envelope = try JSONDecoder().decode(WorkflowEnvelope.self, from: response)
                guard envelope.ok, envelope.result != nil else {
                    throw NSError(domain: "DiamondController", code: 1,
                                  userInfo: [NSLocalizedDescriptionKey: envelope.error?.message ?? "Controller rejected reconciliation"])
                }
                message = "Workflow reconciled. Refreshing controller state..."
                refreshNeeded = true
            } catch {
                message = "Recovery action failed: \(error.localizedDescription)"
            }
        }
    }

    func loadEvents(for workflowID: String) {
        let identifier = workflowID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !identifier.isEmpty else {
            eventError = "Enter a workflow ID first."
            eventMessage = "No workflow ID was provided."
            return
        }
        guard !isLoading else { return }
        eventWorkflowID = identifier
        isLoading = true
        eventError = nil
        eventMessage = "Reading immutable events for \(identifier)..."
        Task {
            defer { isLoading = false }
            do {
                let response: Data
                do {
                    response = try await run(["workflow", "events", identifier, "--json"])
                } catch {
                    response = try await run([
                        "--state-dir", Self.selfImproveStateDirectory.path,
                        "self-improve", "events", identifier, "--json"
                    ])
                }
                let object = try jsonObject(response)
                guard let result = object["result"] as? [String: Any],
                      let rawEvents = result["events"] as? [[String: Any]] else {
                    throw NSError(domain: "DiamondController", code: 1,
                                  userInfo: [NSLocalizedDescriptionKey: "Controller returned no event list."])
                }
                workflowEvents = rawEvents.compactMap(decodeEvent)
                eventMessage = workflowEvents.isEmpty
                    ? "The controller has no recorded events for this workflow."
                    : "\(workflowEvents.count) immutable event\(workflowEvents.count == 1 ? "" : "s") loaded."
            } catch {
                workflowEvents = []
                eventError = error.localizedDescription
                eventMessage = "Could not load workflow events. Check the workflow ID and controller state."
            }
        }
    }

    func executeCommand(_ input: String) {
        let words = input.split(whereSeparator: \.isWhitespace).map(String.init)
        guard !words.isEmpty else {
            commandMessage = "Enter a controller command."
            return
        }
        if words == ["build", "music", "player"] {
            guard !isLoading else {
                commandMessage = "The controller is busy. Try again when the current action finishes."
                return
            }
            isLoading = true
            commandMessage = "Building the local music player..."
            Task {
                defer { isLoading = false }
                do {
                    let response = try await runLocal(["music-player"])
                    commandOutput = String(data: response, encoding: .utf8) ?? "Music Player opened."
                    commandMessage = "Music Player opened."
                } catch {
                    commandMessage = "Music Player build failed: \(error.localizedDescription)"
                }
            }
            return
        }
        let command: [String]
        if words == ["status"] {
            command = ["project", "status", "--json"]
        } else if words == ["doctor"] {
            command = ["doctor", "--json"]
        } else if words == ["workflows"] {
            command = ["workflow", "list", "--json"]
        } else if words == ["deliveries"] {
            command = ["delivery", "list", "--json"]
        } else if words.count == 3, words[0] == "run", words[1] == "workflow" {
            command = ["workflow", "run", words[2], "--expected-revision", "1", "--json"]
        } else if words.count == 4, words[0] == "run", words[1] == "workflow" {
            let revision = words[3]
            guard Int(revision) != nil else {
                commandMessage = "Revision must be a number."
                return
            }
            command = ["workflow", "run", words[2], "--expected-revision", revision, "--json"]
        } else {
            commandMessage = "Supported commands: status, doctor, workflows, deliveries, run workflow ID [revision]"
            return
        }

        guard !isLoading else {
            commandMessage = "The controller is busy. Try again when the current action finishes."
            return
        }
        isLoading = true
        commandMessage = "Running \(input)..."
        Task {
            var refreshNeeded = false
            defer {
                isLoading = false
                if refreshNeeded { refresh() }
            }
            do {
                let response = try await run(command)
                commandOutput = Self.prettyJSON(response)
                commandMessage = "Command completed."
                refreshNeeded = command.first == "project"
            } catch {
                commandOutput = ""
                commandMessage = "Command failed: \(error.localizedDescription)"
            }
        }
    }

    private static func prettyJSON(_ data: Data) -> String {
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let formatted = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]),
              let text = String(data: formatted, encoding: .utf8) else {
            return String(data: data, encoding: .utf8) ?? "Controller returned no readable output."
        }
        return text
    }

    private static func findProjectRoot() -> URL {
        let starts = [
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
            URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
        ]
        for start in starts {
            var candidate = start.standardizedFileURL
            while candidate.path != "/" {
                if FileManager.default.fileExists(atPath: candidate.appendingPathComponent("scripts/diamond.sh").path) {
                    return candidate
                }
                candidate.deleteLastPathComponent()
            }
        }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    }

    private static var selfImproveStateDirectory: URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("flawless-diamond-self-improve-state")
    }

    private func run(_ arguments: [String]) async throws -> Data {
        try await Task.detached(priority: .userInitiated) {
            let process = Process()
            let output = Pipe()
            let error = Pipe()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = ["./scripts/diamond.sh"] + arguments
            process.currentDirectoryURL = self.projectRoot
            process.standardOutput = output
            process.standardError = error
            try process.run()
            async let outputData = Task.detached { output.fileHandleForReading.readDataToEndOfFile() }.value
            async let errorData = Task.detached { error.fileHandleForReading.readDataToEndOfFile() }.value
            process.waitUntilExit()
            let (data, errors) = await (outputData, errorData)
            guard process.terminationStatus == 0 else {
                let envelope = try? JSONDecoder().decode(WorkflowEnvelope.self, from: data)
                let details = envelope?.error?.message
                    ?? String(data: errors.isEmpty ? data : errors, encoding: .utf8)
                    ?? "unknown error"
                throw NSError(domain: "DiamondController", code: Int(process.terminationStatus),
                              userInfo: [NSLocalizedDescriptionKey: details])
            }
            return data
        }.value
    }

    private func runLocal(_ arguments: [String]) async throws -> Data {
        try await Task.detached(priority: .userInitiated) {
            let process = Process()
            let output = Pipe()
            let error = Pipe()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = ["./scripts/local.sh"] + arguments
            process.currentDirectoryURL = self.projectRoot
            process.standardOutput = output
            process.standardError = error
            try process.run()
            async let outputData = Task.detached { output.fileHandleForReading.readDataToEndOfFile() }.value
            async let errorData = Task.detached { error.fileHandleForReading.readDataToEndOfFile() }.value
            process.waitUntilExit()
            let (data, errors) = await (outputData, errorData)
            guard process.terminationStatus == 0 else {
                let details = String(data: errors.isEmpty ? data : errors, encoding: .utf8) ?? "unknown error"
                throw NSError(domain: "DiamondLocalLauncher", code: Int(process.terminationStatus),
                              userInfo: [NSLocalizedDescriptionKey: details])
            }
            return data
        }.value
    }
}

private func jsonObject(_ data: Data) throws -> [String: Any] {
    guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw NSError(domain: "DiamondController", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Controller returned invalid JSON."])
    }
    return object
}

private func decodeEvent(_ value: [String: Any]) -> WorkflowEvent? {
    guard let revision = value["revision"] as? Int,
          let eventType = value["event_type"] as? String,
          let timestamp = value["timestamp"] as? String,
          let commandID = value["command_id"] as? String else {
        return nil
    }
    let payload = value["payload"] as? [String: Any]
    let detail = (payload?["detail"] as? String)
        ?? (payload?["reason"] as? String)
        ?? "Recorded controller transition."
    return WorkflowEvent(
        id: "\(revision)-\(commandID)",
        revision: revision,
        eventType: eventType,
        timestamp: timestamp,
        commandID: commandID,
        detail: detail
    )
}

@main
struct DiamondMacApp: App {
    var body: some Scene {
        WindowGroup("Flawless Diamond") {
            DashboardView()
        }
        .windowResizability(.contentSize)
    }
}

struct DashboardView: View {
    @StateObject private var client = DiamondClient()
    @State private var selection = Section.overview
    @State private var headerPhase = false

    private enum Section: String, CaseIterable, Identifiable {
        case overview = "Overview"
        case workflows = "Workflows"
        case providers = "Providers"
        case delivery = "Delivery"
        case recovery = "Recovery"
        case logs = "Logs"
        case settings = "Settings"

        var id: String { rawValue }

        var icon: String {
            switch self {
            case .overview: "square.grid.2x2"
            case .workflows: "arrow.triangle.branch"
            case .providers: "server.rack"
            case .delivery: "checkmark.seal"
            case .recovery: "arrow.clockwise"
            case .logs: "list.bullet.rectangle"
            case .settings: "slider.horizontal.3"
            }
        }
    }

    var body: some View {
        NavigationSplitView {
            List(Section.allCases, selection: $selection) { item in
                Label(item.rawValue, systemImage: item.icon)
                    .tag(item)
            }
            .listStyle(.sidebar)
            .safeAreaInset(edge: .bottom) {
                sidebarFooter
            }
            .navigationTitle("Diamond")
        } detail: {
            detailView
        }

        .navigationSplitViewColumnWidth(min: 190, ideal: 220, max: 260)
        .task {
            client.refresh()
            withAnimation(.spring(response: 0.8, dampingFraction: 0.72).delay(0.2)) {
                headerPhase = true
            }
        }
    }

    @ViewBuilder
    private var detailView: some View {
        switch selection {
        case .overview:
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    header
                    summary
                    controllerCard
                    commandCenter
                    activitySection
                    providerSection
                }
                .padding(.horizontal, 36)
                .padding(.vertical, 32)
                .frame(maxWidth: 980, alignment: .leading)
            }
        case .providers:
            ScrollView { providerSection.padding(36).frame(maxWidth: 980, alignment: .leading) }
        case .workflows:
            ScrollView { workflowSection.padding(36).frame(maxWidth: 980, alignment: .leading) }
        case .delivery:
            ScrollView { deliverySection.padding(36).frame(maxWidth: 980, alignment: .leading) }
        case .recovery:
            ScrollView { recoverySection.padding(36).frame(maxWidth: 980, alignment: .leading) }
        case .logs:
            ScrollView { logsSection.padding(36).frame(maxWidth: 980, alignment: .leading) }
        case .settings:
            ScrollView { settingsSection.padding(36).frame(maxWidth: 980, alignment: .leading) }
        }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Project control")
                    .font(.system(size: 38, weight: .semibold, design: .rounded))
                    .tracking(-0.8)
                Text("A calm command surface for building, reviewing, and releasing software.")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: "diamond.fill")
                .font(.system(size: 24, weight: .medium))
                .foregroundStyle(.tint)
                .frame(width: 54, height: 54)
                .background(.tint.opacity(0.12), in: Circle())
                .scaleEffect(headerPhase ? 1 : 0.72)
                .rotationEffect(.degrees(headerPhase ? 0 : -12))
                .shadow(color: .accentColor.opacity(headerPhase ? 0.22 : 0), radius: 18)
                .animation(.spring(response: 0.65, dampingFraction: 0.68), value: headerPhase)
        }
    }

    private var summary: some View {
        HStack(spacing: 12) {
            SummaryTile(
                title: "Controller",
                value: client.isLoading ? "Checking" : "Online",
                detail: "Local JSON bridge",
                icon: "bolt.horizontal.circle",
                tint: .green
            )
            SummaryTile(
                title: "Providers",
                value: "\(client.providers.count)",
                detail: "Configured routes",
                icon: "server.rack",
                tint: .blue
            )
            SummaryTile(
                title: "Auth",
                value: authSummary,
                detail: "Credentials",
                icon: "lock.shield",
                tint: .orange
            )
        }
    }

    private var authSummary: String {
        guard !client.providers.isEmpty else { return "Unknown" }
        let checked = client.providers.filter { $0.auth_status != "NOT_CHECKED" }
        return checked.isEmpty ? "Not checked" : "\(checked.count) checked"
    }

    private var sidebarFooter: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider()
            Label("Local controller", systemImage: "lock.fill")
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
            Text("The app never calls providers directly.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
    }

    private var controllerCard: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) {
                Image(systemName: client.isLoading ? "arrow.triangle.2.circlepath" : "checkmark.circle.fill")
                    .font(.title2)
                    .foregroundStyle(client.isLoading ? Color.secondary : Color.green)
                VStack(alignment: .leading, spacing: 3) {
                    Text(client.isLoading ? "Checking controller" : "Controller connected")
                        .font(.headline)
                    Text(client.message)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                Spacer()
                Circle()
                    .fill(client.isLoading ? .orange : .green)
                    .frame(width: 9, height: 9)
                    .accessibilityHidden(true)
            }

            Divider()

            HStack {
                Label("Project workspace", systemImage: "folder")
                    .foregroundStyle(.secondary)
                Spacer()
                Text("flawless-diamond-piece")
                    .font(.callout.weight(.medium))
            }

            Button {
                client.refresh()
            } label: {
                Label("Refresh status", systemImage: "arrow.clockwise")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut("r", modifiers: [.command])
            .disabled(client.isLoading)
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.08), radius: 22, y: 10)
    }

    private var commandCenter: some View {
        CommandCenter(
            message: client.commandMessage,
            output: client.commandOutput,
            isLoading: client.isLoading,
            onExecute: client.executeCommand
        )
    }

    private var providerSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Provider routes")
                        .font(.title2.weight(.semibold))
                    Text("Version probes from the Diamond controller.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text("\(client.providers.filter { $0.status == "available" }.count)/\(client.providers.count) ready")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            if client.providers.isEmpty {
                ContentUnavailableView(
                    "No provider data",
                    systemImage: "server.rack",
                    description: Text("Refresh the controller to inspect configured CLIs.")
                )
                .frame(maxWidth: .infinity, minHeight: 160)
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 18))
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), spacing: 12)], spacing: 12) {
                    ForEach(client.providers) { provider in
                        ProviderCard(provider: provider)
                    }
                }
            }
        }
    }

    private var activitySection: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("AI activity")
                        .font(.title2.weight(.semibold))
                    Text("Truthful controller events, not invented chat.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if client.isLoading {
                    ProgressView()
                        .controlSize(.small)
                }
            }

            if client.activity.isEmpty {
                HStack(spacing: 12) {
                    Image(systemName: "bubble.left.and.bubble.right")
                        .foregroundStyle(.secondary)
                    Text("No provider conversation is active. Start a workflow to see real role activity here.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 16))
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(client.activity.enumerated()), id: \.element.id) { index, item in
                        ActivityRow(item: item, index: index, isLoading: client.isLoading)
                        if index < client.activity.count - 1 {
                            Divider().padding(.leading, 42)
                        }
                    }
                }
                .padding(.horizontal, 14)
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 18))
            }
        }
    }

    private var workflowSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            sectionTitle("Workflows", subtitle: "Bounded Builder and Debugger work owned by the controller.")
            if client.workflows.isEmpty {
                emptyState("No workflows", detail: "AI-created workflows will appear here after the controller records them.", icon: "arrow.triangle.branch")
            } else {
                ForEach(client.workflows) { workflow in
                    WorkflowRow(workflow: workflow)
                }
            }
        }
    }

    private var deliverySection: some View {
        VStack(alignment: .leading, spacing: 14) {
            sectionTitle("Delivery", subtitle: "Candidate integration, QA, review, and release state.")
            if client.deliveries.isEmpty {
                emptyState("No deliveries", detail: "Accepted workflow candidates will appear here.", icon: "checkmark.seal")
            } else {
                ForEach(client.deliveries) { delivery in
                    DeliveryRow(delivery: delivery)
                }
            }
        }
    }

    private var recoverySection: some View {
        VStack(alignment: .leading, spacing: 14) {
            sectionTitle("Recovery", subtitle: "Quarantined work stays visible until an explicit reconciliation action.")
            let blockedWorkflows = client.workflows.filter { $0.status == "BLOCKED" || $0.status == "EXHAUSTED" }
            if blockedWorkflows.isEmpty {
                emptyState("No blocked workflows", detail: "The controller has no visible workflow requiring reconciliation.", icon: "checkmark.shield")
            } else {
                ForEach(blockedWorkflows) { workflow in
                    WorkflowRow(workflow: workflow, onReconcile: { selected in
                        client.reconcile(workflow: selected)
                    })
                }
            }
        }
    }

    private var logsSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionTitle("Execution logs", subtitle: "Immutable controller events for workflows and gated self-improvement.")

            HStack(spacing: 10) {
                TextField("Workflow ID", text: $client.eventWorkflowID)
                    .textFieldStyle(.roundedBorder)
                Button("Load log") {
                    client.loadEvents(for: client.eventWorkflowID)
                }
                .buttonStyle(.borderedProminent)
                .disabled(client.eventWorkflowID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || client.isLoading)
            }

            if !client.workflows.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Project workflows")
                        .font(.headline)
                    ForEach(client.workflows) { workflow in
                        Button {
                            client.loadEvents(for: workflow.id)
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(workflow.spec.objective).lineLimit(1)
                                    Text(workflow.id)
                                        .font(.caption.monospaced())
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                StatusBadge(text: workflow.status)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .padding(12)
                        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 12))
                    }
                }
            }

            Text(client.eventMessage)
                .font(.subheadline)
                .foregroundStyle(client.eventError == nil ? Color.secondary : Color.red)

            if client.isLoading && !client.workflowEvents.isEmpty {
                ProgressView("Refreshing log…")
            } else if client.workflowEvents.isEmpty {
                ContentUnavailableView(
                    "No events loaded",
                    systemImage: "list.bullet.rectangle",
                    description: Text("Enter a workflow ID or choose a project workflow above. Self-improvement logs use the same workflow ID.")
                )
                .frame(maxWidth: .infinity, minHeight: 220)
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 18))
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(client.workflowEvents.enumerated()), id: \.element.id) { index, event in
                        WorkflowEventRow(event: event)
                        if index < client.workflowEvents.count - 1 {
                            Divider().padding(.leading, 42)
                        }
                    }
                }
                .padding(.horizontal, 14)
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 18))
            }
        }
    }

    private var settingsSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            sectionTitle("Settings", subtitle: "Configuration remains owned by the Python controller.")
            HStack(spacing: 12) {
                Image(systemName: "lock.shield")
                    .foregroundStyle(.orange)
                Text("Provider routing, timeouts, workspace ownership, and safety policy are read from config/diamond.toml. This client does not edit them yet.")
                    .foregroundStyle(.secondary)
            }
            .padding(18)
            .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 16))
        }
    }

    private func sectionTitle(_ title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(size: 32, weight: .semibold, design: .rounded))
            Text(subtitle).font(.title3).foregroundStyle(.secondary)
        }
    }

    private func emptyState(_ title: String, detail: String, icon: String) -> some View {
        ContentUnavailableView(title, systemImage: icon, description: Text(detail))
            .frame(maxWidth: .infinity, minHeight: 220)
            .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 18))
    }
}

private struct WorkflowRow: View {
    let workflow: WorkflowStatus
    let onReconcile: ((WorkflowStatus) -> Void)?
    @State private var showingConfirmation = false

    init(workflow: WorkflowStatus, onReconcile: ((WorkflowStatus) -> Void)? = nil) {
        self.workflow = workflow
        self.onReconcile = onReconcile
    }

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: workflow.status == "VERIFIED" ? "checkmark.circle.fill" : "arrow.triangle.branch")
                .foregroundStyle(workflow.status == "VERIFIED" ? Color.green : Color.accentColor)
                .font(.title3)
            VStack(alignment: .leading, spacing: 4) {
                Text(workflow.spec.objective).font(.headline).lineLimit(2)
                Text("Revision \(workflow.revision) • \(workflow.id)")
                    .font(.caption).foregroundStyle(.secondary)
                if !workflow.detail.isEmpty {
                    Text(workflow.detail).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 8) {
                StatusBadge(text: workflow.status)
                if workflow.status == "BLOCKED", onReconcile != nil {
                    Button("Reconcile") {
                        showingConfirmation = true
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .confirmationDialog(
                        "Reconcile this blocked workflow?",
                        isPresented: $showingConfirmation,
                        titleVisibility: .visible
                    ) {
                        Button("Reconcile Workflow", role: .destructive) {
                            onReconcile?(workflow)
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("This records an explicit recovery decision. It does not relaunch the provider or overwrite files.")
                    }
                }
            }
        }
        .padding(16)
        .background(.background, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay { RoundedRectangle(cornerRadius: 16).stroke(Color.primary.opacity(0.08)) }
    }
}

private struct WorkflowEventRow: View {
    let event: WorkflowEvent

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text(String(event.revision))
                .font(.caption.weight(.semibold).monospacedDigit())
                .frame(width: 28, height: 28)
                .background(Color.accentColor.opacity(0.12), in: Circle())
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(event.eventType.replacingOccurrences(of: "_", with: " "))
                        .font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(event.timestamp)
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                }
                Text(event.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                Text("Command \(event.commandID)")
                    .font(.caption2.monospaced())
                    .foregroundStyle(.tertiary)
                    .textSelection(.enabled)
            }
        }
        .padding(.vertical, 13)
    }
}

private struct DeliveryRow: View {
    let delivery: DeliveryStatus

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: delivery.status == "ACCEPTED" ? "checkmark.seal.fill" : "checkmark.seal")
                .foregroundStyle(delivery.status == "ACCEPTED" ? Color.green : Color.accentColor)
                .font(.title3)
            VStack(alignment: .leading, spacing: 4) {
                Text(delivery.spec.objective).font(.headline).lineLimit(2)
                Text("Revision \(delivery.revision) • \(delivery.id)")
                    .font(.caption).foregroundStyle(.secondary)
                if !delivery.detail.isEmpty {
                    Text(delivery.detail).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            StatusBadge(text: delivery.status)
        }
        .padding(16)
        .background(.background, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay { RoundedRectangle(cornerRadius: 16).stroke(Color.primary.opacity(0.08)) }
    }
}

private struct StatusBadge: View {
    let text: String

    var body: some View {
        let isSuccess = text == "VERIFIED" || text == "ACCEPTED"
        Text(text.replacingOccurrences(of: "_", with: " "))
            .font(.caption.weight(.semibold))
            .foregroundStyle(isSuccess ? Color.green : Color.orange)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background((isSuccess ? Color.green : Color.orange).opacity(0.13), in: Capsule())
    }
}

private struct CommandCenter: View {
    let message: String
    let output: String
    let isLoading: Bool
    let onExecute: (String) -> Void
    @State private var input = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Command Center")
                    .font(.title2.weight(.semibold))
                Text("Send safe controller commands without using the terminal.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 10) {
                TextField("status", text: $input)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { submit() }
                Button("Run") { submit() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.return, modifiers: [])
                    .disabled(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isLoading)
            }

            Text(message)
                .font(.caption)
                .foregroundStyle(.secondary)

            if !output.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(output)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(12)
                }
                .frame(maxWidth: .infinity, maxHeight: 190, alignment: .leading)
                .background(Color.black.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
            }

            Text("Examples: status  •  doctor  •  build music player  •  run workflow ID 1")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private func submit() {
        let command = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !command.isEmpty else { return }
        onExecute(command)
    }
}

private struct SummaryTile: View {
    let title: String
    let value: String
    let detail: String
    let icon: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: icon)
                    .foregroundStyle(tint)
                Spacer()
                Text(title.uppercased())
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.tertiary)
            }
            Text(value)
                .font(.title3.weight(.semibold))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

private struct ProviderCard: View {
    let provider: ProviderStatus

    private var isAvailable: Bool {
        provider.status == "available"
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: isAvailable ? "checkmark" : "exclamationmark")
                .font(.caption.weight(.bold))
                .foregroundStyle(isAvailable ? .green : .orange)
                .frame(width: 28, height: 28)
                .background((isAvailable ? Color.green : Color.orange).opacity(0.14), in: Circle())
            VStack(alignment: .leading, spacing: 4) {
                Text(provider.provider.capitalized)
                    .font(.headline)
                Text(provider.version ?? "Version unavailable")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(isAvailable ? "Ready" : provider.status.capitalized)
                .font(.caption.weight(.semibold))
                .foregroundStyle(isAvailable ? .green : .orange)
        }
        .padding(15)
        .background(.background, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(Color.primary.opacity(0.07), lineWidth: 1)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct ActivityRow: View {
    let item: ActivityItem
    let index: Int
    let isLoading: Bool
    @State private var isVisible = false

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: item.symbol)
                .foregroundStyle(isLoading && index == 0 ? Color.orange : Color.green)
                .frame(width: 28, height: 28)
                .background(Color.primary.opacity(0.07), in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(item.provider)
                    .font(.caption.weight(.semibold))
                Text(item.message)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            Spacer()
            if isLoading && index == 0 {
                Circle()
                    .fill(.orange)
                    .frame(width: 7, height: 7)
                    .scaleEffect(isVisible ? 1.25 : 0.7)
                    .animation(.spring(response: 0.45, dampingFraction: 0.55).repeatForever(autoreverses: true), value: isVisible)
            }
        }
        .padding(.vertical, 13)
        .opacity(isVisible ? 1 : 0)
        .offset(y: isVisible ? 0 : 8)
        .task {
            try? await Task.sleep(nanoseconds: UInt64(index) * 55_000_000)
            withAnimation(.spring(response: 0.5, dampingFraction: 0.82)) {
                isVisible = true
            }
        }
    }
}
