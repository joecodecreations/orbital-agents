import Foundation

// The `videos` block: one channel's videos and where each one was posted, with the public link,
// time, status, and views of every post. Drawn on its own page as posts by day.

struct VideosBlock: Codable, Equatable {
    var type: String = "videos"
    var id: String
    var title: String?
    /// The space the channel belongs to (`company` in the hub's contract).
    var space: String
    /// The channel or product the videos are for.
    var product: String
    var asOf: String?
    var timezone: String?
    var note: String?
    /// Buttons under the videos: a page in the app, a URL, or a prompt for the desk.
    var experiments: [VideoLink]
    var items: [VideoItem]

    enum CodingKeys: String, CodingKey {
        case type, id, title, space, company, product, timezone, note, experiments, items
        case asOf = "as_of"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        space = Insights.nonEmpty(c.lenientString(.space)) ?? c.lenientString(.company) ?? ""
        product = c.lenientString(.product) ?? ""
        asOf = c.lenientString(.asOf)
        timezone = c.lenientString(.timezone)
        note = c.lenientString(.note)
        experiments = c.lossy(VideoLink.self, .experiments)
        items = c.lossy(VideoItem.self, .items)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(title, forKey: .title)
        try c.encode(space, forKey: .space)
        try c.encode(product, forKey: .product)
        try c.encodeIfPresent(asOf, forKey: .asOf)
        try c.encodeIfPresent(timezone, forKey: .timezone)
        try c.encodeIfPresent(note, forKey: .note)
        try c.encode(experiments, forKey: .experiments)
        try c.encode(items, forKey: .items)
    }
}

/// One video and every platform it went to.
struct VideoItem: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var caption: String?
    var madeAt: String?
    var thumbnail: String?
    var duration: Double?
    var file: String?
    var tags: [String]
    var experiment: String?
    var posts: [VideoPost]

    enum CodingKeys: String, CodingKey {
        case id, title, caption, thumbnail, duration, file, tags, experiment, posts
        case madeAt = "made_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = Insights.nonEmpty(c.lenientString(.id)) else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "video id"))
        }
        self.id = id
        title = Insights.nonEmpty(c.lenientString(.title)) ?? id
        caption = c.lenientString(.caption)
        madeAt = c.lenientString(.madeAt)
        thumbnail = c.lenientString(.thumbnail)
        duration = c.lenientDouble(.duration)
        file = c.lenientString(.file)
        tags = c.lossy(String.self, .tags)
        experiment = c.lenientString(.experiment)
        posts = c.lossy(VideoPost.self, .posts)
    }
}

/// One upload of a video to one platform.
struct VideoPost: Codable, Equatable, Identifiable {
    var platform: String
    var status: String
    var url: String?
    var postId: String?
    var at: String?
    var account: String?
    var views: Int?
    var note: String?

    var id: String { "\(platform)|\(postId ?? url ?? at ?? "")" }

    enum CodingKeys: String, CodingKey {
        case platform, status, url, at, account, views, note
        case postId = "post_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        platform = (c.lenientString(.platform) ?? "web").lowercased()
        status = (c.lenientString(.status) ?? "posted").lowercased()
        url = c.lenientString(.url)
        postId = c.lenientString(.postId)
        at = c.lenientString(.at)
        account = c.lenientString(.account)
        views = c.lenientInt(.views)
        note = c.lenientString(.note)
    }

    /// An https link the app can open. Anything else is treated as not captured.
    var link: URL? {
        guard let url, let parsed = URL(string: url), parsed.scheme == "https" else { return nil }
        return parsed
    }

    var failed: Bool { status == "failed" || status == "removed" }
}

/// A button under the videos.
struct VideoLink: Codable, Equatable, Identifiable {
    var id: String
    var label: String
    var detail: String?
    var url: String?
    var action: ButtonAction?
}

/// A videos block and the page it was found on.
struct VideoRef: Identifiable, Equatable {
    var id: String
    var location: PageLocation
    var accent: String?
    var block: VideosBlock

    var spaceID: String { Spaces.key(block.space) }
}

/// One post of one video, flattened for the day list.
struct VideoPostRow: Identifiable, Equatable {
    var id: String
    var video: VideoItem
    var post: VideoPost
    var at: Date
    var spaceID: String
    var product: String
    var location: PageLocation
}

/// One video on one day, with the platforms it reached that day.
struct VideoDayEntry: Identifiable, Equatable {
    var id: String
    var video: VideoItem
    var posts: [VideoPost]
    var spaceID: String
    var product: String
    var location: PageLocation
    var latest: Date
}

struct VideoDay: Identifiable, Equatable {
    var id: Date { day }
    var day: Date
    var entries: [VideoDayEntry]
    var postCount: Int
}

enum VideoFeed {
    /// Every post with a time, newest first. Posts with no time are left out of the day list.
    static func rows(_ block: VideosBlock, location: PageLocation = PageLocation(sectionID: "")) -> [VideoPostRow] {
        rows([VideoRef(id: block.id, location: location, block: block)])
    }

    /// Every post across videos blocks, newest first.
    static func rows(_ refs: [VideoRef]) -> [VideoPostRow] {
        var out: [VideoPostRow] = []
        var seen = Set<String>()
        for ref in refs {
            for video in ref.block.items {
                for post in video.posts {
                    guard let raw = post.at, let at = OrbitalDates.parse(raw) ?? Insights.day(raw) else { continue }
                    let key = "\(ref.spaceID)|\(video.id)|\(post.id)"
                    guard seen.insert(key).inserted else { continue }
                    out.append(VideoPostRow(
                        id: key,
                        video: video,
                        post: post,
                        at: at,
                        spaceID: ref.spaceID,
                        product: ref.block.product,
                        location: ref.location
                    ))
                }
            }
        }
        return out.sorted { $0.at > $1.at }
    }

    /// Posts grouped by the device's calendar day, then by video within the day.
    static func days(_ rows: [VideoPostRow], calendar: Calendar = .current) -> [VideoDay] {
        let byDay = Dictionary(grouping: rows) { calendar.startOfDay(for: $0.at) }
        return byDay.keys.sorted(by: >).map { day in
            let list = byDay[day] ?? []
            var order: [String] = []
            var grouped: [String: VideoDayEntry] = [:]
            for row in list.sorted(by: { $0.at > $1.at }) {
                let key = "\(row.spaceID)|\(row.video.id)"
                if var entry = grouped[key] {
                    entry.posts.append(row.post)
                    grouped[key] = entry
                } else {
                    order.append(key)
                    grouped[key] = VideoDayEntry(
                        id: "\(Int(day.timeIntervalSince1970))|\(key)",
                        video: row.video,
                        posts: [row.post],
                        spaceID: row.spaceID,
                        product: row.product,
                        location: row.location,
                        latest: row.at
                    )
                }
            }
            return VideoDay(day: day, entries: order.compactMap { grouped[$0] }, postCount: list.count)
        }
    }

    static func count(_ rows: [VideoPostRow], since: Date) -> Int {
        rows.filter { $0.at >= since && !$0.post.failed }.count
    }
}
