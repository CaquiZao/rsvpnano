#include "voice/VoiceQueue.h"

#include <esp_log.h>

#include <string>

namespace voice::queue {

    namespace {

        constexpr char kTag[] = "voice";

        std::vector<std::string> listNames(fs::FS& fs) {
            std::vector<std::string> names;
            File dir = fs.open(kQueueDir);
            if (!dir || !dir.isDirectory()) {
                return names;
            }
            for (File child = dir.openNextFile(); child; child = dir.openNextFile()) {
                if (!child.isDirectory()) {
                    const char* raw = child.name();
                    if (raw != nullptr) {
                        // Some SD implementations hand back a full path, others a bare
                        // name. planFrom() qualifies the names itself, so strip either.
                        std::string name(raw);
                        const size_t slash = name.find_last_of('/');
                        names.push_back(slash == std::string::npos ? name : name.substr(slash + 1));
                    }
                }
                child.close();
            }
            dir.close();
            return names;
        }

    } // namespace

    bool ensureDir(fs::FS& fs) {
        if (fs.exists(kQueueDir)) {
            return true;
        }
        if (fs.mkdir(kQueueDir)) {
            return true;
        }
        ESP_LOGW(kTag, "could not create %s", kQueueDir);
        return false;
    }

    std::vector<QueueEntry> pending(fs::FS& fs) {
        const auto plan = planFrom(listNames(fs));
        for (const std::string& stale : plan.sweep) {
            fs.remove(stale.c_str());
        }
        return plan.pending;
    }

    size_t pendingCount(fs::FS& fs) {
        return pending(fs).size();
    }

    bool writeSidecar(fs::FS& fs, const char* wavPath, const NoteMeta& meta) {
        const std::string finalPath = sidecarPath(wavPath);
        const std::string tempPath = finalPath + ".tmp";
        const std::string json = toJson(meta);

        File file = fs.open(tempPath.c_str(), FILE_WRITE);
        if (!file) {
            ESP_LOGW(kTag, "could not open %s", tempPath.c_str());
            return false;
        }
        const size_t written = file.write(reinterpret_cast<const uint8_t*>(json.data()), json.size());
        file.close();
        if (written != json.size()) {
            fs.remove(tempPath.c_str());
            ESP_LOGW(kTag, "short write on %s", tempPath.c_str());
            return false;
        }

        fs.remove(finalPath.c_str());
        if (!fs.rename(tempPath.c_str(), finalPath.c_str())) {
            fs.remove(tempPath.c_str());
            ESP_LOGW(kTag, "could not rename %s", tempPath.c_str());
            return false;
        }
        return true;
    }

    bool markSent(fs::FS& fs, const QueueEntry& entry) {
        // The sidecar goes first: a sidecar with no audio is swept next pass, whereas
        // audio with no sidecar would be uploaded a second time without its anchor.
        if (!entry.metaPath.empty()) {
            fs.remove(entry.metaPath.c_str());
        }
        return fs.remove(entry.wavPath.c_str());
    }

    bool markRejected(fs::FS& fs, const QueueEntry& entry) {
        // The audio goes first here, the opposite of markSent(): what must not be lost
        // is the recording, and parking it first means a failed second rename costs the
        // sidecar rather than the note. Both parked names end in .parked, which
        // planFrom() skips outright, so neither returns as pending nor gets swept.
        if (!fs.rename(entry.wavPath.c_str(), parkedName(entry.wavPath).c_str())) {
            ESP_LOGW(kTag, "could not park refused %s", entry.wavPath.c_str());
            return false;
        }
        if (!entry.metaPath.empty()) {
            // Kept, not dropped: when the bridge refuses a note over its sidecar, the
            // sidecar is the only description of what it refused.
            fs.rename(entry.metaPath.c_str(), parkedName(entry.metaPath).c_str());
        }
        ESP_LOGW(kTag, "bridge refused %s; parked rather than deleted", entry.wavPath.c_str());
        return true;
    }

    bool markFailed(fs::FS& fs, const QueueEntry& entry, uint8_t attempts) {
        if (attempts < kMaxUploadAttempts) {
            return true; // Stays in the queue for the next flush.
        }

        const std::string parked = parkedName(entry.wavPath);
        if (!entry.metaPath.empty()) {
            fs.remove(entry.metaPath.c_str());
        }
        if (!fs.rename(entry.wavPath.c_str(), parked.c_str())) {
            ESP_LOGW(kTag, "could not park %s", entry.wavPath.c_str());
            return false;
        }
        ESP_LOGW(kTag, "parked %s after %u attempts", entry.wavPath.c_str(), static_cast<unsigned>(attempts));
        return true;
    }

} // namespace voice::queue
