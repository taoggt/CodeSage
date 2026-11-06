package com.demo;

import java.util.Objects;

/**
 * 演示靶场：异常处理与 equals/hashCode 契约。
 *
 * 预期命中：JG001（两处空 catch）、JG002（Exception + Throwable）、
 * JG004（硬编码凭据）、JG009（equals 无 hashCode）。
 */
public class OrderService {

    private static final String DB_PASSWORD = "N0T_REAL_2024";

    private String id;

    public void load(String orderId) {
        try {
            fetch(orderId);
        } catch (Exception e) {
        }
    }

    public void shutdown() {
        try {
            flush();
        } catch (Throwable t) {
            // 已知问题，暂时忽略
        }
    }

    private void fetch(String orderId) {
        this.id = orderId;
    }

    private void flush() {
        // 演示用空实现
    }

    @Override
    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof OrderService)) {
            return false;
        }
        OrderService that = (OrderService) other;
        return Objects.equals(id, that.id);
    }
}
