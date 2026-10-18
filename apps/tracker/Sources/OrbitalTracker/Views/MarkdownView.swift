import SwiftUI

/// Block-level markdown: headings, lists (incl. task lists), code, quotes,
/// rules, tables and images. Inline styling uses AttributedString.
enum MDBlock {
    case heading(Int, String)
    case paragraph(String)
    case list([MDListItem])
    case code(String)
    case quote(String)
    case rule
    case image(alt: String, src: String)
    case table([[String]])
}

struct MDListItem {
    var level: Int
    var marker: String
    var text: String
    var checked: Bool?
}

enum MarkdownParser {
    private static let headingRx = try! NSRegularExpression(pattern: #"^(#{1,6})\s+(.*)$"#)
    private static let listRx = try! NSRegularExpression(pattern: #"^(\s*)([-*+]|\d+[.)])\s+(?:\[( |x|X)\]\s+)?(.*)$"#)
    private static let imageLineRx = try! NSRegularExpression(pattern: #"^!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)$"#)
    static let inlineImageRx = try! NSRegularExpression(pattern: #"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)"#)

    static func parse(_ text: String) -> [MDBlock] {
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var blocks: [MDBlock] = []
        var paragraph: [String] = []
        var list: [MDListItem] = []

        func flushParagraph() {
            if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: " "))) }
            paragraph = []
        }
        func flushList() {
            if !list.isEmpty { blocks.append(.list(list)) }
            list = []
        }
        func groups(_ rx: NSRegularExpression, _ s: String) -> [String]? {
            let ns = s as NSString
            guard let m = rx.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return nil }
            return (1..<m.numberOfRanges).map { m.range(at: $0).location == NSNotFound ? "" : ns.substring(with: m.range(at: $0)) }
        }

        var i = 0
        while i < lines.count {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.hasPrefix("```") {
                flushParagraph(); flushList()
                var code: [String] = []
                i += 1
                while i < lines.count, !lines[i].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    code.append(lines[i])
                    i += 1
                }
                blocks.append(.code(code.joined(separator: "\n")))
                i += 1
                continue
            }
            if trimmed.isEmpty {
                flushParagraph(); flushList()
                i += 1
                continue
            }
            if let g = groups(headingRx, trimmed) {
                flushParagraph(); flushList()
                blocks.append(.heading(g[0].count, g[1]))
            } else if trimmed.range(of: #"^(-{3,}|\*{3,}|_{3,})$"#, options: .regularExpression) != nil {
                flushParagraph(); flushList()
                blocks.append(.rule)
            } else if trimmed.hasPrefix(">") {
                flushParagraph(); flushList()
                var quote: [String] = []
                while i < lines.count, lines[i].trimmingCharacters(in: .whitespaces).hasPrefix(">") {
                    quote.append(String(lines[i].trimmingCharacters(in: .whitespaces).dropFirst()).trimmingCharacters(in: .whitespaces))
                    i += 1
                }
                blocks.append(.quote(quote.joined(separator: " ")))
                continue
            } else if trimmed.hasPrefix("|") {
                flushParagraph(); flushList()
                var rows: [[String]] = []
                while i < lines.count, lines[i].trimmingCharacters(in: .whitespaces).hasPrefix("|") {
                    let row = lines[i].trimmingCharacters(in: .whitespaces)
                    let cells = row.trimmingCharacters(in: CharacterSet(charactersIn: "|")).components(separatedBy: "|")
                        .map { $0.trimmingCharacters(in: .whitespaces) }
                    if !cells.allSatisfy({ $0.range(of: #"^:?-{2,}:?$"#, options: .regularExpression) != nil }) {
                        rows.append(cells)
                    }
                    i += 1
                }
                blocks.append(.table(rows))
                continue
            } else if let g = groups(imageLineRx, trimmed) {
                flushParagraph(); flushList()
                blocks.append(.image(alt: g[0], src: g[1]))
            } else if let g = groups(listRx, line) {
                flushParagraph()
                let level = g[0].replacingOccurrences(of: "\t", with: "  ").count / 2
                let checked: Bool? = g[2].isEmpty ? nil : g[2].lowercased() == "x"
                list.append(MDListItem(level: level, marker: g[1], text: g[3], checked: checked))
            } else if !list.isEmpty, line.first?.isWhitespace == true {
                list[list.count - 1].text += " " + trimmed
            } else {
                flushList()
                paragraph.append(trimmed)
            }
            i += 1
        }
        flushParagraph(); flushList()
        return blocks
    }

