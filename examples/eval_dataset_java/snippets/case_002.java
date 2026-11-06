public class JobRunner {

    public void run() {
        try {
            execute();
        } catch (Throwable t) {
            record(t);
        }
    }

    private void execute() {
    }

    private void record(Throwable t) {
    }
}
