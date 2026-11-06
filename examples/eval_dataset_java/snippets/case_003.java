public class ImageTool {

    public void convert(String name) throws Exception {
        String command = "convert " + name;
        Runtime.getRuntime().exec(command);
    }
}
