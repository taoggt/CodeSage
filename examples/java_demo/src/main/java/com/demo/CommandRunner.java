package com.demo;

import java.io.IOException;

/**
 * 演示靶场：命令执行面。
 *
 * 预期命中：JG003 三次——动态 exec（MAJOR）、字面量 exec（INFO）、
 * ProcessBuilder 动态参数（MAJOR）。
 */
public class CommandRunner {

    public void run(String userInput) throws IOException {
        Runtime.getRuntime().exec("sh -c " + userInput);
    }

    public void runFixed() throws IOException {
        Runtime.getRuntime().exec("ls -l");
    }

    public Process start(String[] args) throws IOException {
        return new ProcessBuilder(args).start();
    }
}
