public class UserQuery {

    private final Statement statement;

    public UserQuery(Statement statement) {
        this.statement = statement;
    }

    public ResultSet findBy(String id) throws SQLException {
        return statement.executeQuery("SELECT name FROM users WHERE id = " + id);
    }
}
