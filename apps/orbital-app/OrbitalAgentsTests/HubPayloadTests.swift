import XCTest
@testable import OrbitalAgents

/// The other payloads the hub serves: catalog and spaces, alerts, pulse, jobs, satellites, agent jobs,
/// the desk, the tracker, speech status, hub info and health.
@MainActor
final class HubPayloadTests: XCTestCase {
    // MARK: Catalog and spaces

    func testCatalogSpaces() throws {
        let catalog = try Fixtures.decode(CatalogResponse.self, "catalog.json")
        XCTAssertEqual(catalog.sections.count, try Fixtures.sectionFiles().count)
        let spaces = try XCTUnwrap(catalog.spaces)
        XCTAssertEqual(spaces.map(\.id), ["homelab", "websites", "shop", "open-source", "home", "studio"])
        XCTAssertEqual(spaces.first?.icon, "server.rack")
    }

    func testSpaceDirectoryFilesEverySection() throws {
        let sections = try Fixtures.sections()
        let catalog = try Fixtures.decode(CatalogResponse.self, "catalog.json")
        let directory = SpaceDirectory.build(
            catalog: sections.map(OrbitalStore.catalogEntry(for:)),
            declared: catalog.spaces,
            sections: Dictionary(uniqueKeysWithValues: sections.map { ($0.id, $0) }),
            collected: Insights.collect(sections)
        )
        XCTAssertEqual(directory.space("homelab")?.sectionIDs, ["server-health", "runbooks"])
        XCTAssertEqual(directory.space("home")?.name, "Home")
        XCTAssertEqual(Set(directory.space("home")?.sectionIDs ?? []), ["weather", "fitness", "household"])
        XCTAssertEqual(directory.space(forSection: "sales")?.id, "shop")
        for section in sections {
            XCTAssertNotNil(directory.space(forSection: section.id), "\(section.id) is in no space")
        }
        // Spaces come in the hub's declared order.
        XCTAssertEqual(directory.spaces.prefix(5).map(\.id), ["homelab", "websites", "shop", "open-source", "home"].map(Spaces.key))
    }

    func testSpacesFromSectionTagsAlone() throws {
        let doc = try Fixtures.json(SectionDocument.self, """
        {"id": "garden", "title": "Garden", "space": "back-yard", "space_title": "Back yard", "space_icon": "leaf.fill", "blocks": []}
        """)
        let directory = SpaceDirectory.build(catalog: [OrbitalStore.catalogEntry(for: doc)], declared: nil,
                                             sections: [doc.id: doc], collected: Insights.collect([doc]))
        let space = try XCTUnwrap(directory.space(forSection: "garden"))
        XCTAssertEqual(space.name, "Back yard")
        XCTAssertEqual(space.symbol, "leaf.fill")
    }

    func testInsightsCollectAcrossSections() throws {
        let collected = Insights.collect(try Fixtures.sections())
        XCTAssertEqual(collected.ledgers.count, 5)
        XCTAssertEqual(collected.subscriptions.count, 1)
        XCTAssertEqual(collected.products.count, 3)
        XCTAssertGreaterThanOrEqual(collected.series.count, 6)
        XCTAssertGreaterThanOrEqual(collected.tasks.count, 10)
    }

    // MARK: Alerts and pulse

    func testAlerts() throws {
        let alerts = try Fixtures.decode([OrbitalAlert].self, "alerts.json")
        XCTAssertEqual(alerts.count, 5)
        let slow = try XCTUnwrap(alerts.first { $0.id == "al-docs-slow" })
        XCTAssertEqual(slow.interruptionLevel, "time-sensitive")
        XCTAssertEqual(slow.resolvedLaunchURL?.absoluteString, "orbital://open?section=website-uptime")
        let agent = try XCTUnwrap(alerts.first { $0.id == "al-agent-done" })
        XCTAssertEqual(agent.resolvedLaunchURL?.host, "agents")
        XCTAssertEqual(alerts.filter(\.isAcked).count, 1)
        XCTAssertNotNil(OrbitalDates.parse(slow.createdAt))
    }

    func testPulse() throws {
        let pulses = try Fixtures.decode([PulseItem].self, "pulse.json")
        XCTAssertEqual(pulses.count, 3)
        let cpu = try XCTUnwrap(pulses.first { $0.key == "server-cpu" })
        XCTAssertEqual(cpu.shownValue, "23%")
        XCTAssertTrue(cpu.isLive())
        XCTAssertEqual(cpu.series?.count, 10)
        XCTAssertTrue(cpu.belongs(to: PageLocation(sectionID: "server-health", nodeID: nil, tabID: nil)))
    }