    static func inline(_ s: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace,
                                                              failurePolicy: .returnPartiallyParsedIfPossible)
        return (try? AttributedString(markdown: s, options: options)) ?? AttributedString(s)
    }

    /// Splits "text ![a](x.png) more" into text and image pieces.
    static func inlineImages(_ s: String) -> (text: String, images: [(alt: String, src: String)]) {
        let ns = s as NSString
        let matches = inlineImageRx.matches(in: s, range: NSRange(location: 0, length: ns.length))
        guard !matches.isEmpty else { return (s, []) }
        let images = matches.map { (ns.substring(with: $0.range(at: 1)), ns.substring(with: $0.range(at: 2))) }
        let text = inlineImageRx.stringByReplacingMatches(in: s, range: NSRange(location: 0, length: ns.length), withTemplate: "")
        return (text.trimmingCharacters(in: .whitespaces), images)
    }
}

struct MarkdownView: View {
    let text: String
    let store: TrackerStore
    var fontSize: CGFloat = 12.5
    var color: Color = .white.opacity(0.9)

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(Array(MarkdownParser.parse(text).enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .tint(Color(red: 0.55, green: 0.78, blue: 1.0))
    }

    @ViewBuilder
    private func blockView(_ block: MDBlock) -> some View {
        switch block {
        case .heading(let level, let s):
            Text(MarkdownParser.inline(s))
                .font(.system(size: fontSize + CGFloat(max(0, 4 - level)) * 1.5 + 1, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 2)
        case .paragraph(let s):
            let parts = MarkdownParser.inlineImages(s)
            VStack(alignment: .leading, spacing: 6) {
                if !parts.text.isEmpty { paragraphText(parts.text) }
                ForEach(Array(parts.images.enumerated()), id: \.offset) { _, img in
                    MarkdownImage(source: img.src, alt: img.alt, store: store)
                }
            }
        case .list(let items):
            VStack(alignment: .leading, spacing: 3) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        if let checked = item.checked {
                            Image(systemName: checked ? "checkmark.square.fill" : "square")
                                .font(.system(size: fontSize - 1))
                                .foregroundStyle(checked ? Palette.ok : .white.opacity(0.6))
                        } else {
                            Text(item.marker.first?.isNumber == true ? item.marker : "•")
                                .font(.system(size: fontSize, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.6))
                        }
                        paragraphText(item.text)
                    }
                    .padding(.leading, CGFloat(item.level) * 14)
                }
            }
        case .code(let code):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(.system(size: fontSize - 1.5, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.88))
                    .textSelection(.enabled)
                    .padding(8)
            }
            .background(RoundedRectangle(cornerRadius: 7).fill(Color.black.opacity(0.35)))
        case .quote(let s):
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 1.5).fill(Color.white.opacity(0.35)).frame(width: 3)
                paragraphText(s).italic()
            }
        case .rule:
            Rectangle().fill(Color.white.opacity(0.15)).frame(height: 1)
        case .image(let alt, let src):
            MarkdownImage(source: src, alt: alt, store: store)
        case .table(let rows):
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                ForEach(Array(rows.enumerated()), id: \.offset) { r, row in
                    GridRow {
                        ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                            Text(MarkdownParser.inline(cell))
                                .font(.system(size: fontSize - 1, weight: r == 0 ? .semibold : .regular))
                                .foregroundStyle(.white.opacity(r == 0 ? 1 : 0.85))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if r == 0 {
                        Rectangle().fill(Color.white.opacity(0.15)).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                    }
                }
            }
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 7).fill(Color.white.opacity(0.05)))
        }
    }

    private func paragraphText(_ s: String) -> Text {
        Text(MarkdownParser.inline(s))
            .font(.system(size: fontSize))
            .foregroundColor(color)
    }
}
