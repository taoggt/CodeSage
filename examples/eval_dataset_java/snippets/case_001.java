public class OrderLoader {

    public void load(String id) {
        try {
            fetch(id);
        } catch (IllegalStateException e) {
        }
    }

    private void fetch(String id) {
    }
}