    // MARK: Automations

    func testJobsSnapshot() throws {
        let snapshot = try Fixtures.decode(JobsSnapshot.self, "jobs.json")
        XCTAssertEqual(snapshot.scheduler.up, true)
        XCTAssertEqual(snapshot.groups.count, 3)
        XCTAssertEqual(snapshot.lanes.count, 2)
        XCTAssertEqual(snapshot.jobs.count, 6)
        XCTAssertEqual(snapshot.failing.map(\.id), ["fitness"])
        XCTAssertTrue(snapshot.needsALook)
        XCTAssertEqual(snapshot.jobs.first { $0.id == "uptime" }?.isRunning, true)
        XCTAssertEqual(snapshot.jobs(for: PageLocation(sectionID: "sales", nodeID: nil, tabID: nil)).map(\.id), ["store"])
        XCTAssertEqual(snapshot.disk?.low, false)
    }

    // MARK: Satellites and agent jobs

    func testSatellites() throws {
        let list = try Fixtures.decode(SatelliteList.self, "satellites.json")
        XCTAssertEqual(list.satellites.count, 3)
        let studio = list.satellites[0]
        XCTAssertEqual(studio.name, "studio")
        XCTAssertEqual(studio.status, "online")
        XCTAssertTrue(studio.isHub)
        XCTAssertEqual(studio.capabilities.agents.map(\.name), ["claude", "codex", "gemini"])
        XCTAssertTrue(studio.capabilities.browser)
        XCTAssertEqual(studio.capabilities.browserPort, 9333)
        XCTAssertEqual(studio.runningJobs, 1)
        XCTAssertEqual(list.satellites[2].status, "offline")
    }

    func testSatelliteLeniency() throws {
        let list = try Fixtures.json(SatelliteList.self, """
        [{"satellite_id": "s1", "machine": "box", "online": false, "role": "hub",
          "capabilities": {"agents": [{"name": "claude", "available": false}, "codex"]}, "running_jobs": [{"id": "a"}, {"id": "b"}]},
         {"no": "id at all"}]
        """)
        XCTAssertEqual(list.satellites.count, 1)
        let box = list.satellites[0]
        XCTAssertEqual(box.id, "s1")
        XCTAssertEqual(box.name, "box")
        XCTAssertEqual(box.status, "offline")
        XCTAssertTrue(box.isHub)
        XCTAssertEqual(box.runningJobs, 2)
        XCTAssertEqual(box.capabilities.agents.map(\.available), [false, true])
    }

    func testAgentJobs() throws {
        let list = try Fixtures.decode(AgentJobList.self, "agent-jobs.json")
        XCTAssertEqual(list.jobs.count, 5)
        let running = list.jobs.filter(\.isActive)
        XCTAssertEqual(running.map(\.id), ["job-9f02", "job-5b11"])
        XCTAssertEqual(list.jobs.filter(\.isFailed).map(\.id), ["job-1d44"])
        let done = try XCTUnwrap(list.jobs.first { $0.id == "job-7c21" })
        XCTAssertEqual(done.statusWord, "Done")
        XCTAssertEqual(done.satelliteID, "sat-studio")
        XCTAssertEqual(done.trackerID, "t-flaky")
        XCTAssertNotNil(done.duration)

        let ordered = AgentJobsSort.ordered(list.jobs)
        XCTAssertTrue(ordered.prefix(2).allSatisfy(\.isActive), "running jobs come first")
    }

    func testAgentJobAlternativeKeys() throws {
        let job = try Fixtures.json(AgentJob.self, """
        {"id": "j1", "agent": "claude", "status": "done", "exit_code": 2, "prompt": "Tidy the README\\nand more"}
        """)
        XCTAssertEqual(job.id, "j1")
        XCTAssertTrue(job.isFailed)
        XCTAssertEqual(job.statusWord, "Exited 2")
        XCTAssertEqual(job.shownTitle, "Tidy the README")
    }

    func testRunRequestBody() {
        let request = AgentRunRequest(satelliteID: "sat-nas", agent: "codex", prompt: "Check disk", cwd: " ", title: "Disk")
        XCTAssertEqual(request.body["agent"] as? String, "codex")
        XCTAssertEqual(request.body["prompt"] as? String, "Check disk")
        XCTAssertNil(request.body["cwd"], "a blank folder is left out")
        XCTAssertEqual(request.body["title"] as? String, "Disk")
    }

