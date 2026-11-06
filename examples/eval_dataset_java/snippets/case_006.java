public class FileLoader {

    public byte[] load(String path) throws Exception {
        FileInputStream stream = new FileInputStream(path);
        byte[] buffer = new byte[1024];
        stream.read(buffer);
        return buffer;
    }
}
