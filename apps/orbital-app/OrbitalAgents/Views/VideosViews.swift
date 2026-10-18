import Charts
import SwiftUI
import UIKit

// The videos block on its own page: a summary of posts, the links under it, and every post by day.

struct VideosBlockCard: View {
    let block: VideosBlock
    var accent: Color = OrbitalTheme.accent
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @State private var opened: VideoDayEntry?
    @State private var dayLimit = 7

    var body: some View {
        let rows = VideoFeed.rows(block)
        let days = VideoFeed.days(rows)
        VStack(alignment: .leading, spacing: 16) {
            if let title = block.title {
                Text(title)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
            }
            if let note = block.note {
                Text(note)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
            VideoSummaryCard(rows: rows, tint: accent)
            if !block.experiments.isEmpty {
                VideoLinksCard(block: block, accent: accent)
            }
            ForEach(days.prefix(dayLimit)) { day in
                VideoDaySection(day: day) { opened = $0 }
            }
            if days.isEmpty {
                Text("No posts yet.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .orbitalCard(padding: 14)
            }
            if days.count > dayLimit {
                Button("Show earlier days") { withAnimation { dayLimit += 14 } }
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .orbitalCard(padding: 4)
            }
            if let asOf = OrbitalDates.relative(block.asOf) {
                Text("Checked \(asOf)")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
        .sheet(item: $opened) { entry in
            VideoDetailSheet(entry: entry, block: block)
                .environmentObject(store)
                .environmentObject(router)
        }
    }
}

/// Posts today, in 7 and 30 days, and a bar per day stacked by platform.
struct VideoSummaryCard: View {
    let rows: [VideoPostRow]
    let tint: Color

    private struct Bar: Identifiable {
        var id: String { "\(day.timeIntervalSince1970)|\(platform)" }
        var day: Date
        var platform: String
        var count: Int
    }

    var body: some View {
        let calendar = Calendar.current
        let start = calendar.startOfDay(for: Date())
        let live = rows.filter { !$0.post.failed }
        let bars = bars(live, calendar: calendar, start: start)
        let names = platformNames(bars)
        let failed = rows.filter { $0.post.failed && $0.at >= (calendar.date(byAdding: .day, value: -6, to: start) ?? start) }.count
        let checking = rows.filter { $0.post.status == "checking" || ($0.post.status == "posted" && $0.post.link == nil) }.count
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                figure(VideoFeed.count(rows, since: start), "Today")
                figure(VideoFeed.count(rows, since: calendar.date(byAdding: .day, value: -6, to: start) ?? start), "7 days")
                figure(VideoFeed.count(rows, since: calendar.date(byAdding: .day, value: -29, to: start) ?? start), "30 days")
            }
            if !bars.isEmpty {
                Chart(bars) { bar in
                    BarMark(
                        x: .value("Day", bar.day, unit: .day),
                        y: .value("Posts", bar.count)
                    )
                    .foregroundStyle(by: .value("Platform", PlatformStyle.name(bar.platform)))
                    .cornerRadius(3)
                }
                .chartForegroundStyleScale(domain: names, range: names.map { name in
                    PlatformStyle.color(bars.first { PlatformStyle.name($0.platform) == name }?.platform)
                })
                .chartXAxis {
                    AxisMarks(values: .stride(by: .day, count: 3)) { _ in
                        AxisValueLabel(format: .dateTime.day().month(.abbreviated))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { _ in
                        AxisGridLine().foregroundStyle(OrbitalTheme.hairline.opacity(0.5))
                        AxisValueLabel().foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
                .chartLegend(position: .bottom, alignment: .leading, spacing: 8)
                .frame(height: 150)
                .accessibilityLabel("Posts per day for the last 14 days, by platform")
            }
            if let last = live.first {
                Text("Last post \(OrbitalDates.relative(OrbitalDates.iso(last.at)) ?? "") · \(last.video.title)")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
            }
            if checking > 0 || failed > 0 {
                HStack(spacing: 6) {
                    if checking > 0 {
                        OrbitalChip(text: "\(checking) without a link yet", symbol: "link.badge.plus", tint: OrbitalTheme.warning)
                    }
                    if failed > 0 {
                        OrbitalChip(text: "\(failed) didn't post this week", symbol: "xmark.octagon.fill", tint: OrbitalTheme.danger)
                    }
                }
            }
        }
        .orbitalCard(padding: 14, tint: tint)
    }

    private func bars(_ rows: [VideoPostRow], calendar: Calendar, start: Date) -> [Bar] {
        guard let from = calendar.date(byAdding: .day, value: -13, to: start) else { return [] }
        var counts: [Date: [String: Int]] = [:]
        for row in rows where row.at >= from {
            counts[calendar.startOfDay(for: row.at), default: [:]][row.post.platform, default: 0] += 1
        }
        return counts.flatMap { day, byPlatform in
            byPlatform.map { Bar(day: day, platform: $0.key, count: $0.value) }
        }
        .sorted { ($0.day, $0.platform) < ($1.day, $1.platform) }
    }

    private func platformNames(_ bars: [Bar]) -> [String] {
        var seen: [String] = []
        for bar in bars.sorted(by: { $0.platform < $1.platform }) {
            let name = PlatformStyle.name(bar.platform)
            if !seen.contains(name) { seen.append(name) }
        }
        return seen
    }

    private func figure(_ value: Int, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text("\(value)")
                .font(.title3.weight(.bold).monospacedDigit())
                .foregroundStyle(OrbitalTheme.text)
            Text(label)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

/// The links under the videos: a page in the app, a URL, or a prompt for the desk.
struct VideoLinksCard: View {
    let block: VideosBlock
    let accent: Color
    @EnvironmentObject private var router: AppRouter
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(block.experiments.enumerated()), id: \.offset) { index, link in
                if index > 0 {
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                }
                Button {
                    open(link)
                } label: {
                    HStack(spacing: 12) {
                        IconTile(symbol: link.action?.type == "agent" ? "sparkles" : "link", tint: accent, size: 34)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(link.label)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.text)
                            if let detail = link.detail {
                                Text(detail)
                                    .font(.caption)
                                    .foregroundStyle(OrbitalTheme.textSecondary)
                                    .lineLimit(2)
                            }
                        }
                        Spacer(minLength: 0)
                        Image(systemName: link.action == nil ? "arrow.up.right" : "chevron.right")
                            .font(.caption.weight(.bold))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                    .padding(.vertical, 8)
                    .contentShape(Rectangle())
                }
                .buttonStyle(OrbitalPressStyle())
            }
        }
        .orbitalCard(padding: 14)
    }

    private func open(_ link: VideoLink) {
        if let action = link.action {
            switch action.type {
            case "open":
                if let section = action.sectionId {
                    router.show(PageLocation(sectionID: section, nodeID: action.nodeId, tabID: action.tabId), tab: action.tabId)
                }
                return
            case "agent", "desk":
                router.openDesk(prompt: action.prompt)
                return
            default:
                if let raw = action.url, let url = URL(string: raw) { openURL(url) }
                return
            }
        }
        if let raw = link.url, let url = URL(string: raw) { openURL(url) }
    }
}

struct VideoDaySection: View {
    let day: VideoDay
    let onOpen: (VideoDayEntry) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                OrbitalEyebrow(text: TrackerGroup.dayTitle(day.day))
                Spacer(minLength: 0)
                Text("\(day.entries.count) \(day.entries.count == 1 ? "video" : "videos") · \(day.postCount) \(day.postCount == 1 ? "post" : "posts")")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            VStack(spacing: 0) {
                ForEach(Array(day.entries.enumerated()), id: \.element.id) { index, entry in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    VideoRow(entry: entry) { onOpen(entry) }
                }
            }
            .orbitalCard(padding: 12)
        }
    }
}

/// Thumbnail, title, time, and a chip per platform reached that day.
struct VideoRow: View {
    let entry: VideoDayEntry
    let onOpen: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Button(action: onOpen) {
                VideoThumb(src: entry.video.thumbnail, width: 54, height: 72)
            }
            .buttonStyle(OrbitalPressStyle())
            .accessibilityLabel("Open \(entry.video.title)")
            VStack(alignment: .leading, spacing: 6) {
                Button(action: onOpen) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(entry.video.title)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.text)
                            .multilineTextAlignment(.leading)
                            .lineLimit(2)
                        Text(entry.latest.formatted(date: .omitted, time: .shortened))
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                FlowLayout(spacing: 6) {
                    ForEach(entry.posts) { post in
                        VideoPostChip(post: post)
                    }
                }
            }
        }
        .padding(.vertical, 8)
    }
}

