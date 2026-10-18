import SwiftUI

/// Block markdown for agent summaries: headings, lists, quotes, code, tables, images.
/// Inline styling (bold, code, links) comes from AttributedString.
struct DeskMarkdown: View {
    let text: String
    var font: Font = .callout
    var color: Color = OrbitalTheme.text
    var onImage: ((String) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(Self.parse(text).enumerated()), id: \.offset) { _, block in
                view(for: block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    enum Block {
        case heading(Int, String)
        case paragraph(String)
        case bullet(marker: String, text: String, indent: Int)
        case quote(String)
        case code(String)
        case table([[String]])
        case image(alt: String, url: String)
        case rule
    }

    @ViewBuilder
    private func view(for block: Block) -> some View {
        switch block {
        case .heading(let level, let value):
            Text(Self.inline(value))
                .font(level <= 1 ? .title3.weight(.bold) : level == 2 ? .headline : .subheadline.weight(.semibold))
                .foregroundStyle(color)
                .padding(.top, 4)
        case .paragraph(let value):
            Text(Self.inline(value))
                .font(font)
                .foregroundStyle(color)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        case .bullet(let marker, let value, let indent):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(marker)
                    .font(font.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
                Text(Self.inline(value))
                    .font(font)
                    .foregroundStyle(color)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(indent) * 14)
        case .quote(let value):
            Text(Self.inline(value))
                .font(font.italic())
                .foregroundStyle(OrbitalTheme.textSecondary)
                .padding(.leading, 10)
                .overlay(alignment: .leading) {
                    Rectangle().fill(OrbitalTheme.accent.opacity(0.5)).frame(width: 2)
                }
        case .code(let value):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(value)
                    .font(.caption.monospaced())
                    .foregroundStyle(OrbitalTheme.text)
                    .textSelection(.enabled)
                    .padding(10)
            }
            .background(OrbitalTheme.backgroundDeep.opacity(0.8), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        case .table(let rows):
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                        GridRow {
                            ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                                Text(Self.inline(cell))
                                    .font(index == 0 ? .caption.weight(.bold) : .caption)
                                    .foregroundStyle(index == 0 ? OrbitalTheme.textSecondary : color)
                            }
                        }
                        if index == 0 {
                            Divider().overlay(OrbitalTheme.hairline)
                        }
                    }
                }
                .padding(10)
            }
            .background(OrbitalTheme.surface.opacity(0.6), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        case .image(let alt, let url):
            Button {
                onImage?(url)
            } label: {
                TrackerImage(src: url, key: Self.stableKey(url), contentMode: .fit)
                    .frame(maxWidth: .infinity, maxHeight: 360)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .accessibilityLabel(alt.isEmpty ? "Image" : alt)
            }
            .buttonStyle(.plain)
        case .rule:
            OrbitalRule()
        }
    }

    static func stableKey(_ url: String) -> String {
        url.split(separator: "?").first.map(String.init) ?? url
    }

    static func inline(_ value: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: value, options: options)) ?? AttributedString(value)
    }

    private static let imagePattern = try! NSRegularExpression(pattern: #"!\[([^\]]*)\]\(([^)\s]+)\)"#)
    private static let orderedPattern = try! NSRegularExpression(pattern: #"^(\d+)[.)]\s+(.*)$"#)

    static func parse(_ text: String) -> [Block] {
        var blocks: [Block] = []
        var paragraph: [String] = []
        var code: [String]?
        var table: [[String]] = []

        func flushParagraph() {
            guard !paragraph.isEmpty else { return }
            let joined = paragraph.joined(separator: "\n")
            paragraph = []
            appendWithImages(joined) { blocks.append(.paragraph($0)) }
        }
        func flushTable() {
            guard !table.isEmpty else { return }
            blocks.append(.table(table))
            table = []
        }
        func appendWithImages(_ value: String, make: (String) -> Void) {
            let ns = value as NSString
            let matches = imagePattern.matches(in: value, range: NSRange(location: 0, length: ns.length))
            if matches.isEmpty {
                make(value)
                return
            }
            var rest = value
            for match in matches.reversed() {
                if let range = Range(match.range, in: rest) { rest.removeSubrange(range) }
            }
            let trimmed = rest.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { make(trimmed) }
            for match in matches {
                blocks.append(.image(alt: ns.substring(with: match.range(at: 1)), url: ns.substring(with: match.range(at: 2))))
            }
        }

        for raw in text.components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("```") {
                if let open = code {
                    blocks.append(.code(open.joined(separator: "\n")))
                    code = nil
                } else {
                    flushParagraph()
                    flushTable()
                    code = []
                }
                continue
            }
            if code != nil {
                code?.append(raw)
                continue
            }
            if line.hasPrefix("|") {
                flushParagraph()
                let cells = line.trimmingCharacters(in: CharacterSet(charactersIn: "|"))
                    .components(separatedBy: "|")
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                if cells.allSatisfy({ $0.allSatisfy { "-:".contains($0) } && !$0.isEmpty }) { continue }
                table.append(cells)
                continue
            }
            flushTable()
            if line.isEmpty {
                flushParagraph()
                continue
            }
            if line == "---" || line == "***" || line == "___" {
                flushParagraph()
                blocks.append(.rule)
                continue
            }
            if line.hasPrefix("#") {
                let level = line.prefix { $0 == "#" }.count
                let value = line.dropFirst(level).trimmingCharacters(in: .whitespaces)
                if level <= 6, !value.isEmpty {
                    flushParagraph()
                    blocks.append(.heading(level, value))
                    continue
                }
            }
            let indent = raw.prefix { $0 == " " }.count / 2
            if line.hasPrefix("- ") || line.hasPrefix("* ") || line.hasPrefix("+ ") {
                flushParagraph()
                var value = String(line.dropFirst(2))
                var marker = "•"
                if value.hasPrefix("[ ] ") { marker = "○"; value = String(value.dropFirst(4)) }
                if value.hasPrefix("[x] ") || value.hasPrefix("[X] ") { marker = "✓"; value = String(value.dropFirst(4)) }
                appendWithImages(value) { blocks.append(.bullet(marker: marker, text: $0, indent: indent)) }
                continue
            }
            let ns = line as NSString
            if let match = orderedPattern.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) {
                flushParagraph()
                blocks.append(.bullet(marker: ns.substring(with: match.range(at: 1)) + ".", text: ns.substring(with: match.range(at: 2)), indent: indent))
                continue
            }
            if line.hasPrefix(">") {
                flushParagraph()
                blocks.append(.quote(line.dropFirst().trimmingCharacters(in: .whitespaces)))
                continue
            }
            paragraph.append(line)
        }
        if let open = code { blocks.append(.code(open.joined(separator: "\n"))) }
        flushParagraph()
        flushTable()
        return blocks
    }
}
