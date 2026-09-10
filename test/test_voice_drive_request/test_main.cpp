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

    void test_the_refresh_body_percent_encodes_special_characters() {
        // Base64-derived secrets and refresh tokens can contain '+', '/', and '=';
        // a compliant x-www-form-urlencoded decoder reads an unescaped '+' as a
        // space, corrupting the value in transit.
        voice::DriveCredentials creds{"cid", "c+s/e=c", "rtok", "fid"};
        const std::string body = voice::refreshBody(creds);
        TEST_ASSERT_NOT_NULL(strstr(body.c_str(), "client_secret=c%2Bs%2Fe%3Dc"));
    }

    void test_parsing_an_incomplete_config_does_not_reuse_a_prior_success() {
        // Glaze's TOML reader only writes fields present in the input; it never
        // clears the destination. A field left from an earlier successful parse
        // must not survive into a later, incomplete parse of the same instance.
        voice::DriveCredentials creds;
        const std::string complete =
            "client_id = \"cid\"\n"
            "client_secret = \"csec\"\n"
            "refresh_token = \"rtok\"\n"
            "folder_id = \"fid\"\n";
        TEST_ASSERT_TRUE(voice::parseDriveConfig(complete, creds));
        const std::string missingRefreshToken =
            "client_id = \"cid\"\n"
            "client_secret = \"csec\"\n"
            "folder_id = \"fid\"\n";
        TEST_ASSERT_FALSE(voice::parseDriveConfig(missingRefreshToken, creds));
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

    void test_a_confirmed_upload_is_sent() {
        TEST_ASSERT_EQUAL_INT(static_cast<int>(voice::DriveResult::Sent),
                              static_cast<int>(voice::driveResultForStatus(200)));
        TEST_ASSERT_EQUAL_INT(static_cast<int>(voice::DriveResult::Sent),
                              static_cast<int>(voice::driveResultForStatus(201)));
    }

    void test_a_wrong_or_deleted_folder_is_not_a_network_problem() {
        // 404 é o folder_id errado ou apagado, e 400 é a requisição que o
        // Drive nunca vai aceitar como está. Os dois caíam no Retry
        // genérico, que a tela traduzia para "sem internet" -- mandando o
        // usuário olhar o roteador por um erro de configuração. É essa
        // classe de mentira que este recurso existe para eliminar.
        TEST_ASSERT_EQUAL_INT(static_cast<int>(voice::DriveResult::Unauthorized),
                              static_cast<int>(voice::driveResultForStatus(400)));
        TEST_ASSERT_EQUAL_INT(static_cast<int>(voice::DriveResult::Unauthorized),
                              static_cast<int>(voice::driveResultForStatus(404)));
    }

    void test_a_token_refusal_is_unauthorized() {
        TEST_ASSERT_EQUAL_INT(static_cast<int>(voice::DriveResult::Unauthorized),
                              static_cast<int>(voice::driveResultForStatus(401)));
        TEST_ASSERT_EQUAL_INT(static_cast<int>(voice::DriveResult::Unauthorized),
                              static_cast<int>(voice::driveResultForStatus(403)));
    }

    void test_a_transient_answer_is_retried() {
        TEST_ASSERT_EQUAL_INT(static_cast<int>(voice::DriveResult::Retry),
                              static_cast<int>(voice::driveResultForStatus(429)));
        TEST_ASSERT_EQUAL_INT(static_cast<int>(voice::DriveResult::Retry),
                              static_cast<int>(voice::driveResultForStatus(500)));
        // -1 é o que readStatusCode devolve quando não conseguiu ler a linha
        // de status: nada foi dito sobre a nota.
        TEST_ASSERT_EQUAL_INT(static_cast<int>(voice::DriveResult::Retry),
                              static_cast<int>(voice::driveResultForStatus(-1)));
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_the_config_is_read_from_toml);
    RUN_TEST(test_a_config_missing_the_refresh_token_is_refused);
    RUN_TEST(test_garbage_config_is_refused);
    RUN_TEST(test_the_refresh_body_asks_for_a_refresh_grant);
    RUN_TEST(test_the_refresh_body_percent_encodes_special_characters);
    RUN_TEST(test_parsing_an_incomplete_config_does_not_reuse_a_prior_success);
    RUN_TEST(test_the_access_token_is_read_from_the_response);
    RUN_TEST(test_an_error_response_yields_no_token);
    RUN_TEST(test_the_upload_metadata_names_the_file_and_its_folder);
    RUN_TEST(test_the_multipart_related_body_carries_metadata_then_content);
    RUN_TEST(test_a_confirmed_upload_is_sent);
    RUN_TEST(test_a_wrong_or_deleted_folder_is_not_a_network_problem);
    RUN_TEST(test_a_token_refusal_is_unauthorized);
    RUN_TEST(test_a_transient_answer_is_retried);
    return UNITY_END();
}
