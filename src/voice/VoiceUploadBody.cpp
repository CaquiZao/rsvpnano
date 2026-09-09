#include "voice/VoiceUploadBody.h"

namespace voice {

    std::string multipartHeader(std::string_view boundary, std::string_view filename,
                                std::string_view metaJson) {
        std::string out;
        out.reserve(metaJson.size() + filename.size() + 256);
        out += "--";
        out += boundary;
        out += "\r\nContent-Disposition: form-data; name=\"meta\"\r\n";
        out += "Content-Type: application/json\r\n\r\n";
        out += metaJson;
        out += "\r\n--";
        out += boundary;
        out += "\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"";
        out += filename;
        out += "\"\r\nContent-Type: audio/wav\r\n\r\n";
        return out;
    }

    std::string multipartFooter(std::string_view boundary) {
        std::string out = "\r\n--";
        out += boundary;
        out += "--\r\n";
        return out;
    }

    size_t multipartLength(std::string_view boundary, std::string_view filename, std::string_view metaJson,
                           size_t audioBytes) {
        return multipartHeader(boundary, filename, metaJson).size() + audioBytes
            + multipartFooter(boundary).size();
    }

} // namespace voice
