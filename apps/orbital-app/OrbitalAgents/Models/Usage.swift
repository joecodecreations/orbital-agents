import Foundation
import SwiftUI

// What AI work costs: tokens, and the share of a subscription plan it used. The same shape comes from
// tracker items (measured by the satellite that ran the agent), satellite jobs, and the scheduler's
// agent automations (`usage_total` adds runs and since). `GET /v1/usage` sums it up.

struct AIUsage: Codable, Equatable, Hashable {
    var engine: String?
    var model: String?
    var inputTokens: Int?
    var cachedTokens: Int?
    var outputTokens: Int?
    var reasoningTokens: Int?
    var costUSD: Double?
    var planPercent: Double?
    var planUsedPercent: Double?
    var planPeriod: String?
    var planResetsAt: String?
    var measuredBy: String?
    var updatedAt: String?
    /// Automations: how many runs these totals cover, and since when.
    var runs: Int?
    var since: String?

    var totalTokens: Int { (inputTokens ?? 0) + (outputTokens ?? 0) + (reasoningTokens ?? 0) }

    static func compact(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 10_000 { return String(format: "%.0fk", Double(n) / 1000) }
        if n >= 1000 { return String(format: "%.1fk", Double(n) / 1000) }
        return "\(n)"
    }

    static func percent(_ value: Double) -> String {
        value >= 10 ? String(format: "%.0f%%", value) : value >= 1 ? String(format: "%.1f%%", value) : String(format: "%.2f%%", value)
    }

    /// "48k tokens · 0.4% of plan"
    var short: String {
        var parts = ["\(Self.compact(totalTokens)) tokens"]
        if let plan = planPercent, plan > 0 { parts.append("\(Self.percent(plan)) of plan") }
        return parts.joined(separator: " · ")
    }

    var breakdown: String {
        var parts: [String] = []
        if let i = inputTokens { parts.append("\(Self.compact(i)) in") }
        if let c = cachedTokens, c > 0 { parts.append("\(Self.compact(c)) cached") }
        if let o = outputTokens { parts.append("\(Self.compact(o)) out") }
        if let r = reasoningTokens, r > 0 { parts.append("\(Self.compact(r)) reasoning") }
        return parts.joined(separator: " · ")
    }

    var engineName: String? {
        guard let engine else { return nil }
        let name = engine.split(separator: "+").map { AgentStyle.displayName(String($0)) }.joined(separator: " + ")
        return model.map { "\(name) · \($0)" } ?? name
    }

    var measuredText: String {
        switch measuredBy {
        case "satellite", "scheduler": return "Measured"
        case "scheduler-estimate": return "Estimated"
        case nil: return "Reported"
        default: return "Reported"
        }
    }

    /// Red when one piece of work took a big bite of the plan.
    var tint: Color {
        let plan = planPercent ?? 0
        if plan >= 5 { return OrbitalTheme.danger }
        if plan >= 2 || totalTokens >= 2_000_000 { return OrbitalTheme.warning }
        return OrbitalTheme.textSecondary
    }
}

struct UsagePlan: Decodable, Equatable, Identifiable {
    var engine: String
    var tier: String?
    var usedPercent: Double
    var period: String?
    var resetsAt: String?
    var id: String { engine }

    enum CodingKeys: String, CodingKey {
        case engine, tier, period
        case usedPercent = "used_percent"
        case resetsAt = "resets_at"
    }
}

struct UsageTop: Decodable, Equatable, Identifiable {
    var kind: String
    var id: String
    var title: String?
    var agent: String?
    var machine: String?
    var status: String?
    var progress: Int?
    var tokens: Int
    var planPercent: Double?
    var runs: Int?

    enum CodingKeys: String, CodingKey {
        case kind, id, title, agent, machine, status, progress, tokens, runs
        case planPercent = "plan_percent"
    }
}

struct UsageTotals: Decodable, Equatable {
    var tokensToday: Int
    var tokensWeek: Int
    var planPercentWeek: Double
    var itemsMeasured: Int

    enum CodingKeys: String, CodingKey {
        case tokensToday = "tokens_today"
        case tokensWeek = "tokens_week"
        case planPercentWeek = "plan_percent_week"
        case itemsMeasured = "items_measured"
    }
}

struct UsageReport: Decodable, Equatable {
    var plans: [UsagePlan]
    var top: [UsageTop]
    var totals: UsageTotals?
}

/// A thin percent-complete bar.
struct ProgressStrip: View {
    let percent: Int
    var tint: Color = OrbitalTheme.accent

    var body: some View {
        HStack(spacing: 8) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.08))
                    Capsule().fill(percent >= 100 ? OrbitalTheme.success : tint)
                        .frame(width: max(4, geo.size.width * CGFloat(min(max(percent, 0), 100)) / 100))
                }
            }
            .frame(height: 5)
            Text("\(percent)%")
                .font(.caption2.weight(.bold).monospacedDigit())
                .foregroundStyle(OrbitalTheme.textSecondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(percent) percent complete")
    }
}

/// Tokens and plan share in one line, with the engine.
struct UsageLine: View {
    let usage: AIUsage
    var showsBreakdown = false

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Label {
                Text([usage.short, usage.engineName].compactMap { $0 }.joined(separator: " · "))
            } icon: {
                Image(systemName: "gauge.with.dots.needle.33percent")
            }
            .font(.caption.weight(.semibold).monospacedDigit())
            .foregroundStyle(usage.tint)
            if showsBreakdown {
                Text([usage.breakdown, usage.measuredText].filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(OrbitalTheme.textTertiary)
                if let used = usage.planUsedPercent {
                    Text("Plan \(AIUsage.percent(used)) used\(usage.planPeriod.map { " this \($0 == "weekly" ? "week" : $0)" } ?? "")\(OrbitalDates.relative(usage.planResetsAt).map { ", resets \($0)" } ?? "")")
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
        }
    }
}
