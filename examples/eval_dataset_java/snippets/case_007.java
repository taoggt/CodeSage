public class DatePrinter {

    private static final SimpleDateFormat FORMAT = new SimpleDateFormat("yyyy-MM-dd");

    public String print(long millis) {
        return FORMAT.format(millis);
    }
}
