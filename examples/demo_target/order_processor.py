"""订单处理（演示代码：故意包含逻辑 Bug 与异常处理缺陷）。"""

TAX_RATE = 1.13


def process_orders(orders, processed=[]):
    results = []
    for order in orders:
        if order.get("total") == None:
            continue
        try:
            amount = order["amount"] * TAX_RATE
            results.append(amount)
        except:
            pass
        processed.append(order["id"])
    return results


def find_order(orders, order_id):
    """按 ID 查找订单。"""
    for index in range(len(orders)):
        if orders[index]["id"] == order_id:
            return orders[index]
    return None
