// On-device speech-to-text helper for the "apple_speech" capture backend.
//
// Reads raw mono 16-bit PCM from stdin (whatever sample rate --sample-rate
// says) and writes one JSON object per line to stdout:
//
//   {"status":"downloading-model"}   model asset is being fetched
//   {"status":"ready"}               analyzer is running, audio may be sent
//   {"text":"...","final":false}     a transcript update
//   {"error":"..."}                  fatal; process exits non-zero after this
//
// Stdin EOF finalizes the analysis and exits. Never talks to the network -
// SpeechAnalyzer is on-device only, which is the whole point of this backend.
//
// Build with build.sh, not `swiftc` directly: the Info.plist section and the
// ad-hoc codesign are what let a bare command-line binary pass the Speech
// Recognition TCC check.

import AVFAudio
import Darwin
import Foundation
import Speech

struct StatusMsg: Encodable { let status: String }
struct ErrorMsg: Encodable { let error: String }
struct TranscriptMsg: Encodable { let text: String; let final: Bool }

func emit<T: Encodable>(_ value: T) {
    guard let data = try? JSONEncoder().encode(value),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line)
}

func fail(_ message: String) -> Never {
    emit(ErrorMsg(error: message))
    Darwin.exit(1)
}

/// Runs one input buffer through an AVAudioConverter, sized generously in
/// case the conversion also changes the sample rate.
func convert(_ input: AVAudioPCMBuffer, using converter: AVAudioConverter, to outputFormat: AVAudioFormat) -> AVAudioPCMBuffer? {
    let ratio = outputFormat.sampleRate / input.format.sampleRate
    let capacity = AVAudioFrameCount(Double(input.frameLength) * ratio) + 16
    guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return nil }

    var provided = false
    var conversionError: NSError?
    let status = converter.convert(to: output, error: &conversionError) { _, outStatus in
        if provided {
            outStatus.pointee = .noDataNow
            return nil
        }
        provided = true
        outStatus.pointee = .haveData
        return input
    }
    guard status != .error else {
        emit(ErrorMsg(error: "audio conversion failed: \(conversionError?.localizedDescription ?? "unknown error")"))
        return nil
    }
    return output.frameLength > 0 ? output : nil
}

@main
struct SpeechHelper {
    static func main() async throws {
        setvbuf(stdout, nil, _IONBF, 0)

        guard #available(macOS 26.0, *) else {
            fail("SpeechAnalyzer requires macOS 26.0 or newer")
        }

        var sampleRate = 16000
        var localeIdentifier = "en-US"
        var it = CommandLine.arguments.dropFirst().makeIterator()
        while let arg = it.next() {
            switch arg {
            case "--sample-rate":
                if let v = it.next(), let n = Int(v) { sampleRate = n }
            case "--locale":
                if let v = it.next() { localeIdentifier = v }
            default:
                break
            }
        }

        try await run(sampleRate: sampleRate, localeIdentifier: localeIdentifier)
    }

    @available(macOS 26.0, *)
    static func run(sampleRate: Int, localeIdentifier: String) async throws {
        let requested = Locale(identifier: localeIdentifier)
        guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: requested) else {
            fail("locale \(localeIdentifier) is not supported for on-device transcription")
        }

        let transcriber = SpeechTranscriber(locale: locale, preset: .progressiveTranscription)

        if !(await SpeechTranscriber.installedLocales).contains(locale) {
            emit(StatusMsg(status: "downloading-model"))
            if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
                try await request.downloadAndInstall()
            }
        }

        guard let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            fail("no available audio format for the analyzer")
        }
        guard let inputFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16, sampleRate: Double(sampleRate),
            channels: 1, interleaved: true
        ) else {
            fail("could not build an audio format for \(sampleRate) Hz")
        }

        guard let converter = AVAudioConverter(from: inputFormat, to: analyzerFormat) else {
            fail("could not build an audio converter to the analyzer's format")
        }
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let (inputSequence, inputBuilder) = AsyncStream<AnalyzerInput>.makeStream()

        let resultsTask = Task {
            do {
                for try await result in transcriber.results {
                    emit(TranscriptMsg(text: String(result.text.characters), final: result.isFinal))
                }
            } catch {
                emit(ErrorMsg(error: "results stream failed: \(error)"))
            }
        }

        try await analyzer.start(inputSequence: inputSequence)
        emit(StatusMsg(status: "ready"))

        let stdin = FileHandle.standardInput
        var pending = Data()
        let bytesPerFrame = 2  // Int16 mono

        while true {
            let chunk = stdin.availableData
            if chunk.isEmpty { break }  // EOF: operator closed the stream
            pending.append(chunk)

            let usableFrames = pending.count / bytesPerFrame
            guard usableFrames > 0 else { continue }
            let usableBytes = usableFrames * bytesPerFrame
            let frameData = pending.prefix(usableBytes)
            pending.removeFirst(usableBytes)

            guard let buffer = AVAudioPCMBuffer(pcmFormat: inputFormat, frameCapacity: AVAudioFrameCount(usableFrames)) else {
                continue
            }
            buffer.frameLength = AVAudioFrameCount(usableFrames)
            frameData.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                if let src = raw.bindMemory(to: Int16.self).baseAddress,
                   let dst = buffer.int16ChannelData?[0] {
                    dst.update(from: src, count: usableFrames)
                }
            }

            if let converted = convert(buffer, using: converter, to: analyzerFormat) {
                inputBuilder.yield(AnalyzerInput(buffer: converted))
            }
        }

        inputBuilder.finish()

        try? await analyzer.finalizeAndFinishThroughEndOfInput()
        _ = await resultsTask.value
    }
}
