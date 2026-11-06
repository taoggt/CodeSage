package com.demo;

import java.text.SimpleDateFormat;

/**
 * 演示靶场：性能与线程安全。
 *
 * 预期命中：JG008（static SimpleDateFormat）、JG007（循环内 +=）、
 * JGCC（classify 的圈复杂度 ≥ 10）。
 */
public class ReportBuilder {

    private static final SimpleDateFormat FORMAT = new SimpleDateFormat("yyyy-MM-dd");

    public String format(long timestamp) {
        return FORMAT.format(timestamp);
    }

    public String join(String[] rows) {
        String result = "";
        for (String row : rows) {
            result += row;
        }
        return result;
    }

    public String classify(int score) {
        if (score > 90) {
            return "A";
        } else if (score > 80) {
            return "B";
        } else if (score > 70) {
            return "C";
        } else if (score > 60) {
            return "D";
        } else if (score > 50) {
            return "E";
        } else if (score > 40) {
            return "F";
        } else if (score > 30) {
            return "G";
        } else if (score > 20) {
            return "H";
        } else if (score > 10) {
            return "I";
        }
        return "J";
    }
}
