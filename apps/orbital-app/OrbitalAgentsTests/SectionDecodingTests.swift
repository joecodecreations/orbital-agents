import XCTest
@testable import OrbitalAgents

/// Every sample section decodes, and together they use every block type the app draws.
@MainActor
final class SectionDecodingTests: XCTestCase {
    func testEveryFixtureSectionDecodes() throws {
        let files = try Fixtures.sectionFiles()
        XCTAssertGreaterThanOrEqual(files.count, 6)
        for url in files {
            let raw = OrbitalPreview.expand(try Data(contentsOf: url))
            XCTAssertNoThrow(try JSONDecoder().decode(SectionDocument.self, from: raw), url.lastPathComponent)
        }
        for doc in try Fixtures.sections() {
            XCTAssertEqual(doc.schema, OrbitalSchema.section, doc.id)
            XCTAssertNotNil(doc.space, "\(doc.id) has no space")
            for block in doc.everyBlock {
                if case .unsupported = block {
                    XCTFail("\(doc.id): block \(block.id) (\(block.typeName)) did not decode")
                }
            }
        }
    }

    func testFixturesCoverEveryBlockType() throws {
        let used = Set(try Fixtures.sections().flatMap(\.everyBlock).map(\.typeName))
        let missing = ContentBlock.knownTypes.subtracting(used)
        XCTAssertTrue(missing.isEmpty, "no fixture uses: \(missing.sorted())")
    }

    func testLayouts() throws {
        let weather = try Fixtures.section("weather")
        XCTAssertEqual(weather.layout, .tabs)
        XCTAssertEqual(weather.tabs.map(\.id), ["today", "week"])
        XCTAssertEqual(weather.spaceTitle, "Home")

        let runbooks = try Fixtures.section("runbooks")
        XCTAssertEqual(runbooks.layout, .sidebar)
        XCTAssertEqual(runbooks.nodes.map(\.id), ["backups", "dns", "restore"])
    }

    /// A tabs or sidebar section's own blocks are a summary page drawn above every tab or node.
    func testTopLevelBlocksOfTabsAndSidebarSections() throws {
        let weather = try Fixtures.section("weather")
        XCTAssertEqual(weather.blocks.map(\.id), ["advisory"])
        XCTAssertEqual(weather.pages.count, 1 + 2)
        XCTAssertNil(weather.pages[0].node)
        XCTAssertNil(weather.pages[0].tabID)
        XCTAssertEqual(weather.pages[0].blocks.map(\.id), ["advisory"])
        XCTAssertEqual(weather.pages[1].tabID, "today")

        let runbooks = try Fixtures.section("runbooks")
        XCTAssertEqual(runbooks.blocks.map(\.id), ["first"])
        XCTAssertEqual(runbooks.pages.count, 1 + 3)
        XCTAssertNil(runbooks.pages[0].node)
        XCTAssertEqual(runbooks.pages[1].node?.id, "backups")

        // Home rollups read the summary too, located on the section itself.
        let doc = try Fixtures.json(SectionDocument.self, """
        {"id": "fleet", "title": "Fleet", "layout": "tabs",
         "blocks": [{"type": "series", "id": "load", "kind": "server", "product": "fleet", "metrics": [{"id": "cpu", "points": []}]}],
         "tabs": [{"id": "a", "title": "A", "blocks": [{"type": "paragraph", "id": "p", "text": "x"}]}]}
        """)
        let series = Insights.collect([doc]).series
        XCTAssertEqual(series.count, 1)
        XCTAssertEqual(series.first?.location, PageLocation(sectionID: "fleet", nodeID: nil, tabID: nil))

        // A plain page has just its own blocks once.
        let plain = try Fixtures.section("household")
        XCTAssertEqual(plain.pages.count, 1)
    }

    // MARK: Text blocks

    func testTextBlocks() throws {
        let runbooks = try Fixtures.section("runbooks")
        guard case .heading(let heading)? = runbooks.block("h") else { return XCTFail("heading") }
        XCTAssertEqual(heading.level, 1)
        let backups = try XCTUnwrap(runbooks.nodes.first)
        guard case .bullets(let steps)? = backups.blocks.first(where: { $0.id == "steps" }) else { return XCTFail("bullets") }
        XCTAssertTrue(steps.ordered)
        XCTAssertEqual(steps.items.count, 4)
        guard case .callout(let warn)? = backups.blocks.first(where: { $0.id == "warn" }) else { return XCTFail("callout") }
        XCTAssertEqual(warn.tone, "danger")
        guard case .image(let image)? = backups.blocks.first(where: { $0.id == "diagram" }) else { return XCTFail("image") }
        XCTAssertEqual(image.src, "/v1/media/backup-diagram.png")
        XCTAssertFalse(image.alt.isEmpty)
    }

