import Charts
import SwiftUI
import UIKit

struct BlockView: View {
    let block: ContentBlock
    let accent: Color
    var location: PageLocation?
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter

    var body: some View {
        switch block {
        case .heading(let heading):
            headingView(heading)
        case .paragraph(let paragraph):
            markdown(paragraph.text)
                .font(.body)
                .lineSpacing(3)
                .foregroundStyle(OrbitalTheme.text.opacity(0.92))
                .frame(maxWidth: .infinity, alignment: .leading)
        case .bullets(let bullets):
            bulletsView(bullets)
        case .callout(let callout):
            calloutView(callout)
        case .metrics(let metrics):
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 100), spacing: 10)], spacing: 10) {
                ForEach(metrics.items) { tile in
                    MetricTileView(tile: tile, accent: accent)
                }
            }
        case .keyValue(let rows):
            keyValueView(rows)
        case .table(let table):
            TableCard(table: table, accent: accent)
        case .timeline(let timeline):
            timelineView(timeline)
        case .divider:
            OrbitalRule()
                .padding(.vertical, 4)
        case .chart(let chart):
            ChartCard(chart: chart, accent: accent)
        case .image(let image):
            RemoteImage(src: image.src, alt: image.alt, aspect: image.aspect, caption: image.caption)
        case .hero(let hero):
            HeroCard(hero: hero, accent: accent, location: location)
        case .buttons(let buttons):
            ButtonRow(items: Array(buttons.items.prefix(6)), accent: accent, blockID: buttons.id, location: location)
        case .progress(let progress):
            ProgressCard(block: progress, accent: accent)
        case .status(let status):
            StatusCard(block: status, accent: accent)
        case .links(let links):
            LinksCard(block: links, accent: accent)
        case .product(let product):
            ProductCard(block: product, accent: accent, location: location)
        case .ledger(let ledger):
            LedgerCard(block: ledger, accent: accent, location: location)
        case .tasks(let tasks):
            TasksCard(block: tasks, accent: accent, location: location)
        case .series(let series):
            SeriesCard(block: series, accent: accent)
        case .insightCards(let insights):
            InsightCardsBlockView(block: insights, location: location)
        case .videos(let videos):
            VideosBlockCard(block: videos, accent: accent)
        case .subscriptions(let subscriptions):
            SubscriptionsBlockCard(block: subscriptions, accent: accent, location: location)
        #if ORBITAL_PRIVATE
        case .extra(let block):
            PrivateBlockView(block: block, accent: accent, location: location)
        #endif
        case .unsupported:
            EmptyView()
        }
    }

    // MARK: - Text blocks

    @ViewBuilder
    private func headingView(_ heading: HeadingBlock) -> some View {
        switch heading.level {
        case 1:
            Text(heading.text)
                .font(.title3.weight(.bold))
                .foregroundStyle(OrbitalTheme.text)
                .padding(.top, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
        case 3:
            HStack(spacing: 8) {
                Capsule()
                    .fill(accent)
                    .frame(width: 3, height: 12)
                Text(heading.text.uppercased())
                    .font(.caption.weight(.bold))
                    .tracking(0.8)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
            .padding(.top, 4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
        default:
            Text(heading.text)
                .font(.headline.weight(.semibold))
                .foregroundStyle(OrbitalTheme.text)
                .padding(.top, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func bulletsView(_ bullets: BulletsBlock) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if let title = bullets.title, !title.isEmpty {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
            }
            ForEach(Array(bullets.items.enumerated()), id: \.offset) { index, item in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    if bullets.ordered {
                        Text("\(index + 1)")
                            .font(.caption.weight(.bold).monospacedDigit())
                            .foregroundStyle(accent)
                            .frame(minWidth: 20)
                            .padding(.vertical, 2)
                            .background(accent.opacity(0.14), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                    } else {
                        Text("•")
                            .font(.body.weight(.black))
                            .foregroundStyle(accent)
                            .frame(width: 14)
                    }
                    markdown(item)
                        .font(.body)
                        .lineSpacing(2)
                        .foregroundStyle(OrbitalTheme.text.opacity(0.92))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func calloutView(_ callout: CalloutBlock) -> some View {
        let tone = OrbitalTheme.toneColor(callout.tone, accent: accent)
        let shape = RoundedRectangle(cornerRadius: OrbitalTheme.cardRadius - 2, style: .continuous)
        return HStack(alignment: .top, spacing: 12) {
            Image(systemName: OrbitalTheme.toneSymbol(callout.tone))
                .font(.body.weight(.semibold))
                .foregroundStyle(tone)
                .frame(width: 22)
                .padding(.top, 1)
                .accessibilityLabel(OrbitalTheme.toneLabel(callout.tone))
            VStack(alignment: .leading, spacing: 4) {
                if let title = callout.title, !title.isEmpty {
                    Text(title)
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                }
                markdown(callout.text)
                    .font(.subheadline)
                    .lineSpacing(2)
                    .foregroundStyle(OrbitalTheme.text.opacity(0.88))
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            shape.fill(
                LinearGradient(
                    colors: [tone.opacity(0.18), tone.opacity(0.07)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
        )
        .overlay(shape.strokeBorder(tone.opacity(0.30), lineWidth: 1))
        .overlay(alignment: .leading) {
            Capsule()
                .fill(tone)
                .frame(width: 3)
                .padding(.vertical, 12)
                .padding(.leading, 1)
        }
    }

    // MARK: - Data blocks

    private func keyValueView(_ rows: KeyValueBlock) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            if let title = rows.title {
                cardTitle(title)
                    .padding(.bottom, 8)
            }
            ForEach(Array(rows.items.enumerated()), id: \.offset) { index, item in
                if index > 0 {
                    Rectangle()
                        .fill(OrbitalTheme.hairline.opacity(0.7))
                        .frame(height: 1)
                }
                // Side by side when both fit on one line, otherwise the value goes under its label.
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        keyLabel(item.label)
                        Spacer(minLength: 12)
                        keyValue(item.value)
                            .multilineTextAlignment(.trailing)
                    }
                    VStack(alignment: .leading, spacing: 3) {
                        keyLabel(item.label)
                        keyValue(item.value)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(.vertical, 10)
            }
        }
        .orbitalCard(padding: 16)
    }

    private func timelineView(_ timeline: TimelineBlock) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if let title = timeline.title {
                cardTitle(title)
            }
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(timeline.items.enumerated()), id: \.element.id) { index, item in
                    let tone = OrbitalTheme.toneColor(item.tone, accent: accent)
                    let isLast = index == timeline.items.count - 1
                    HStack(alignment: .top, spacing: 12) {
                        VStack(spacing: 0) {
                            ZStack {
                                Circle()
                                    .fill(tone.opacity(0.18))
                                    .frame(width: 18, height: 18)
                                Circle()
                                    .fill(tone)
                                    .frame(width: 8, height: 8)
                                    .shadow(color: tone.opacity(0.7), radius: 4)
                            }
                            if !isLast {
                                Rectangle()
                                    .fill(
                                        LinearGradient(
                                            colors: [tone.opacity(0.5), OrbitalTheme.hairline],
                                            startPoint: .top,
                                            endPoint: .bottom
                                        )
                                    )
                                    .frame(width: 2)
                                    .frame(maxHeight: .infinity)
                            }
                        }
                        .frame(width: 18)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(item.shownAt)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(tone)
                            Text(item.title)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.text)
                            if let detail = item.detail {
                                Text(detail)
                                    .font(.subheadline)
                                    .foregroundStyle(OrbitalTheme.textSecondary)
                            }
                        }
                        .padding(.bottom, isLast ? 0 : 18)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .orbitalCard(padding: 16)
    }

    // MARK: - Helpers

    private func keyLabel(_ text: String) -> some View {
        Text(text)
            .font(.subheadline)
            .foregroundStyle(OrbitalTheme.textSecondary)
    }

    private func keyValue(_ text: String) -> some View {
        Text(text)
            .font(.subheadline.weight(.semibold))
            .monospacedDigit()
            .foregroundStyle(OrbitalTheme.text)
    }

    private func cardTitle(_ title: String) -> some View {
        Text(title)
            .font(.headline)
            .foregroundStyle(OrbitalTheme.text)
    }

    private func markdown(_ text: String) -> Text {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        if let attributed = try? AttributedString(markdown: text, options: options) {
            return Text(attributed)
        }
        return Text(text)
    }
}

struct MetricTileView: View {
    let tile: MetricTile
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(tile.label.uppercased())
                .font(.caption2.weight(.bold))
                .tracking(0.6)
                .foregroundStyle(OrbitalTheme.textSecondary)
                .lineLimit(2)
            Text(tile.value)
                .font(.system(.title2, design: .rounded).weight(.bold))
                .monospacedDigit()
                .foregroundStyle(OrbitalTheme.text)
                .lineLimit(1)
                .minimumScaleFactor(0.55)
            if let delta = tile.delta {
                DeltaPill(delta: delta, direction: tile.direction)
            }
            if let caption = tile.caption {
                Text(caption)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(3)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 44, alignment: .topLeading)
        .background(alignment: .topLeading) {
            Circle()
                .fill(accent.opacity(0.22))
                .frame(width: 90, height: 90)
                .blur(radius: 28)
                .offset(x: -44, y: -50)
                .allowsHitTesting(false)
        }
        .orbitalCard(padding: 14, radius: 16)
        .accessibilityElement(children: .combine)
    }
}

struct DeltaPill: View {
    let delta: String
    let direction: String?

    var body: some View {
        let color = Self.color(direction)
        HStack(spacing: 3) {
            Image(systemName: Self.symbol(direction))
                .font(.system(size: 9, weight: .heavy))
            Text(delta)
                .font(.caption.weight(.bold))
                .monospacedDigit()
        }
        .foregroundStyle(color)
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(color.opacity(0.14), in: Capsule())
    }

    static func color(_ direction: String?) -> Color {
        switch direction {
        case "up": return OrbitalTheme.success
        case "down": return OrbitalTheme.danger
        default: return OrbitalTheme.textSecondary
        }
    }

    static func symbol(_ direction: String?) -> String {
        switch direction {
        case "up": return "arrow.up.right"
        case "down": return "arrow.down.right"
        default: return "arrow.right"
        }
    }
}

struct HeroCard: View {
    let hero: HeroBlock
    let accent: Color
    var location: PageLocation?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let image = hero.image {
                RemoteImage(src: image.src, alt: image.alt, aspect: "16:9", caption: nil, corner: 0)
                    .overlay(alignment: .bottom) {
                        LinearGradient(
                            colors: [OrbitalTheme.surface.opacity(0), OrbitalTheme.surface.opacity(0.85)],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                        .frame(height: 70)
                        .allowsHitTesting(false)
                    }
            }
            VStack(alignment: .leading, spacing: 8) {
                if let eyebrow = hero.eyebrow {
                    OrbitalEyebrow(text: eyebrow, color: accent)
                }
                Text(hero.title)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                    .fixedSize(horizontal: false, vertical: true)
                if let subtitle = hero.subtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let actions = hero.actions, !actions.isEmpty {
                    ButtonRow(items: Array(actions.prefix(3)), accent: accent, blockID: hero.id, location: location)
                        .padding(.top, 6)
                }
            }
            .padding(18)
        }
        .orbitalCard(padding: 0, tint: accent)
    }
}

/// Buttons on a page. A `post` button sends its action to the feed that owns the section (after its
/// `confirm` question, if any) and shows "Sent" until the feed pushes the page again.
struct ButtonRow: View {
    let items: [ButtonItem]
    let accent: Color
    var blockID: String?
    var location: PageLocation?
    @EnvironmentObject private var router: AppRouter
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.openURL) private var openURL
    @State private var confirming: ButtonItem?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(items) { item in
                Button {
                    activate(item)
                } label: {
                    label(for: item)
                }
                .buttonStyle(OrbitalPressStyle())
                .disabled(item.pending)
                .opacity(item.pending ? 0.6 : 1)
            }
        }
        .confirmationDialog(
            confirming?.action?.confirm ?? "",
            isPresented: Binding(get: { confirming != nil }, set: { if !$0 { confirming = nil } }),
            titleVisibility: .visible,
            presenting: confirming
        ) { item in
            Button(item.label, role: item.style == "destructive" ? .destructive : nil) { post(item) }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func post(_ item: ButtonItem) {
        guard let action = item.action, let type = action.action, let location else { return }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        Task {
            await store.perform(
                type: type,
                sectionID: action.sectionId ?? location.sectionID,
                itemID: item.id,
                blockID: blockID,
                nodeID: location.nodeID,
                tabID: location.tabID,
                value: action.postValue,
                source: "button"
            )
        }
    }

    @ViewBuilder
    private func label(for item: ButtonItem) -> some View {
        let shape = RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius, style: .continuous)
        if item.style == "link" {
            HStack(spacing: 6) {
                Text(item.label)
                    .font(.subheadline.weight(.semibold))
                if item.pending {
                    ProgressView().controlSize(.mini)
                } else {
                    Image(systemName: symbol(for: item))
                        .font(.caption.weight(.bold))
                }
            }
            .foregroundStyle(accent)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        } else {
            HStack(spacing: 8) {
                if item.pending {
                    ProgressView().controlSize(.small).tint(foreground(item.style))
                } else {
                    Image(systemName: symbol(for: item))
                        .font(.subheadline.weight(.bold))
                }
                Text(item.pending ? "\(item.label) · sent" : item.label)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
            }
            .foregroundStyle(foreground(item.style))
            .frame(maxWidth: .infinity, minHeight: 46)
            .padding(.horizontal, 12)
            .background(background(item.style))
            .overlay(shape.strokeBorder(stroke(item.style), lineWidth: 1))
            .clipShape(shape)
            .contentShape(shape)
        }
    }

    private func activate(_ item: ButtonItem) {
        if let action = item.action {
            switch action.type {
            case "agent", "desk", "grok":
                router.openDesk(prompt: action.prompt)
                return
            case "run_agent", "run":
                router.runAgent(AgentRunDraft(machine: action.machine, agent: action.agent, prompt: action.prompt ?? "",
                                              cwd: action.cwd ?? "", title: action.title ?? ""))
                return
            case "open":
                router.open(section: action.sectionId, node: action.nodeId, tab: action.tabId)
                return
            case "url":
                if let url = action.url.flatMap(URL.init(string:)) { openURL(url) }
                return
            case "post":
                guard action.action != nil, location != nil else { return }
                if Insights.nonEmpty(action.confirm) != nil {
                    confirming = item
                } else {
                    post(item)
                }
                return
            default:
                break
            }
        }
        if let url = item.url.flatMap(URL.init(string:)) {
            openURL(url)
        }
    }

    private func symbol(for item: ButtonItem) -> String {
        switch item.action?.type {
        case "agent", "desk", "grok": return "sparkles"
        case "run_agent", "run": return "play.fill"
        case "post": return item.style == "destructive" ? "exclamationmark.triangle.fill" : "paperplane.fill"
        case "open": return "arrow.right"
        case "url": return "arrow.up.right"
        default: return item.url != nil ? "arrow.up.right" : "arrow.right"
        }
    }

    private func foreground(_ style: String) -> Color {
        switch style {
        case "primary": return OrbitalTheme.backgroundDeep
        case "destructive": return OrbitalTheme.danger
        default: return OrbitalTheme.text
        }
    }

    @ViewBuilder
    private func background(_ style: String) -> some View {
        switch style {
        case "primary":
            LinearGradient(colors: [accent, accent.opacity(0.8)], startPoint: .top, endPoint: .bottom)
        case "destructive":
            OrbitalTheme.danger.opacity(0.12)
        default:
            OrbitalTheme.surfaceRaised
        }
    }

    private func stroke(_ style: String) -> Color {
        switch style {
        case "primary": return Color.white.opacity(0.18)
        case "destructive": return OrbitalTheme.danger.opacity(0.35)
        default: return Color.white.opacity(0.08)
        }
    }
}

struct ChartCard: View {
    let chart: ChartBlock
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if chart.series.allSatisfy({ $0.points.isEmpty }) {
                HStack(spacing: 8) {
                    Image(systemName: "chart.xyaxis.line")
                    Text("No points in this payload.")
                }
                .font(.footnote)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .frame(maxWidth: .infinity, minHeight: 80)
            } else {
                chartView
                    .frame(height: 220)
                if chart.series.count > 1 {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), spacing: 8)], alignment: .leading, spacing: 6) {
                        ForEach(Array(chart.series.enumerated()), id: \.element.name) { index, series in
                            HStack(spacing: 6) {
                                RoundedRectangle(cornerRadius: 2, style: .continuous)
                                    .fill(color(for: series, index: index))
                                    .frame(width: 10, height: 10)
                                Text(series.name)
                                    .font(.caption.weight(.medium))
                                    .foregroundStyle(OrbitalTheme.textSecondary)
                                    .lineLimit(1)
                            }
                        }
                    }
                }
            }
            if let caption = chart.caption {
                Text(caption)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
        }
        .orbitalCard(padding: 16)
    }

    @ViewBuilder
    private var header: some View {
        if chart.title != nil || latest != nil {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                if let title = chart.title {
                    Text(title)
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                }
                Spacer(minLength: 0)
                if let latest {
                    VStack(alignment: .trailing, spacing: 1) {
                        Text(formatY(latest.point.y))
                            .font(.system(.title3, design: .rounded).weight(.bold))
                            .monospacedDigit()
                            .foregroundStyle(primaryColor)
                        Text(label(latest.point))
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Latest \(formatY(latest.point.y)) at \(label(latest.point))")
                }
            }
        }
    }

    /// The last point of a single line or area series. Bars and scatter are not time series by contract.
    private var latest: (series: ChartSeries, point: ChartPoint)? {
        guard isSingle, chart.chart == "line" || chart.chart == "area",
              let series = chart.series.first, series.points.count >= 2,
              let point = series.points.last
        else { return nil }
        return (series, point)
    }

    private var isSingle: Bool { chart.series.count == 1 }

    private var primaryColor: Color {
        guard let first = chart.series.first else { return accent }
        return color(for: first, index: 0)
    }

    private var lineStroke: StrokeStyle {
        StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round)
    }

    private var areaFill: LinearGradient {
        LinearGradient(
            colors: [primaryColor.opacity(0.38), primaryColor.opacity(0.02)],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    @ViewBuilder
    private var chartView: some View {
        switch chart.chart {
        case "bar":
            styled(Chart(samples, id: \.id) { sample in
                barMark(sample)
            })
        case "scatter":
            styled(Chart(samples.filter(\.numericX), id: \.id) { sample in
                PointMark(
                    x: .value(chart.xLabel ?? "X", sample.number),
                    y: .value(chart.yLabel ?? "Y", sample.y)
                )
                .foregroundStyle(by: .value("Series", sample.series))
                .symbolSize(60)
            })
        case "area":
            styled(Chart(samples, id: \.id) { sample in
                areaMarks(sample)
            })
        default:
            styled(Chart(samples, id: \.id) { sample in
                lineMarks(sample)
            })
        }
    }

    @ChartContentBuilder
    private func barMark(_ sample: Sample) -> some ChartContent {
        if isSingle {
            BarMark(
                x: .value(chart.xLabel ?? "X", sample.x),
                y: .value(chart.yLabel ?? "Y", sample.y)
            )
            .foregroundStyle(
                LinearGradient(colors: [primaryColor, primaryColor.opacity(0.55)], startPoint: .top, endPoint: .bottom)
            )
            .cornerRadius(5)
        } else {
            BarMark(
                x: .value(chart.xLabel ?? "X", sample.x),
                y: .value(chart.yLabel ?? "Y", sample.y)
            )
            .foregroundStyle(by: .value("Series", sample.series))
            .position(by: .value("Series", sample.series))
            .cornerRadius(4)
        }
    }

    @ChartContentBuilder
    private func areaMarks(_ sample: Sample) -> some ChartContent {
        if isSingle {
            AreaMark(
                x: .value(chart.xLabel ?? "X", sample.x),
                y: .value(chart.yLabel ?? "Y", sample.y)
            )
            .foregroundStyle(areaFill)
            .interpolationMethod(.catmullRom)
            LineMark(
                x: .value(chart.xLabel ?? "X", sample.x),
                y: .value(chart.yLabel ?? "Y", sample.y)
            )
            .foregroundStyle(primaryColor)
            .lineStyle(lineStroke)
            .interpolationMethod(.catmullRom)
        } else {
            AreaMark(
                x: .value(chart.xLabel ?? "X", sample.x),
                y: .value(chart.yLabel ?? "Y", sample.y)
            )
            .foregroundStyle(by: .value("Series", sample.series))
            .opacity(0.35)
            .interpolationMethod(.catmullRom)
            LineMark(
                x: .value(chart.xLabel ?? "X", sample.x),
                y: .value(chart.yLabel ?? "Y", sample.y)
            )
            .foregroundStyle(by: .value("Series", sample.series))
            .lineStyle(lineStroke)
            .interpolationMethod(.catmullRom)
        }
    }

    @ChartContentBuilder
    private func lineMarks(_ sample: Sample) -> some ChartContent {
        if isSingle {
            AreaMark(
                x: .value(chart.xLabel ?? "X", sample.x),
                y: .value(chart.yLabel ?? "Y", sample.y)
            )
            .foregroundStyle(areaFill)
            .interpolationMethod(.catmullRom)
            LineMark(
                x: .value(chart.xLabel ?? "X", sample.x),
                y: .value(chart.yLabel ?? "Y", sample.y)
            )
            .foregroundStyle(primaryColor)
            .lineStyle(lineStroke)
            .interpolationMethod(.catmullRom)
            if sample.isLast {
                PointMark(
                    x: .value(chart.xLabel ?? "X", sample.x),
                    y: .value(chart.yLabel ?? "Y", sample.y)
                )
                .foregroundStyle(primaryColor)
                .symbolSize(70)
            }
        } else {
            LineMark(
                x: .value(chart.xLabel ?? "X", sample.x),
                y: .value(chart.yLabel ?? "Y", sample.y)
            )
            .foregroundStyle(by: .value("Series", sample.series))
            .lineStyle(lineStroke)
            .interpolationMethod(.catmullRom)
        }
    }

    private func styled<V: View>(_ chartView: V) -> some View {
        chartView
            .chartForegroundStyleScale(range: seriesColors)
            .chartLegend(.hidden)
            .chartYAxis {
                AxisMarks(position: .leading) { value in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 1, dash: [3, 4]))
                        .foregroundStyle(OrbitalTheme.hairline.opacity(0.6))
                    AxisValueLabel {
                        if let number = value.as(Double.self) {
                            Text(formatY(number))
                                .font(.caption2)
                                .foregroundStyle(OrbitalTheme.textTertiary)
                        }
                    }
                }
            }
            .chartXAxis {
                if chart.chart == "scatter" {
                    AxisMarks(values: .automatic(desiredCount: 4)) { _ in
                        AxisValueLabel()
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                } else {
                    AxisMarks(values: xAxisLabels) { _ in
                        AxisValueLabel()
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
            }
            .chartPlotStyle { plot in
                plot.background(Color.white.opacity(0.015))
            }
    }

    private struct Sample: Identifiable {
        let id: String
        let series: String
        let x: String
        let y: Double
        let numericX: Bool
        let number: Double
        let isLast: Bool
    }

    private var samples: [Sample] {
        var rows: [Sample] = []
        for series in chart.series {
            for (index, point) in series.points.enumerated() {
                let number: Double
                let numeric: Bool
                switch point.x {
                case .number(let value):
                    number = value
                    numeric = true
                case .text(let text):
                    number = Double(text) ?? 0
                    numeric = Double(text) != nil
                }
                rows.append(Sample(
                    id: "\(series.name)-\(index)",
                    series: series.name,
                    x: label(point),
                    y: point.y,
                    numericX: numeric,
                    number: number,
                    isLast: index == series.points.count - 1
                ))
            }
        }
        return rows
    }

    /// At most five evenly spaced category labels, always the first and the last, so dates do not collide.
    private var xAxisLabels: [String] {
        var seen = Set<String>()
        let ordered = samples.map(\.x).filter { seen.insert($0).inserted }
        guard ordered.count > 5 else { return ordered }
        let last = ordered.count - 1
        var picked: [String] = []
        for step in 0 ... 4 {
            let label = ordered[Int((Double(step) * Double(last) / 4).rounded())]
            if !picked.contains(label) { picked.append(label) }
        }
        return picked
    }

    private var seriesColors: [Color] {
        chart.series.enumerated().map { index, series in color(for: series, index: index) }
    }

    private func label(_ point: ChartPoint) -> String {
        if let label = point.label { return label }
        switch point.x {
        case .text(let text): return text
        case .number(let number): return formatY(number)
        }
    }

    private func formatY(_ number: Double) -> String {
        switch chart.yFormat {
        case "currency":
            let formatter = NumberFormatter()
            formatter.numberStyle = .currency
            formatter.currencyCode = "USD"
            formatter.maximumFractionDigits = number.rounded() == number ? 0 : 2
            return formatter.string(from: NSNumber(value: number)) ?? "\(number)"
        case "percent":
            return String(format: "%.0f%%", number * 100)
        case "compact":
            if abs(number) >= 1_000_000 { return String(format: "%.1fM", number / 1_000_000) }
            if abs(number) >= 1_000 { return String(format: "%.1fK", number / 1_000) }
            return String(format: "%g", number)
        default:
            return String(format: "%g", number)
        }
    }

    private func color(for series: ChartSeries, index: Int) -> Color {
        if let hex = series.color, let color = Color(hex: hex) { return color }
        if index == 0 { return accent }
        return OrbitalTheme.chartPalette[index % OrbitalTheme.chartPalette.count]
    }
}

struct RemoteImage: View {
    let src: String
    let alt: String
    let aspect: String?
    let caption: String?
    var corner: CGFloat = OrbitalTheme.cardRadius
    @EnvironmentObject private var store: OrbitalStore
    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ZStack {
                LinearGradient(
                    colors: [OrbitalTheme.surfaceRaised, OrbitalTheme.surface],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                        .transition(.opacity)
                } else if failed {
                    VStack(spacing: 6) {
                        Image(systemName: "photo")
                            .font(.title3)
                        Text(alt.isEmpty ? "Image unavailable" : alt)
                            .font(.footnote)
                            .multilineTextAlignment(.center)
                    }
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .padding(12)
                } else {
                    ProgressView()
                        .tint(OrbitalTheme.textTertiary)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(minHeight: image == nil && ratio == nil ? 160 : nil)
            .aspectRatio(ratio, contentMode: .fit)
            .frame(maxHeight: ratio == nil ? 480 : nil)
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: corner, style: .continuous))
            .overlay {
                if corner > 0 {
                    RoundedRectangle(cornerRadius: corner, style: .continuous)
                        .strokeBorder(Color.white.opacity(0.08), lineWidth: 1)
                }
            }
            .accessibilityLabel(alt)
            .animation(.easeOut(duration: 0.25), value: image != nil)
            if let caption, !caption.isEmpty {
                Text(caption)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
        }
        .task(id: src) { await load() }
    }

    private var ratio: CGFloat? {
        switch aspect {
        case "16:9": return 16 / 9
        case "4:3": return 4 / 3
        case "1:1": return 1
        case "3:2": return 3 / 2
        default: return nil
        }
    }

    private func load() async {
        failed = false
        if let cached = OrbitalImageLoader.cached(src) {
            image = cached
            return
        }
        image = nil
        if src.hasPrefix("file:"), let url = URL(string: src) {
            // Only the Alerts test card writes these, into this app's own caches.
            if OrbitalTestAlert.isOwnedFile(url), let local = OrbitalTestAlert.localFile(for: url),
               let loaded = UIImage(contentsOfFile: local.path) {
                image = loaded
            } else {
                failed = true
            }
            return
        }
        // Hub media (/v1/media/…) with the session token, or any https image; cached in memory and on disk.
        image = await OrbitalImageLoader.shared.image(src: src, baseURL: store.baseURL, token: OrbitalImageToken.current)
        failed = image == nil
    }
}

/// Set by the store after login so image loads can attach the bearer without exposing the token on the view model.
enum OrbitalImageToken {
    static var current: String?
}


/// A table card. With `limit`, the first rows show and a button reveals the rest.
struct TableCard: View {
    let table: TableBlock
    let accent: Color
    @State private var showAll = false

    private var visibleRows: [[String]] {
        guard let limit = table.limit, limit > 0, !showAll, table.rows.count > limit else { return table.rows }
        return Array(table.rows.prefix(limit))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let title = table.title {
                HStack(alignment: .firstTextBaseline) {
                    Text(title)
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                    Spacer(minLength: 8)
                    if table.rows.count > 5 {
                        Text("\(table.rows.count) rows")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
            }
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 0) {
                    GridRow {
                        ForEach(table.columns, id: \.self) { column in
                            Text(column.uppercased())
                                .font(.caption2.weight(.bold))
                                .tracking(0.6)
                                .foregroundStyle(OrbitalTheme.textTertiary)
                                .padding(.bottom, 8)
                        }
                    }
                    ForEach(Array(visibleRows.enumerated()), id: \.offset) { _, row in
                        Rectangle()
                            .fill(OrbitalTheme.hairline.opacity(0.7))
                            .frame(height: 1)
                            .gridCellUnsizedAxes(.horizontal)
                        GridRow {
                            ForEach(0 ..< table.columns.count, id: \.self) { index in
                                Text(index < row.count ? row[index] : "")
                                    .font(index == 0 ? .subheadline.weight(.semibold) : .subheadline)
                                    .monospacedDigit()
                                    .foregroundStyle(index == 0 ? OrbitalTheme.text : OrbitalTheme.text.opacity(0.85))
                                    .padding(.vertical, 10)
                            }
                        }
                    }
                }
            }
            if let limit = table.limit, limit > 0, table.rows.count > limit {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { showAll.toggle() }
                } label: {
                    Text(showAll ? "Show fewer" : "Show all \(table.rows.count)")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(accent)
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(OrbitalPressStyle())
            }
        }
        .orbitalCard(padding: 16)
    }
}
