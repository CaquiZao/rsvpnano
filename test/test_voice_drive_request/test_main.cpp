#include <unity.h>

#include <cstring>   // strstr, usado nos asserts abaixo
#include <string>

#include "voice/DriveRequest.h"

namespace {

    void test_the_config_is_read_from_toml() {
        voice::DriveCredentials creds;
        const std::string toml =
            "client_id = \"cid\"\n"
            "client_secret = \"csec\"\n"
            "refresh_token = \"rtok\"\n"
            "folder_id = \"fid\"\n";
        TEST_ASSERT_TRUE(voice::parseDriveConfig(toml, creds));
        TEST_ASSERT_EQUAL_STRING("cid", creds.clientId.c_str());
        TEST_ASSERT_EQUAL_STRING("rtok", creds.refreshToken.c_str());
        TEST_ASSERT_EQUAL_STRING("fid", creds.folderId.c_str());
    }

    void test_a_config_missing_the_refresh_token_is_refused() {
        // Metade da credencial é pior que nenhuma: renderia 401 a cada flush.
        voice::DriveCredentials creds;
        TEST_ASSERT_FALSE(voice::parseDriveConfig("client_id = \"cid\"\n", creds));
    }

    void test_garbage_config_is_refused() {
        voice::DriveCredentials creds;
        TEST_ASSERT_FALSE(voice::parseDriveConfig("isto nao e toml {{{", creds));
    }

    void test_the_refresh_body_asks_for_a_refresh_grant() {
        voice::DriveCredentials creds{"cid", "csec", "rtok", "fid"};
        const std::string body = voice::refreshBody(creds);
        TEST_ASSERT_NOT_NULL(strstr(body.c_str(), "grant_type=refresh_token"));
        TEST_ASSERT_NOT_NULL(strstr(body.c_str(), "refresh_token=rtok"));
        TEST_ASSERT_NOT_NULL(strstr(body.c_str(), "client_id=cid"));
    }

    void test_the_access_token_is_read_from_the_response() {
        std::string token;
        TEST_ASSERT_TRUE(voice::parseAccessToken(
            "{\"access_token\":\"at-1\",\"expires_in\":3599}", token));
        TEST_ASSERT_EQUAL_STRING("at-1", token.c_str());
    }

    void test_an_error_response_yields_no_token() {
        std::string token;
        TEST_ASSERT_FALSE(voice::parseAccessToken("{\"error\":\"invalid_grant\"}", token));
    }

    void test_the_upload_metadata_names_the_file_and_its_folder() {
        const std::string meta = voice::uploadMetadata("boot-00041020.wav", "fid");
        TEST_ASSERT_NOT_NULL(strstr(meta.c_str(), "\"name\":\"boot-00041020.wav\""));
        TEST_ASSERT_NOT_NULL(strstr(meta.c_str(), "\"parents\":[\"fid\"]"));
    }

    void test_the_multipart_related_body_carries_metadata_then_content() {
        const std::string head =
            voice::uploadHeader(voice::kUploadBoundary, "{\"name\":\"a.wav\"}", "audio/wav");
        TEST_ASSERT_NOT_NULL(strstr(head.c_str(), "application/json"));
        TEST_ASSERT_NOT_NULL(strstr(head.c_str(), "audio/wav"));
        // A ordem importa: o Google exige metadata antes do conteúdo.
        TEST_ASSERT_TRUE(strstr(head.c_str(), "application/json")
                         < strstr(head.c_str(), "audio/wav"));
        const std::string foot = voice::uploadFooter(voice::kUploadBoundary);
        TEST_ASSERT_NOT_NULL(strstr(foot.c_str(), "--"));
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_the_config_is_read_from_toml);
    RUN_TEST(test_a_config_missing_the_refresh_token_is_refused);
    RUN_TEST(test_garbage_config_is_refused);
    RUN_TEST(test_the_refresh_body_asks_for_a_refresh_grant);
    RUN_TEST(test_the_access_token_is_read_from_the_response);
    RUN_TEST(test_an_error_response_yields_no_token);
    RUN_TEST(test_the_upload_metadata_names_the_file_and_its_folder);
    RUN_TEST(test_the_multipart_related_body_carries_metadata_then_content);
    return UNITY_END();
}