    // MARK: Desk, tracker, speech

    func testDeskSnapshot() throws {
        let desk = try Fixtures.decode(DeskSnapshot.self, "desk/desk.json")
        XCTAssertEqual(desk.messages.count, 5)
        XCTAssertEqual(desk.messages.map(\.role), [.user, .assistant, .user, .event, .assistant])
        XCTAssertEqual(desk.tasks.filter(\.isActive).count, 1)
        XCTAssertEqual(desk.model?.configured, true)
        XCTAssertEqual(desk.configured, true)
        XCTAssertEqual(desk.trackerActive, 2)
    }

    func testDeskNotConfigured() throws {
        let desk = try Fixtures.json(DeskSnapshot.self, """
        {"messages": [], "tasks": [], "configured": false, "reason": "Set XAI_API_KEY in secrets.env"}
        """)
        XCTAssertEqual(desk.configured, false)
        XCTAssertEqual(desk.setup, "Set XAI_API_KEY in secrets.env")

        // The hub's own shape: the model block says it, with nulls for provider and model.
        let hub = try Fixtures.json(DeskSnapshot.self, """
        {"messages": [], "tasks": [], "model": {"configured": false, "provider": null, "model": null, "note": "add XAI_API_KEY"}}
        """)
        XCTAssertEqual(hub.model?.configured, false)
        XCTAssertEqual(hub.model?.provider, "none")
        XCTAssertNil(hub.configured)
    }

    func testTracker() throws {
        let page = try Fixtures.decode(TrackerPage.self, "desk/tracker.json")
        XCTAssertEqual(page.items.count, 4)
        XCTAssertEqual(page.active, 2)
        XCTAssertEqual(page.done, 2)
        let first = page.items[0]
        XCTAssertTrue(first.isActive)
        XCTAssertTrue(first.isAI)
        XCTAssertEqual(first.cover?.thumbURL?.hasPrefix("/v1/tracker/media/"), true)

        for item in page.items {
            let detail = try Fixtures.decode(TrackerDetail.self, "desk/items/\(item.id).json")
            XCTAssertEqual(detail.id, item.id)
            XCTAssertEqual(detail.title, item.title)
        }
        let dark = try Fixtures.decode(TrackerDetail.self, "desk/items/t-darkmode.json")
        XCTAssertEqual(dark.media.count, 2)
        XCTAssertEqual(dark.subagents.first?.title, "Contrast check")
        XCTAssertEqual(dark.notes.count, 2)
    }

    func testSpeechStatus() throws {
        let speech = try Fixtures.decode(SpeechStatus.self, "desk/speech.json")
        XCTAssertTrue(speech.tts)
        XCTAssertEqual(speech.providerTTS, "fish")
        XCTAssertEqual(speech.voices.map(\.id), ["voice-en-1", "voice-pt-1"])
        XCTAssertEqual(speech.defaultVoice, "voice-en-1")
        XCTAssertEqual(speech.defaultVoice(for: .english), "voice-en-1")
        XCTAssertEqual(speech.defaultVoice(for: .portuguese), "voice-pt-1")
        let bare = try Fixtures.json(SpeechStatus.self, #"{"tts": true, "default_voice": "v1"}"#)
        XCTAssertEqual(bare.defaultVoice(for: .english), "v1")
        XCTAssertNil(bare.defaultVoice(for: .portuguese))
    }

    // MARK: Hub

    func testHubInfoAndHealth() throws {
        let info = try Fixtures.decode(HubInfo.self, "hub.json")
        XCTAssertTrue(info.isOrbitalHub)
        XCTAssertEqual(info.name, "Orbital Agents")
        let other = try Fixtures.json(HubInfo.self, #"{"service": "something-else"}"#)
        XCTAssertFalse(other.isOrbitalHub)

        let health = try Fixtures.json(HubHealth.self, """
        {"ok": true, "service": "orbital-hub", "version": "0.1.0", "apns": {"enabled": true, "configured": true, "environment": "production"}}
        """)
        XCTAssertEqual(health.apns?.enabled, true)
        XCTAssertEqual(health.apns?.environment, "production")
        let old = try Fixtures.json(HubHealth.self, #"{"ok": true}"#)
        XCTAssertNil(old.apns)
    }

    func testPrefs() throws {
        struct Box: Decodable { var prefs: SharedPrefs }
        let prefs = try Fixtures.decode(Box.self, "prefs.json").prefs
        XCTAssertEqual(prefs.pinnedSections, ["server-health", "website-uptime", "sales"])
    }
}