    // MARK: Figures

    func testServerHealthBlocks() throws {
        let doc = try Fixtures.section("server-health")
        guard case .metrics(let metrics)? = doc.block("now") else { return XCTFail("metrics") }
        XCTAssertEqual(metrics.items.map(\.id), ["cpu", "memory", "disk", "uptime"])
        XCTAssertEqual(metrics.items.first?.direction, "down")

        guard case .status(let status)? = doc.block("services") else { return XCTFail("status") }
        XCTAssertEqual(status.items.count, 6)
        XCTAssertEqual(status.downCount, 1)
        XCTAssertEqual(status.degradedCount, 1)

        guard case .progress(let progress)? = doc.block("volumes") else { return XCTFail("progress") }
        XCTAssertEqual(progress.items[1].fraction, 0.71, accuracy: 0.001)
        XCTAssertEqual(progress.items[1].shownValue, "71%")
        XCTAssertEqual(progress.items[2].max, 8)
        XCTAssertEqual(progress.items[2].shownValue, "5.1 / 8 TB")

        guard case .chart(let chart)? = doc.block("cpu-24h") else { return XCTFail("chart") }
        XCTAssertEqual(chart.chart, "area")
        XCTAssertEqual(chart.series.first?.points.count, 24)

        guard case .keyValue(let facts)? = doc.block("facts") else { return XCTFail("key_value") }
        XCTAssertEqual(facts.items.first?.label, "Host")

        guard case .table(let table)? = doc.block("containers") else { return XCTFail("table") }
        XCTAssertEqual(table.columns.count, 4)
        XCTAssertEqual(table.rows.count, 6)
        XCTAssertEqual(table.limit, 4)

        guard case .timeline(let timeline)? = doc.block("events") else { return XCTFail("timeline") }
        XCTAssertEqual(timeline.items.count, 4)
        XCTAssertNotNil(OrbitalDates.parse(timeline.items[0].at), "date token was not expanded")
    }

    func testTableCellsMayBeNumbers() throws {
        let doc = try Fixtures.section("github-repos")
        guard case .table(let table)? = doc.block("prs") else { return XCTFail("table") }
        XCTAssertEqual(table.rows.first?.first, "412")
    }

    // MARK: Actions

    func testButtonsAndPostActions() throws {
        let doc = try Fixtures.section("server-health")
        guard case .buttons(let buttons)? = doc.block("controls") else { return XCTFail("buttons") }
        XCTAssertEqual(buttons.items.count, 3)

        let restart = buttons.items[0]
        XCTAssertEqual(restart.id, "restart-media")
        XCTAssertEqual(restart.style, "primary")
        XCTAssertEqual(restart.action?.type, "post")
        XCTAssertEqual(restart.action?.action, "server.restart")
        XCTAssertEqual(restart.action?.postValue["service"] as? String, "media")
        XCTAssertNotNil(restart.action?.confirm)
        XCTAssertFalse(restart.pending)

        XCTAssertEqual(buttons.items[1].action?.type, "agent")
        XCTAssertNotNil(buttons.items[1].action?.prompt)
        XCTAssertEqual(buttons.items[2].action?.url, "https://status.example.com")
    }

    func testHero() throws {
        let doc = try Fixtures.section("weather")
        guard case .hero(let hero)? = doc.block("now-hero") else { return XCTFail("hero") }
        XCTAssertEqual(hero.image?.src, "/v1/media/weather-sky.jpg")
        XCTAssertEqual(hero.actions?.first?.action?.type, "open")
        XCTAssertEqual(hero.actions?.first?.action?.tabId, "week")
    }

    func testLinks() throws {
        let doc = try Fixtures.section("website-uptime")
        guard case .links(let links)? = doc.block("site-links") else { return XCTFail("links") }
        XCTAssertEqual(links.items.count, 2)
        XCTAssertEqual(links.items[0].icon, "dot.radiowaves.left.and.right")
    }

