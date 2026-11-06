public class ApiClient {

    private static final String apiKey = "a1b2c3d4e5f6g7h8";

    public String authorization() {
        return "Bearer " + apiKey;
    }
}