/// One platform: tap opens the post. Checking, failed, and removed posts say so in words, not only colour.
struct VideoPostChip: View {
    let post: VideoPost
    @Environment(\.openURL) private var openURL

    var body: some View {
        let style = VideoPostStyle(post)
        Button {
            if let link = post.link { openURL(link) }
        } label: {
            HStack(spacing: 5) {
                PlatformBadge(platform: post.platform, size: 18)
                Text(PlatformStyle.name(post.platform))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                if let label = style.label {
                    Text(label)
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(style.tint)
                }
                if let views = post.views {
                    Text("\(views.formatted()) views")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(OrbitalTheme.textSecondary)
                }
                if post.link != nil {
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            .padding(.leading, 4)
            .padding(.trailing, 9)
            .frame(minHeight: 30)
            .background(style.tint.opacity(0.12), in: Capsule())
            .overlay(Capsule().strokeBorder(style.tint.opacity(0.3), lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(OrbitalPressStyle())
        .disabled(post.link == nil)
        .accessibilityLabel("\(PlatformStyle.name(post.platform)), \(style.label ?? "posted")\(post.views.map { ", \($0) views" } ?? "")")
    }
}

struct VideoPostStyle {
    let label: String?
    let tint: Color

    init(_ post: VideoPost) {
        switch post.status {
        case "failed":
            label = "Didn't post"
            tint = OrbitalTheme.danger
        case "removed":
            label = "Removed"
            tint = OrbitalTheme.textTertiary
        case "posting":
            label = "Posting"
            tint = OrbitalTheme.accent
        case "checking":
            label = "Checking"
            tint = OrbitalTheme.warning
        default:
            if post.link == nil {
                label = "No link"
                tint = OrbitalTheme.warning
            } else {
                label = nil
                tint = PlatformStyle.color(post.platform)
            }
        }
    }
}

/// A portrait thumbnail from /v1/media or https. A film glyph stands in until it loads or when there is none.
struct VideoThumb: View {
    let src: String?
    var width: CGFloat = 54
    var height: CGFloat = 72
    @EnvironmentObject private var store: OrbitalStore
    @State private var image: UIImage?

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 10, style: .continuous)
        ZStack {
            LinearGradient(colors: [OrbitalTheme.surfaceHighlight, OrbitalTheme.surface], startPoint: .top, endPoint: .bottom)
            if let picture = image ?? src.flatMap(OrbitalImageLoader.cached) {
                Image(uiImage: picture)
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "film")
                    .font(.system(size: min(width, height) * 0.32, weight: .semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
        .frame(width: width, height: height)
        .clipShape(shape)
        .overlay(shape.strokeBorder(Color.white.opacity(0.1), lineWidth: 1))
        .task(id: src) {
            guard let src, !src.isEmpty else { image = nil; return }
            image = await OrbitalImageLoader.shared.image(src: src, baseURL: store.baseURL, token: store.imageToken)
        }
        .accessibilityHidden(true)
    }
}

struct VideoDetailSheet: View {
    let entry: VideoDayEntry
    let block: VideosBlock
    @Environment(\.dismiss) private var dismiss

    /// Every post of this video, not only the day it was opened from. Newest first.
    private var posts: [VideoPost] {
        let all = block.items.first { $0.id == entry.video.id }?.posts ?? entry.video.posts
        return all.sorted { (OrbitalDates.parse($0.at) ?? .distantPast) > (OrbitalDates.parse($1.at) ?? .distantPast) }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top, spacing: 14) {
                        VideoThumb(src: entry.video.thumbnail, width: 96, height: 140)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(entry.video.title)
                                .font(.headline)
                                .foregroundStyle(OrbitalTheme.text)
                            if !block.product.isEmpty {
                                Text(block.product)
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(OrbitalTheme.accent)
                            }
                            Text(facts)
                                .font(.caption)
                                .foregroundStyle(OrbitalTheme.textTertiary)
                            if !entry.video.tags.isEmpty {
                                FlowLayout(spacing: 6) {
                                    ForEach(entry.video.tags, id: \.self) { tag in
                                        OrbitalChip(text: tag)
                                    }
                                }
                            }
                        }
                    }
                    if let experiment = entry.video.experiment, !experiment.isEmpty {
                        HStack(spacing: 8) {
                            Image(systemName: "flask.fill")
                                .foregroundStyle(OrbitalTheme.violet)
                            Text(experiment)
                                .font(.footnote)
                                .foregroundStyle(OrbitalTheme.textSecondary)
                        }
                        .orbitalCard(padding: 12)
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        OrbitalEyebrow(text: "Where it went")
                        VStack(spacing: 0) {
                            ForEach(Array(posts.enumerated()), id: \.offset) { index, post in
                                if index > 0 {
                                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                                }
                                postRow(post)
                            }
                        }
                        .orbitalCard(padding: 12)
                    }
                    if let caption = entry.video.caption, !caption.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            OrbitalEyebrow(text: "Caption")
                            Text(caption)
                                .font(.footnote)
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .orbitalCard(padding: 12)
                        }
                    }
                }
                .padding(16)
            }
            .background(OrbitalTheme.background.ignoresSafeArea())
            .navigationTitle(entry.video.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .preferredColorScheme(.dark)
    }

    private var facts: String {
        var parts: [String] = []
        if let made = OrbitalDates.shortDay(entry.video.madeAt) { parts.append("Made \(made)") }
        if let duration = entry.video.duration { parts.append("\(Int(duration.rounded())) s") }
        return parts.joined(separator: " · ")
    }

    private func postRow(_ post: VideoPost) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                VideoPostChip(post: post)
                Spacer(minLength: 0)
                if let at = OrbitalDates.parse(post.at) {
                    Text(at.formatted(date: .abbreviated, time: .shortened))
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            let detail = [post.account, post.note].compactMap { $0 }.filter { !$0.isEmpty }
            if !detail.isEmpty {
                Text(detail.joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
            if let link = post.link {
                Text(link.absoluteString)
                    .font(.caption2.monospaced())
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
            }
        }
        .padding(.vertical, 8)
    }
}