    // MARK: Data blocks

    func testProduct() throws {
        let doc = try Fixtures.section("sales")
        guard case .product(let app)? = doc.block("app-product") else { return XCTFail("product") }
        XCTAssertEqual(app.space, "shop")
        XCTAssertEqual(app.live.count, 2)
        XCTAssertEqual(app.releases.first { $0.status == "testing" }?.build, "118")
        XCTAssertEqual(app.ratings.first?.count, 812)
        XCTAssertEqual(app.reviews.count, 2)

        let repos = try Fixtures.section("github-repos")
        guard case .product(let cli)? = repos.block("cli-product") else { return XCTFail("product") }
        XCTAssertEqual(cli.pending.map(\.platform), ["homebrew"])
    }

    func testLedger() throws {
        let doc = try Fixtures.section("sales")
        guard case .ledger(let ledger)? = doc.block("store-ledger") else { return XCTFail("ledger") }
        XCTAssertEqual(ledger.currency, "USD")
        XCTAssertTrue(ledger.connected)
        XCTAssertEqual(ledger.days.count, 30)
        XCTAssertEqual(ledger.days.last?.date, DayMath.isoDay(Date()))
        XCTAssertTrue(ledger.days.allSatisfy { $0.date.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil })
        XCTAssertEqual(ledger.entries.count, 5)
        XCTAssertLessThan(ledger.entries[2].amount, 0)
    }

    func testTasksWithGroups() throws {
        let doc = try Fixtures.section("household")
        guard case .tasks(let tasks)? = doc.block("chores") else { return XCTFail("tasks") }
        XCTAssertEqual(tasks.items.count, 6)
        XCTAssertEqual(Set(tasks.items.compactMap(\.group)), ["home", "garden", "errands"])
        XCTAssertEqual(tasks.items.filter(\.done).count, 1)
        let add = try XCTUnwrap(tasks.add)
        XCTAssertEqual(add.group, "home")
        XCTAssertEqual(add.groups.map(\.label), ["Home", "Garden", "Errands"])
        XCTAssertEqual(add.remindOptions.count, 2)
        XCTAssertEqual(tasks.items.first?.due, DayMath.isoDay(Date()))
    }

    func testSeries() throws {
        let doc = try Fixtures.section("github-repos")
        guard case .series(let series)? = doc.block("cli-series") else { return XCTFail("series") }
        XCTAssertEqual(series.kind, "repo")
        XCTAssertEqual(series.space, "open-source")
        XCTAssertEqual(series.product, "orbit-cli")
        XCTAssertEqual(series.window, 30)
        let stars = try XCTUnwrap(series.metrics.first { $0.statKey == "stars" })
        XCTAssertTrue(stars.level)
        XCTAssertEqual(stars.platform, "github")
        XCTAssertEqual(stars.points.count, 30)
        XCTAssertEqual(stars.points.last?.x, DayMath.isoDay(Date()))
    }

    func testPercentagesAreReadingsNotCounts() throws {
        let block = try Fixtures.json(SeriesBlock.self, """
        {"type": "series", "id": "s", "kind": "server", "metrics": [
          {"id": "cpu", "format": "percent", "points": [{"x": "2026-01-01", "y": 20}, {"x": "2026-01-02", "y": 30}]},
          {"id": "latency", "format": "ms", "points": []},
          {"id": "errors", "points": []},
          {"id": "share", "format": "percent", "level": false, "points": []}
        ]}
        """)
        XCTAssertEqual(block.metrics.map(\.level), [true, true, false, false])
        let cpu = block.metrics[0]
        let days = ["2026-01-01", "2026-01-02"].compactMap { Insights.day($0) }
        XCTAssertEqual(days.count, 2)
        XCTAssertEqual(Trends.summary(Trends.line(cpu, days: days), level: cpu.level), 30, "a level shows its latest value, not a sum")
    }

