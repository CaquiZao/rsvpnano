#pragma once

#include <cstddef>
#include <string>
#include <string_view>

namespace voice {

    // Long enough for the paragraph that prompted the thought, short enough that the
    // sidecar stays small and the LLM prompt stays cheap.
    constexpr size_t kExcerptMaxChars = 400;

    // "/books/epdf.pub_sapiens.rsvp" -> "epdf.pub_sapiens". The bridge turns this into
    // the wikilink to the book note in the vault.
    std::string bookSlug(std::string_view sourcePath);

    // Cuts on a word boundary and never inside a UTF-8 sequence. A passage that ends
    // mid-word reads like a bug; one that ends mid-codepoint corrupts the note.
    std::string clampExcerpt(std::string_view paragraph, size_t maxChars = kExcerptMaxChars);

} // namespace voice
