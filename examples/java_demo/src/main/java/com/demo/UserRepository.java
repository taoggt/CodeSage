package com.demo;

import java.io.FileInputStream;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

/**
 * 演示靶场：SQL 注入与资源泄漏。
 *
 * 预期命中：JG005 两次（直接拼接 + 先赋给变量再执行）、JG006 一次（FileInputStream）。
 */
public class UserRepository {

    private Connection connection;

    public ResultSet find(String userId) throws SQLException {
        Statement statement = connection.createStatement();
        return statement.executeQuery("SELECT * FROM users WHERE id = " + userId);
    }

    public ResultSet findIndirect(String userId) throws SQLException {
        Statement statement = connection.createStatement();
        String sql = "SELECT name FROM users WHERE id = " + userId;
        return statement.executeQuery(sql);
    }

    public byte[] readAll(String path) throws Exception {
        FileInputStream stream = new FileInputStream(path);
        byte[] buffer = new byte[1024];
        stream.read(buffer);
        return buffer;
    }
}