    func testInsightCards() throws {
        let doc = try Fixtures.section("sales")
        guard case .insightCards(let block)? = doc.block("findings") else { return XCTFail("insight_cards") }
        XCTAssertEqual(block.space, "shop")
        XCTAssertEqual(block.cards.count, 2)
        let refunds = block.cards[0]
        XCTAssertEqual(refunds.severity, "high")
        XCTAssertTrue(refunds.isOpen)
        XCTAssertEqual(refunds.evidence.count, 3)
        XCTAssertEqual(refunds.actions.map(\.kind), ["email", "agent"])
        XCTAssertEqual(refunds.actions[0].email?.subject, "About your order")
        XCTAssertEqual(refunds.actions[0].email?.to, "customer@example.com")
        XCTAssertNotNil(refunds.actions[1].brief?.prompt)

        let listed = try Fixtures.json(InsightEmail.self, #"{"to": ["a@example.com", "b@example.com"], "subject": "Hi", "body": "Hello"}"#)
        XCTAssertEqual(listed.to, "a@example.com, b@example.com")
    }

    func testVideos() throws {
        let doc = try Fixtures.section("github-repos")
        guard case .videos(let videos)? = doc.block("demos") else { return XCTFail("videos") }
        XCTAssertEqual(videos.items.count, 2)
        XCTAssertEqual(videos.items[0].posts.count, 2)
        XCTAssertEqual(videos.items[0].posts[0].views, 4210)
        XCTAssertNotNil(videos.items[0].posts[0].link)
        XCTAssertTrue(videos.items[1].posts[1].failed)
    }

    // MARK: Leniency

    func testUnknownBlockTypeDoesNotBreakTheSection() throws {
        let doc = try Fixtures.json(SectionDocument.self, """
        {"schema": "orbital.section.v1", "id": "x", "title": "X", "icon": "star", "order": 1, "layout": "page", "tabs": [], "nodes": [],
         "blocks": [{"type": "map", "id": "m1", "pins": []}, {"type": "paragraph", "id": "p", "text": "still here"}]}
        """)
        XCTAssertEqual(doc.blocks.count, 2)
        guard case .unsupported(let id, _) = doc.blocks[0] else { return XCTFail("expected unsupported") }
        XCTAssertEqual(id, "m1")
        XCTAssertEqual(doc.blocks[0].typeName, "map")
        guard case .paragraph(let p) = doc.blocks[1] else { return XCTFail("paragraph") }
        XCTAssertEqual(p.text, "still here")
    }

    func testTypeAliases() throws {
        let doc = try Fixtures.json(SectionDocument.self, """
        {"schema": "orbital.section.v1", "id": "a", "title": "A", "icon": "star", "order": 1, "layout": "page", "tabs": [], "nodes": [],
         "blocks": [
           {"type": "text", "id": "t", "text": "hi"},
           {"type": "list", "id": "l", "items": ["one"]},
           {"type": "metric", "id": "m", "items": [{"label": "Up", "value": 3}]},
           {"type": "media", "id": "i", "url": "https://example.com/a.png", "alt": "a"},
           {"type": "actions", "id": "b", "items": [{"label": "Go"}]}
         ]}
        """)
        XCTAssertEqual(doc.blocks.map(\.typeName), ["paragraph", "bullets", "metrics", "image", "buttons"])
        guard case .metrics(let m) = doc.blocks[2] else { return XCTFail("metrics") }
        XCTAssertEqual(m.items.first?.value, "3")
    }

    func testOlderFeedsUseCompanyForTheSpace() throws {
        let series = try Fixtures.json(SeriesBlock.self, """
        {"type": "series", "id": "s", "kind": "site", "company": "websites", "product": "example.com", "metrics": []}
        """)
        XCTAssertEqual(series.space, "websites")
        let ledger = try Fixtures.json(LedgerBlock.self, """
        {"type": "ledger", "id": "l", "company": "shop", "days": [{"date": "2026-01-02", "amount": "12.5", "company": "shop"}]}
        """)
        XCTAssertEqual(ledger.space, "shop")
        XCTAssertEqual(ledger.days.first?.amount, 12.5)
        XCTAssertEqual(ledger.currency, "USD")
    }

    func testMissingOptionalFieldsDefault() throws {
        let doc = try Fixtures.json(SectionDocument.self, """
        {"id": "bare", "title": "Bare", "blocks": [{"type": "tasks", "id": "t", "items": [{"id": "1", "title": "Do it"}, {"title": "no id"}]}]}
        """)
        XCTAssertEqual(doc.layout, .page)
        XCTAssertTrue(doc.tabs.isEmpty)
        guard case .tasks(let tasks) = doc.blocks[0] else { return XCTFail("tasks") }
        XCTAssertEqual(tasks.items.map(\.id), ["1"])
        XCTAssertTrue(tasks.items[0].canToggle)
    }
}
