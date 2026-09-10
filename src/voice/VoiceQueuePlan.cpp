#include "voice/VoiceQueuePlan.h"

#include <algorithm>
#include <cctype>

namespace voice {

    namespace {

        constexpr std::string_view kWavExt = ".wav";
        constexpr std::string_view kJsonExt = ".json";
        constexpr std::string_view kTmpExt = ".tmp";
        constexpr std::string_view kParkedExt = ".parked";

        std::string lowered(std::string_view text) {
            std::string out(text);
            std::transform(out.begin(), out.end(), out.begin(),
                           [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
            return out;
        }

        bool endsWith(std::string_view text, std::string_view suffix) {
            return text.size() >= suffix.size() && text.substr(text.size() - suffix.size()) == suffix;
        }

        std::string qualify(std::string_view name) {
            std::string out(kQueueDir);
            out += '/';
            out += name;
            return out;
        }

        std::string_view stemOf(std::string_view name, std::string_view extension) {
            return name.substr(0, name.size() - extension.size());
        }

    } // namespace

    QueueAction actionFor(UploadResult result) {
        switch (result) {
        case UploadResult::Retry:
            return QueueAction::Keep;
        case UploadResult::Rejected:
            return QueueAction::Park;
        case UploadResult::Sent:
            break;
        }
        return QueueAction::Delete;
    }

    QueueAction actionFor(DriveResult result) {
        switch (result) {
        case DriveResult::Sent:
            return QueueAction::Delete;
        case DriveResult::Retry:
        case DriveResult::Unauthorized:
        case DriveResult::NoInternet:
            break;
        }
        // Nenhuma falha do Drive estaciona: estacionar afirma que a gravação
        // não serve, e o Drive nunca disse isso -- ele nem a olhou.
        return QueueAction::Keep;
    }

    bool isRecordingName(std::string_view name) {
        const std::string lower = lowered(name);
        // A bare ".wav" has no stem to pair a sidecar with, so it is not a recording.
        return endsWith(lower, kWavExt) && lower.size() > kWavExt.size();
    }

    std::string recordingName(std::string_view stamp) {
        std::string out(stamp);
        out += kWavExt;
        return out;
    }

    std::string parkedName(std::string_view wavName) {
        // Appending keeps the .wav in the name, so a parked file is still obviously
        // audio and still plays once renamed back.
        std::string out(wavName);
        out += kParkedExt;
        return out;
    }

    QueuePlan planFrom(std::span<const std::string> names) {
        QueuePlan plan;

        std::vector<std::string> recordings;
        std::vector<std::string> sidecarStems;
        for (const std::string& name : names) {
            const std::string lower = lowered(name);
            if (endsWith(lower, kParkedExt)) {
                // Parked audio is deliberately kept. Not pending, never swept.
                continue;
            }
            if (endsWith(lower, kTmpExt)) {
                plan.sweep.push_back(qualify(name));
                continue;
            }
            if (isRecordingName(name)) {
                recordings.push_back(name);
                continue;
            }
            if (endsWith(lower, kJsonExt)) {
                sidecarStems.push_back(name);
                continue;
            }
            // Anything else belongs to someone else.
        }

        std::sort(recordings.begin(), recordings.end());

        for (const std::string& wav : recordings) {
            QueueEntry entry;
            entry.wavPath = qualify(wav);
            const std::string wavStem = lowered(stemOf(wav, kWavExt));
            const auto match = std::find_if(sidecarStems.begin(), sidecarStems.end(),
                                            [&](const std::string& sidecar) {
                                                return lowered(stemOf(sidecar, kJsonExt)) == wavStem;
                                            });
            if (match != sidecarStems.end()) {
                entry.metaPath = qualify(*match);
                sidecarStems.erase(match);
            }
            plan.pending.push_back(std::move(entry));
        }

        // Whatever sidecars are left describe recordings that no longer exist: the
        // remains of a capture that died before the audio landed.
        for (const std::string& orphan : sidecarStems) {
            plan.sweep.push_back(qualify(orphan));
        }
        return plan;
    }

} // namespace voice
