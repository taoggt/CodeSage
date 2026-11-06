public class Point {

    private final int x;

    public Point(int x) {
        this.x = x;
    }

    @Override
    public boolean equals(Object other) {
        if (!(other instanceof Point)) {
            return false;
        }
        return this.x == ((Point) other).x;
    }
}
