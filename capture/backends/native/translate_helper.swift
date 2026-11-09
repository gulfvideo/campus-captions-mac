// translate_helper: on-device translation through Apple's Translation
// framework, driven by relay/translate.py (AppleTranslator) over stdin/stdout.
//
// One JSON object per line in:
//     {"id": 7, "text": "...", "source": "en", "targets": ["es", "fr"]}
// one per line out:
//     {"id": 7, "translations": {"es": "..."}, "errors": {"fr": "why"}}
//
// Nothing leaves the machine. A language pack that is not installed is
// reported per language rather than failing the line; the operator installs
// it under System Settings > General > Language & Region > Translation
// Languages. Requires macOS 26 for TranslationSession(installedSource:target:),
// the initialiser that works without a SwiftUI view.

import Foundation
import Translation

struct Request: Decodable {
    let id: Int
    let text: String
    let source: String
    let targets: [String]
}

struct Response: Encodable {
    let id: Int
    let translations: [String: String]
    let errors: [String: String]
}

func emit<T: Encodable>(_ value: T) {
    if let data = try? JSONEncoder().encode(value), let line = String(data: data, encoding: .utf8) {
        print(line)
        fflush(stdout)
    }
}

@main
struct Helper {
    static func main() async {
        let availability = LanguageAvailability()
        var sessions: [String: TranslationSession] = [:]
        var unavailable: [String: String] = [:]      // pair -> reason, checked once

        print(#"{"ready": true}"#)
        fflush(stdout)

        while let line = readLine() {
            guard let data = line.data(using: .utf8),
                  let request = try? JSONDecoder().decode(Request.self, from: data) else { continue }
            let source = Locale.Language(identifier: request.source)
            var translations: [String: String] = [:]
            var errors: [String: String] = [:]

            for code in request.targets {
                let pair = request.source + ">" + code
                if let reason = unavailable[pair] {
                    errors[code] = reason
                    continue
                }
                let target = Locale.Language(identifier: code)
                if sessions[pair] == nil {
                    switch await availability.status(from: source, to: target) {
                    case .installed:
                        sessions[pair] = TranslationSession(installedSource: source, target: target)
                    case .supported:
                        unavailable[pair] = "\(code) is not downloaded on this Mac - System Settings > General > Language & Region > Translation Languages"
                    default:
                        unavailable[pair] = "\(code) is not supported by Apple Translation"
                    }
                    if let reason = unavailable[pair] {
                        errors[code] = reason
                        continue
                    }
                }
                do {
                    translations[code] = try await sessions[pair]!.translate(request.text).targetText
                } catch {
                    errors[code] = "\(error)"
                    sessions[pair] = nil             // a fresh session next time
                }
            }
            emit(Response(id: request.id, translations: translations, errors: errors))
        }
    }
}
