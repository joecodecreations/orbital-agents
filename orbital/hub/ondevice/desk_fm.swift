// The desk's last-resort model: Apple's on-device language model on this Mac.
//
// No account, no network, no usage limit. desk_model.OnDeviceProvider compiles this on first
// use (xcrun swiftc) and runs it once per turn. The model is small, so it fills typed fields
// (guided generation) instead of writing the desk's `>>>` lines itself.
//
// stdin:  {"instructions": "...", "prompt": "..."}
// stdout: {"result": {"reply": "...", "todos": [...], "reminders": [...], "work": [...]}},
//         or {"error": "..."} and a non-zero exit.
// With --check it only reports whether the model is available.

import Foundation
import FoundationModels

struct Request: Decodable {
    let instructions: String
    let prompt: String
}

@Generable
struct WorkRequest: Codable {
    @Guide(description: "The project name exactly as the user said it")
    var project: String
    @Guide(description: "A two to five word title for the job")
    var title: String
    @Guide(description: "The user's request in full, in their own words")
    var request: String
}

@Generable
struct Reminder: Codable {
    @Guide(description: "What to remind the user about")
    var title: String
    @Guide(description: "When, as YYYY-MM-DDTHH:MM in the user's local time")
    var due: String
}

@Generable
struct DeskReply: Codable {
    @Guide(description: "One or two short, plain sentences spoken aloud to the user that answer what they just said")
    var reply: String
    @Guide(description: "To-dos the user explicitly asked you to save in their latest message. Usually empty.")
    var todos: [String]
    @Guide(description: "Reminders at a time that the user explicitly asked for in their latest message. Usually empty.")
    var reminders: [Reminder]
    @Guide(description: "Project work the user explicitly asked to have done in their latest message, like build, fix or add something. Never for greetings, questions, or status. Usually empty.")
    var work: [WorkRequest]
}

func emit(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object),
          let line = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write(Data((line + "\n").utf8))
}

@main
struct DeskFM {
    static func main() async {
        let model = SystemLanguageModel.default
        guard case .available = model.availability else {
            emit(["error": "on-device model unavailable: \(model.availability)"])
            exit(3)
        }
        if CommandLine.arguments.contains("--check") {
            emit(["done": "1"])
            return
        }
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let request = try? JSONDecoder().decode(Request.self, from: input) else {
            emit(["error": "bad request"])
            exit(2)
        }
        let session = LanguageModelSession(model: model, instructions: request.instructions)
        let options = GenerationOptions(temperature: 0.3, maximumResponseTokens: 400)
        do {
            let response = try await session.respond(to: request.prompt, generating: DeskReply.self, options: options)
            let data = try JSONEncoder().encode(response.content)
            let object = try JSONSerialization.jsonObject(with: data)
            emit(["result": object])
        } catch {
            emit(["error": "on-device model: \(error)"])
            exit(1)
        }
    }
}
